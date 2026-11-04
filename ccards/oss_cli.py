#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oss_cli.py — cc-team 引擎一键面（REQ-OSS-ENGINE-1）。

控制台入口 `cc`（pyproject: cc = "ccards.oss_cli:main"；无安装环境时
`python -I ccards/oss_cli.py <子命令>` 等价）。**裸 `cc`（无子命令）=交互向导**
（REQ-CC-WIZARD-1：提需求→方案书→确认→批量立卡→全程播报；ccards.wizard）。
四个子命令：

  init     --dir <仓根>   生成运行时目录骨架 + config.toml 默认模板 +
                           系统定时模板（Windows 计划任务 / cron 两份）+
                           cards_dag.py 直达启动器；检测 Claude Code（终端
                           agent），缺失时打印官方安装命令，--install-agent
                           才显式执行安装（绝不静默下载执行）；跑完自检 ALL_PASS。
  selftest [--engine-home] 跑引擎自带红绿锁套件（tests/all.sh，需源码树）。
  export   --out <目录>    按发布白名单复制干净发行包（等价一次干净 clone；
                           现账 state/ logs/ claims/ briefs/ registry 实账与
                           运营 docs/ 一律不出包）。
  demo     [--break-at]    一次性临时沙箱（临时 git 底仓+临时账根）跑通并自毁
                           立卡→泳道并行→机器验收 全链；不碰任何真实账、不联网；
                           末行 DEMO_PASS，任一环节失败 DEMO_FAIL: <环节名>。

白名单 RELEASE_DIRS / RELEASE_FILES 是本包边界的**唯一权威**，
tests/oss-release-check.py 的 B 判据直接引用本常量对账——改边界必须同批改这里。
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

# 公仓 CI 第四圈同因预防：oss-release-check 以 `python -I ccards/oss_cli.py selftest/export`
# 起子程（B 步在干净克隆内跑），-I 不读 PYTHONIOENCODING，中文宣 print 即 charmap 崩。
# 出口钉 utf-8=ctrld/dispatch/oss-release-check 同款（MECH-GBKDECODE-BOMB-1 推广面）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# bin=起会话核 run-cc.ps1（BUG-RUNNER-GONE-1 后为引擎命脉；缺它干净克隆车队起不来，
# 且 dispatch selftest T11 的「正本在仓」断言必红——2026-11-04 CI 首绿前补账）
RELEASE_DIRS = ("ccards", "ops", "tests", "deploy", ".github", "bin")
RELEASE_FILES = ("cards_dag.py", "README.md", "pyproject.toml", "LICENSE",
                 "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", ".gitattributes",
                 # demo.gif=README 首屏动画资产（2026-11-04 README-UX 轮补：export 根文件按清单点名，
                 # 漏收=公仓死图；readme-story-test T7 差分锁防再犯——引用资产必须同时在导出面）
                 "demo.gif")

# Claude Code 官方安装面（2026-11-03 实查 code.claude.com/docs/en/setup）：
# 原生安装免 Node、自带更新；npm 通道需 Node 22+，全平台兜底。
CC_INSTALL_PS = "irm https://claude.ai/install.ps1 | iex"          # Windows PowerShell
CC_INSTALL_SH = "curl -fsSL https://claude.ai/install.sh | bash"   # Linux/macOS/WSL
CC_INSTALL_NPM = "npm install -g @anthropic-ai/claude-code"        # 全平台（Node 22+）

CONFIG_TEMPLATE = '''# cc-team 引擎配置（文件层）。留空即用内置默认；内置默认=出厂行为（全部由仓位置/宿主派生）。
# 优先级：环境变量 > 本文件 > 内置默认。发现顺序：env CC_CONFIG > 账本根下 config.toml。
# 全部键都是可选项；未识别的键只告警、不报错（旧引擎吃新配置不炸）。

[host]
# fleet_root = "/abs/path"       # 环境变量 CC_HOME；缺省=账本根上两级
# user_home = "/abs/path"        # 环境变量 CC_USER；缺省=从仓路径反推宿主主目录
# sessions_dir = "/abs/path"     # 环境变量 CC_PROJ；缺省=<user_home>/.claude/projects
# win_root = "/mnt/x"            # 环境变量 CC_WIN_ROOT；缺省=探测（Windows 盘挂载根）
# py310_dir = "..."              # 环境变量 CC_PY310_DIR
# py312_exe = "..."              # 环境变量 CC_PY312_EXE

[fleet]
# code_root = "/abs/path"        # 环境变量 CC_CODE / DISPATCH_CODE_ROOT；wf-<道> 工作树所在根

[engine]
# runner_cmd = "..."             # 环境变量 CC_RUNNER；起终端 agent 会话的包装命令
# powershell = "..."             # 环境变量 DISPATCH_PS
# verify_timeout_s = 600         # 环境变量 CARD_VERIFY_TIMEOUT

[dispatch]
# lane_cap = 6                   # 环境变量 DISPATCH_CAP；同时在飞泳道上限
# heartbeat_path = "..."         # 环境变量 DISPATCH_HB
# bind_wait_s = 15               # 环境变量 DISPATCH_BIND_WAIT
# dead_grace_s = 60              # 环境变量 DISPATCH_DEAD_GRACE_S
# redauto_every = 10             # 环境变量 DISPATCH_REDAUTO_EVERY

[thresholds]
# beat_stale_min = 15            # 环境变量 CARD_STALE_MIN；心跳过期判死（分钟）
# silence_release_min = 45       # 环境变量 CARD_SILENCE_RELEASE_MIN；断流探针阈值（分钟）

[pool]
# done_sweep_hours = 2.0         # 环境变量 CARD_DONE_SWEEP_HOURS
# done_sweep_fails = 2           # 环境变量 CARD_DONE_SWEEP_FAILS

[tasks]
# ctrld = "cc-ctrld"             # 环境变量 CTRLD_TASK_NAME（Windows 计划任务名）
# boot = "cc-boot-bringup"       # 环境变量 BOOT_TASK_NAME
# fuse = "cc-fuse"               # 环境变量 FUSE_TASK_NAME

[platform]
# api_base = "http://127.0.0.1:8080"  # 环境变量 NIGHT_LEARN_BASE；外接平台 API 入口（夜环上报类）；
#                                      内置默认=本机形态，真实部署地址属环境定制项、只写本机配置文件，不入发布包

# rings.<环名> = 秒数 —— 控制进程时间轮各环周期；环境变量锚 CTRLD_<环名>_SEC 同效。
# [rings]
# patrol = 60
# dispatch = 120
'''

WINDOWS_TEMPLATE = '''# Windows 计划任务模板（唯一 OS 触点=开机启动器；其余周期全部在引擎进程内时间轮）
# 设计纪律：不要给每个环建一条计划任务——常驻控制进程一个任务即可，周期改 config.toml [rings]。

# 1) 控制进程常驻（登录后拉起；周期环全在进程内，不要再加别的计划任务）
schtasks /Create /F /TN "cc-ctrld" /SC ONLOGON /RL LIMITED ^
  /TR "cmd /c start /min \"\" python -I \"<仓根>\\cc-team\\ops\\ctrld.py\" --live"

# 2) 先影子后正式（推荐）：把 --live 换成 --shadow=只报不动账（派发 dry、回收不带 --apply），
#    观察 logs/ 一个周期无异常再切正式。任务名可改，改处与本模板同改 config.toml [tasks]。
'''

CRON_TEMPLATE = '''# cron 备选模板（非 Windows 宿主；同样「一个常驻 + 时间轮」，别一环一条 cron）
# 控制进程常驻：@reboot 拉起，环周期在 config.toml [rings]
@reboot python3 -I <仓根>/cc-team/ops/ctrld.py --live >> <仓根>/cc-team/logs/ctrld-boot.log 2>&1
'''

LAUNCHER = '''#!/usr/bin/env python3
# cards_dag 直达启动器（cc init 生成）：账本根=本文件所在 cc-team；已设 CC_TEAM_HOME 则随环境。
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # 就近优先（cc export 产物自带 ccards/）；否则回落 site-packages 安装
os.environ.setdefault("CC_TEAM_HOME", HERE)
from ccards.cli import main
if __name__ == "__main__":
    sys.exit(main())
'''


def _engine_home():
    """引擎源码根（含 ops/ tests/ 的那级 cc-team 目录）；pip 安装环境返回 None。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ccards 上级=cc-team
    return here if os.path.isdir(os.path.join(here, "ops")) else None


def _cc_status(path=None):
    """检测 Claude Code：返回 (可执行文件路径|None, 版本串|None)。只查 PATH+跑 --version，不联网。"""
    exe = shutil.which("claude", path=path)
    if not exe:
        return None, None
    ver = None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                           timeout=30, encoding="utf-8", errors="replace")
        if r.returncode == 0 and (r.stdout or "").strip():
            ver = r.stdout.strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return exe, ver


def _cc_install_cmd(platform=None, which=None):
    """按宿主给出官方安装命令（argv 形）。返回 None=本机没有任何可执行官方通道，只提示手动装。
    纯决策函数：不执行任何东西。测试注入 platform/which 做红绿锁（tests/cc_env_detect_test.py）。"""
    platform = platform or sys.platform
    which = which or shutil.which
    npm = ["npm", "install", "-g", "@anthropic-ai/claude-code"]
    if platform.startswith("win") or platform.startswith("cygwin") or platform.startswith("msys"):
        if which("powershell") or which("powershell.exe"):
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command", CC_INSTALL_PS]
        return npm if which("npm") else None
    if which("curl") and which("bash"):
        return ["bash", "-c", CC_INSTALL_SH]
    if which("wget") and which("bash"):
        return ["bash", "-c", "wget -qO- https://claude.ai/install.sh | bash"]
    return npm if which("npm") else None


def _cc_install_hints():
    """手动安装提示行（init 缺 agent 时打印；命令与 _cc_install_cmd 同源）。"""
    if sys.platform.startswith("win"):
        return ["Windows PowerShell: %s" % CC_INSTALL_PS,
                "或 npm（Node 22+）:   %s" % CC_INSTALL_NPM]
    return ["Linux/macOS/WSL: %s" % CC_INSTALL_SH,
            "或 npm（Node 22+）: %s" % CC_INSTALL_NPM]


def _spawn_install(cmdv):
    """显式安装的唯一执行口（红绿锁在此打桩，证明默认路径零执行）。"""
    return subprocess.run(cmdv)


def cmd_init(a):
    root = os.path.abspath(a.dir)
    team = os.path.join(root, "cc-team")
    created, skipped = [], []
    for sub in ("state", "logs", "claims", "briefs"):
        p = os.path.join(team, sub)
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            created.append("cc-team/%s/" % sub)
        else:
            skipped.append("cc-team/%s/" % sub)
    # registry 骨架（泳道名册文件；实账内容不覆盖）
    reg = os.path.join(team, "registry.json")
    if not os.path.exists(reg):
        with open(reg, "w", encoding="utf-8") as f:
            json.dump({"projects": []}, f, ensure_ascii=False, indent=1)
        created.append("cc-team/registry.json")
    else:
        skipped.append("cc-team/registry.json")
    # config.toml 模板（存在绝不覆盖——现账配置比模板值钱）
    cfg = os.path.join(team, "config.toml")
    if not os.path.exists(cfg) or a.force_config:
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(CONFIG_TEMPLATE)
        created.append("cc-team/config.toml")
    else:
        skipped.append("cc-team/config.toml")
    # 系统定时模板（放 cc-team/templates/，只是参考文件）
    tdir = os.path.join(team, "templates")
    os.makedirs(tdir, exist_ok=True)
    for name, body in (("windows-tasks.md", WINDOWS_TEMPLATE), ("cron.example", CRON_TEMPLATE)):
        p = os.path.join(tdir, name)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            created.append("cc-team/templates/%s" % name)
        else:
            skipped.append("cc-team/templates/%s" % name)
    # cards_dag 直达启动器
    lz = os.path.join(team, "cards_dag.py")
    if not os.path.exists(lz):
        with open(lz, "w", encoding="utf-8") as f:
            f.write(LAUNCHER)
        os.chmod(lz, 0o755)
        created.append("cc-team/cards_dag.py")
    else:
        skipped.append("cc-team/cards_dag.py（已存在，未覆盖）")

    # ---- 自检 ----
    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, ok, detail))
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))

    chk("python>=3.9", sys.version_info >= (3, 9), sys.version.split()[0])
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from ccards import config as _cfg
        snap = _cfg.config_snapshot()
        # host.win_root=None 是合法态（非 WSL 宿主探测无盘挂载）；其余键必须有值。
        missing = [k for k, v in snap.items() if k != "host.win_root" and not v]
        chk("config 单入口可导入且快照完整", len(snap) >= 13 and not missing,
            "%d 键%s" % (len(snap), (" 缺:" + ",".join(missing)) if missing else ""))
    except Exception as e:
        chk("config 单入口可导入且快照完整", False, repr(e))
    probe = os.path.join(team, "state", ".write-probe")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(probe)
        chk("state/ 可写", True)
    except OSError as e:
        chk("state/ 可写", False, str(e))
    # 账本闭环冒烟：create→claim→progress→show 全在**临时 team 子进程**里跑
    # （子进程重启解释器、CC_TEAM_HOME 现读——绝不可能写进调用方的真账）。
    tmp_team = tempfile.mkdtemp(prefix="cc-init-probe-")
    ccd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ccards 的上级（源码树=cc-team；pip=site-packages）
    code = ("import sys; sys.path.insert(0, %r); "
            "from ccards.cli import main; sys.exit(main(sys.argv[1:]))" % ccd)
    def ledger(*argv):
        env = dict(os.environ, CC_TEAM_HOME=tmp_team)
        r = subprocess.run([sys.executable, "-I", "-c", code] + list(argv),
                           env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return r.returncode
    try:
        rc1 = ledger("create", "CC-INIT-PROBE", "--title", "init 冒烟", "--verify", "true")
        rc2 = ledger("claim", "CC-INIT-PROBE", "--owner", "probe")
        rc3 = ledger("progress", "CC-INIT-PROBE", "冒烟拍", "--lane", "probe", "--epoch", "1", "--ring", "1")
        rc4 = ledger("show", "CC-INIT-PROBE")
        chk("账本闭环冒烟(create/claim/progress/show)",
            rc1 == 0 and rc2 == 0 and rc3 == 0 and rc4 == 0,
            "rc=%s/%s/%s/%s" % (rc1, rc2, rc3, rc4))
    except Exception as e:
        chk("账本闭环冒烟(create/claim/progress/show)", False, repr(e))
    finally:
        shutil.rmtree(tmp_team, ignore_errors=True)

    # ---- 终端 agent（Claude Code）环境检测：没装就明示官方安装命令；--install-agent 才代执行 ----
    # 纪律=宁红不猜：默认绝不静默下载执行；显式安装前把将要执行的完整命令打出来；
    # 装完复检找不到 = FAIL（不假绿），提示重开终端后复跑。
    exe, ver = _cc_status()
    if exe:
        chk("终端 agent Claude Code", True, "%s (%s)" % (ver or "已安装", exe))
    elif getattr(a, "install_agent", False):
        cmdv = _cc_install_cmd()
        if not cmdv:
            chk("终端 agent Claude Code", False,
                "本机没有可执行的官方安装通道（powershell/curl/npm 均缺），请手动安装")
        else:
            print("[cc] 显式执行官方安装：%s" % " ".join(cmdv))
            try:
                _spawn_install(cmdv)
            except OSError as e:
                print("[cc] 安装进程未能启动：%r" % (e,))
            exe, ver = _cc_status()
            if exe:
                chk("终端 agent Claude Code", True, "%s (%s)" % (ver or "刚安装", exe))
                print("[cc] 首次使用：终端里跑 `claude` 走浏览器 OAuth 登录（需 Pro/Max 等付费计划），"
                      "或设 ANTHROPIC_API_KEY")
            else:
                chk("终端 agent Claude Code", False,
                    "安装命令已执行但 PATH 还找不到 claude——重开终端后再跑一次 cc init 复检")
    else:
        print("[warn] 未检测到 Claude Code（终端 agent 可选项：账本引擎不依赖它；自动派单才需要）。官方安装：")
        for ln in _cc_install_hints():
            print("       " + ln)
        print("       或重跑 `cc init --install-agent` 由引擎代执行安装（执行前明示完整命令，绝不静默）")

    print("created=%s skipped=%s" % (",".join(created) or "-", ",".join(skipped) or "-"))
    if all(ok for _, ok, _ in checks):
        print("ALL_PASS")
        return 0
    print("INIT_FAIL: %s" % "; ".join(n for n, ok, _ in checks if not ok))
    return 1


def cmd_selftest(a):
    home = a.engine_home or _engine_home()
    if not home or not os.path.isfile(os.path.join(home, "tests", "all.sh")):
        print("SELFTEST_SKIP: 套件随源码分发；当前环境未找到 tests/all.sh"
              "（pip 精简安装属正常——用 --engine-home 指源码树）")
        return 2
    # 与判据宿主同纪律（MECH-VERIFYPIN-FALLBACK-1）：Windows 裸 "bash" 会命中
    # System32\bash.exe（WSL 启动器），反斜杠路径被剥成「盘符+主目录名连写」假红；
    # 复用 _resolve_verify_bash() 钉 Git Bash，POSIX 分支零漂移。
    # 取数式与本文件 init 自检同形：直跑脚本无包上下文，先补父目录再按包导入。
    if __package__ in (None, ""):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ccards.commands import _resolve_verify_bash
    r = subprocess.run([_resolve_verify_bash(), os.path.join(home, "tests", "all.sh")])
    return r.returncode


def cmd_export(a):
    home = _engine_home()
    if not home:
        print("EXPORT_FAIL: export 需在引擎源码树内运行（ccards 与 ops/tests 同级）")
        return 1
    out = os.path.abspath(a.out)
    if os.path.exists(out) and os.listdir(out) and not a.force:
        print("EXPORT_FAIL: 目标非空（--force 允许并入）: %s" % out)
        return 1
    os.makedirs(out, exist_ok=True)
    manifest, skipped = [], []
    for d in RELEASE_DIRS:
        src = os.path.join(home, d)
        if not os.path.isdir(src):
            skipped.append(d + "/(缺)")
            continue
        dst = os.path.join(out, d)
        os.makedirs(dst, exist_ok=True)
        for root, dirs, files in os.walk(src):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            rel = os.path.relpath(root, src)
            tgt = os.path.join(dst, rel) if rel != "." else dst
            os.makedirs(tgt, exist_ok=True)
            for fn in files:
                if fn.endswith((".pyc", ".pyo")):
                    continue
                shutil.copy2(os.path.join(root, fn), os.path.join(tgt, fn))
                manifest.append(os.path.join(d, rel, fn).replace(os.sep, "/").replace("/./", "/"))
    for fn in RELEASE_FILES:
        src = os.path.join(home, fn)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(out, fn))
            manifest.append(fn)
        else:
            skipped.append(fn + "(缺)")
    with open(os.path.join(out, "RELEASE-MANIFEST.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(manifest)) + "\n")
    print("EXPORT_OK out=%s files=%d skipped=%s" % (out, len(manifest), ",".join(skipped) or "-"))
    return 0


# ── cc demo（REQ-CC-DEMO-1）：装完一条命令看全链 ────────────────────────────────────────
# 一次性临时沙箱（mkdtemp 每跑全新=幂等；临时 git 底仓+临时账根）真实跑通
# 立卡→泳道并行→交付合入→机器验收 全链后自毁。三条验收约束即设计约束：
#   a) 零真实账触碰：账本操作全部=CC_TEAM_HOME 钉向沙箱账根的**子进程**（与 cmd_init
#      账本闭环冒烟同一取数式）；cmd_demo 本进程全程 stdlib、不 import ccards。
#   b) 幂等可重复：沙箱 mkdtemp 唯一、成败必自毁（finally 兜底），零残留无固定路径。
#   c) 不联网：git 全本地（无 remote/clone/fetch），子程只起本地解释器；静态对账
#      =tests/cc-demo-test.py T5 扫 demo 段源码无 URL/urllib/socket 字面。
# 末行契约：成功=DEMO_PASS；任一环节失败=DEMO_FAIL: <环节名>（点名环节；
# 守卫要红得起来——tests/cc-demo-test.py T2 用 --break-at 逐环节注入验红得起来）。
DEMO_STEPS = ("建沙箱", "立卡", "泳道并行", "交付合入", "机器验收", "自毁")
_NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗根除纪律同 ccards.config（此处不引包，免真账 import）

# 演示卡：REQ 前缀=G2 类型闸 enforce 档合法族（type 必与卡号前缀一致）。
# 判据=相对路径 grep 标记文件：立卡时产物未落盘、失败输出恰点名 --files 声明的
# 该文件 → 走 REQ-VERIFY-DRYRUN-1 R2 red-first 豁免（「首红基线属预期态」真实形态）；
# 泳道交付+合入主干后 verdict 复跑同一条判据即绿——一张卡演全「判据先红后绿」。
_DEMO_CARDS = (
    ("REQ-DEMO-A", "lane-a", "demo_delivery_a.txt", "OK_DEMO_A", "演示卡A：交付+交卷（主干闸前停 DONE 属预期）"),
    ("REQ-DEMO-B", "lane-b", "demo_delivery_b.txt", "OK_DEMO_B", "演示卡B：与A并发跑全套"),
)

_DEMO_CLI_CODE = ("import sys; sys.path.insert(0, %r); "
                  "from ccards.cli import main; sys.exit(main(sys.argv[1:]))")

# 泳道子程（真实并行体：两条 lane_worker 同时在飞，账本写锁天然串行记账、执行并行）。
# 全程只动沙箱账根与自己的 worktree；任何一环与预期不符=LANE-FAIL 非零退出（demo 归因到「泳道并行」环节）。
_DEMO_LANE_SCRIPT = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lane_worker.py — cc demo 泳道子程（REQ-CC-DEMO-1 演示数据，非业务道）。
claim→beat→交付 commit→done（CC_AUTO_VERIFY=1 与生产 run-cc.ps1 注入同源）。
done 后 AUTO-VERIFY 必须**红在主干闸**（REQ-MERGE-GATE-1：交付未合 main 不许落章、
卡停 DONE）——这是生产实况不是故障；绿了反倒说明闸坏了，本道判 LANE-FAIL。"""
import contextlib
import io
import os
import subprocess
import sys

# 子程 piped stdout 默认随宿主 locale（win=cp936），父侧按 utf-8 收流中文必乱码；
# -I(含 -E) 挡掉 PYTHONIOENCODING，故进程内 reconfigure（BUG-CCARDS-UTF8-1 同款出口钉）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.environ["CC_DEMO_CCD"])
from ccards.cli import run as cli_run  # noqa: E402

LANE, CID, DELIVERY, MARKER, WT = sys.argv[1:6]


def cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            rc = cli_run(list(argv))
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue()


def git(*args):
    p = subprocess.run(["git", "-C", WT] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def fail(detail):
    print("LANE-FAIL %s: %s" % (LANE, detail))
    sys.exit(1)


rc, out = cli("claim", CID, "--owner", LANE)
if rc != 0 or "epoch=" not in out:
    fail("claim rc=%s out=%s" % (rc, out.strip()[-200:]))
epoch = out.split("epoch=")[1].split()[0].strip()

rc, out = cli("progress", CID, "开工：领牌首拍", "--lane", LANE,
              "--epoch", epoch, "--ring", "1")
if rc != 0 or "BEAT_OK" not in out:
    fail("beat1 rc=%s out=%s" % (rc, out.strip()[-200:]))

with open(os.path.join(WT, DELIVERY), "w", encoding="utf-8") as f:
    f.write("%s\\n" % MARKER)
rc, out = git("add", "-A")
if rc != 0:
    fail("git add: %s" % out.strip()[-200:])
rc, out = git("commit", "-qm", "feat(%s): demo 交付" % CID)
if rc != 0:
    fail("git commit: %s" % out.strip()[-200:])
rc, sha = git("rev-parse", "HEAD")
if rc != 0 or len(sha.strip()) < 40:
    fail("git rev-parse: %s" % sha.strip()[-200:])
sha = sha.strip()

rc, out = cli("progress", CID, "交付 commit=%s" % sha[:12], "--lane", LANE,
              "--epoch", epoch, "--ring", "2")
if rc != 0 or "BEAT_OK" not in out:
    fail("beat2 rc=%s out=%s" % (rc, out.strip()[-200:]))

os.environ["CC_AUTO_VERIFY"] = "1"
rc, out = cli("done", CID, "--owner", LANE, "--commit", sha, "--repo", WT,
              "--epoch", epoch, "--evidence", DELIVERY)
if rc != 0 or (" DONE" not in out and not out.strip().endswith("DONE")):
    fail("done rc=%s out=%s" % (rc, out.strip()[-200:]))
if "AUTO-VERIFY" not in out or "红" not in out:
    fail("done 后 AUTO-VERIFY 未按主干闸预期红: %s" % out.strip()[-200:])

print("LANE-OK %s epoch=%s commit=%s 停DONE待合入" % (LANE, epoch, sha[:12]))
'''


class _DemoFail(Exception):
    """环节失败（环节名+细节）；cmd_demo 唯一消费口=转成末行 DEMO_FAIL: <环节名>。"""

    def __init__(self, step, detail=""):
        Exception.__init__(self, "%s — %s" % (step, detail))
        self.step, self.detail = step, detail


def _demo_kill_tree(path):
    """删净沙箱：Windows 下 git 对象库文件只读（pack/loose object mode 0444），
    裸 rmtree 必留残骸——先剥只读再删（仅限本函数自家 mkdtemp 根，不伤他物）。"""
    def _onerr(_fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            _fn(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=_onerr)


def _demo_cli(ledger, ccd, argv, cwd, timeout=180):
    """账本命令子进程：CC_TEAM_HOME 必钉沙箱账根（真账物理够不着）。返回 (rc, stdout, stderr)。"""
    env = dict(os.environ, CC_TEAM_HOME=ledger)
    r = subprocess.run([sys.executable, "-I", "-c", _DEMO_CLI_CODE % ccd] + list(argv),
                       env=env, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=_NO_WIN)
    return r.returncode, r.stdout or "", r.stderr or ""


def _demo_git(gitexe, cwd, *args, timeout=120):
    """demo 侧 git 一律本地仓、显式 -C；输出合流便于失败归因（不引 ccards 免真账 import）。"""
    r = subprocess.run([gitexe, "-c", "core.quotepath=false", "-C", cwd] + list(args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=_NO_WIN)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _demo_sandbox(ctx):
    gitexe = shutil.which("git")
    if not gitexe:
        raise _DemoFail("建沙箱", "PATH 里没有 git（演示依赖本地 git 底仓；装完 git 再跑）")
    root = ctx["root"]
    repo = os.path.join(root, "repo")
    ledger = os.path.join(root, "cc-team")
    for sub in ("state", "claims", "briefs", "logs"):
        os.makedirs(os.path.join(ledger, sub), exist_ok=True)
    rc, out = _demo_git(gitexe, root, "init", "-q", "repo")
    if rc:
        raise _DemoFail("建沙箱", "git init: %s" % out.strip()[:160])
    _demo_git(gitexe, repo, "config", "user.name", "cc-demo")
    _demo_git(gitexe, repo, "config", "user.email", "demo@example.invalid")
    # 初始分支钉 main（不赌宿主 init.defaultBranch——主干闸按 main→master→trunk 找 ref）
    rc, out = _demo_git(gitexe, repo, "symbolic-ref", "HEAD", "refs/heads/main")
    if rc:
        raise _DemoFail("建沙箱", "git symbolic-ref: %s" % out.strip()[:160])
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as f:
        f.write("# cc demo 临时底仓（本地仓，跑完即焚）\n")
    rc, out = _demo_git(gitexe, repo, "add", "-A")
    if rc:
        raise _DemoFail("建沙箱", "git add: %s" % out.strip()[:160])
    rc, out = _demo_git(gitexe, repo, "commit", "-qm", "chore(demo): 底仓初始化")
    if rc:
        raise _DemoFail("建沙箱", "git commit: %s" % out.strip()[:160])
    for _cid, lane, _f, _m, _t in _DEMO_CARDS:  # 泳道=同仓 worktree，生产 wf-<道> 同形
        rc, out = _demo_git(gitexe, repo, "worktree", "add", "-q", "-b", lane,
                            os.path.join(root, "wf-" + lane), "main")
        if rc:
            raise _DemoFail("建沙箱", "git worktree(%s): %s" % (lane, out.strip()[:160]))
    _rc, ver = _demo_git(gitexe, repo, "--version")
    ctx.update(git=gitexe, repo=repo, ledger=ledger,
               ccd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print("    沙箱=%s（临时 git 底仓 main + 临时账根 + %d 条泳道 worktree）"
          % (root, len(_DEMO_CARDS)))
    print("    git=%s；底仓未配任何远端=物理不联网" % (ver or "").strip())
    return "临时底仓+临时账根+2 泳道 worktree 就位"


def _demo_create(ctx):
    for cid, lane, delivery, marker, title in _DEMO_CARDS:
        rc, out, err = _demo_cli(ctx["ledger"], ctx["ccd"], [
            "create", cid, "--title", title,
            "--verify", "grep -q %s %s" % (marker, delivery),
            "--files", delivery, "--lanes", lane, "--type", "REQ",
            # 三段式+判据三要素+type 带全=立卡三口闸 enforce 生效日到了演示照跑绿（T4 前瞻锁）
            "--src", "cc demo 内置演示数据（REQ-CC-DEMO-1，非业务账）",
            "--story", "新用户装完跑一条命令看立卡→泳道并行→机器验收全链真实运行",
            "--plan", "双泳道并发 claim/beat/commit/done，合入后 verdict 复跑判据",
            "--c-op", "grep 交付标记 " + marker,
            "--c-in", "泳道交付文件 " + delivery,
            "--c-expect", "文件在场且含标记=退出码 0",
        ], cwd=ctx["repo"])
        if rc or (cid + " OPEN") not in out:
            raise _DemoFail("立卡", "%s rc=%s｜%s" % (cid, rc, (out + err).strip()[-240:]))
        print("    %s → OPEN（判据=「grep -q %s %s」；立卡试跑 %s）" % (
            cid, marker, delivery,
            "首红 R2 豁免放行（产物未落盘=预期态）" if ("R2" in out or "red-first" in out) else "rc=0 直绿"))
    return "%d 张演示卡 OPEN（发卡即考试：判据试跑过了才立得住）" % len(_DEMO_CARDS)


def _demo_lanes(ctx):
    script = os.path.join(ctx["root"], "lane_worker.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(_DEMO_LANE_SCRIPT)
    procs = []
    for cid, lane, delivery, marker, _t in _DEMO_CARDS:
        wt = os.path.join(ctx["root"], "wf-" + lane)
        env = dict(os.environ, CC_TEAM_HOME=ctx["ledger"], CC_DEMO_CCD=ctx["ccd"],
                   CC_AUTO_VERIFY="1")
        p = subprocess.Popen([sys.executable, "-I", script, lane, cid, delivery, marker, wt],
                             env=env, cwd=wt, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=_NO_WIN)
        procs.append((lane, p))
    outs, commits = {}, {}
    for lane, p in procs:  # 两 lane 同时在飞；收流串行不影响并行语义（记账本就锁内串行）
        try:
            out, _ = p.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            for _l, q in procs:
                if q.poll() is None:
                    q.kill()
            raise _DemoFail("泳道并行", "%s 泳道子程超时(180s)" % lane)
        out = out or ""
        for ln in out.splitlines():
            if ln.strip():
                print("    [%s] %s" % (lane, ln.strip()))
        rc = p.returncode
        if rc or "LANE-OK" not in out:
            raise _DemoFail("泳道并行", "%s rc=%s｜tail=%s" % (lane, rc, out.strip()[-240:]))
        commits[lane] = out.split("commit=")[1].split()[0]
        outs[lane] = out
    ctx["commits"] = commits
    return "%s 并发领牌+交卷，done 即 AUTO-VERIFY——未合主干=主干闸红、卡停 DONE（生产实况非故障）" % \
        "+".join(sorted(commits))


def _demo_merge(ctx):
    merged = []
    for _cid, lane, _f, _m, _t in _DEMO_CARDS:
        rc, out = _demo_git(ctx["git"], ctx["repo"], "merge", "-q", "--no-ff", "-m",
                            "merge(demo): %s 合入 main" % lane, lane)
        if rc:
            raise _DemoFail("交付合入", "merge %s rc=%s｜%s" % (lane, rc, out.strip()[-200:]))
        merged.append(lane)
    return "%s 已合入 main（REQ-MERGE-GATE-1：交付上主干才许落章）" % "+".join(merged)


def _demo_accept(ctx):
    for cid, _lane, _f, _m, _t in _DEMO_CARDS:
        rc, out, err = _demo_cli(ctx["ledger"], ctx["ccd"],
                                 ["verdict", cid, "PASS", "--reason", "cc demo 机器验收：判据复跑绿"],
                                 cwd=ctx["repo"])
        if rc or (cid + " VERIFIED") not in out:
            raise _DemoFail("机器验收", "%s verdict rc=%s｜%s" % (cid, rc, (out + err).strip()[-240:]))
        rc, out, err = _demo_cli(ctx["ledger"], ctx["ccd"], ["show", cid], cwd=ctx["repo"])
        try:
            d = json.loads(out[out.index("{"):out.rindex("}") + 1])
        except (ValueError, IndexError):
            raise _DemoFail("机器验收", "%s show 输出不可解析 rc=%s" % (cid, rc))
        vr = d.get("verify_run") or {}
        if d.get("status") != "VERIFIED" or vr.get("rc") != 0 or not (d.get("commit") or ""):
            raise _DemoFail("机器验收", "%s 卡面终态不符：status=%s verify_run.rc=%s commit=%s"
                            % (cid, d.get("status"), vr.get("rc"), (d.get("commit") or "")[:12]))
        print("    %s → VERIFIED（机器复跑 rc=%s via=%s cmd_sha=%s；交付 commit=%s）" % (
            cid, vr.get("rc"), vr.get("via") or "-", (vr.get("cmd_sha") or "")[:12],
            (d.get("commit") or "")[:12]))
    return "%d 张卡 verdict PASS→VERIFIED（完成=机器复跑退出码0，不是自然语言自述）" % len(_DEMO_CARDS)


def _demo_cleanup(ctx):
    _demo_kill_tree(ctx["root"])
    if os.path.isdir(ctx["root"]):
        raise _DemoFail("自毁", "沙箱未删净: %s" % ctx["root"])
    return "沙箱已销毁，零残留（含只读 git 对象库剥权限后删净）"


_DEMO_STAGES = {
    "建沙箱": _demo_sandbox, "立卡": _demo_create, "泳道并行": _demo_lanes,
    "交付合入": _demo_merge, "机器验收": _demo_accept, "自毁": _demo_cleanup,
}


def cmd_demo(a):
    """cc demo —— 一次性沙箱全链演示（REQ-CC-DEMO-1）。末行契约见段头。"""
    root = tempfile.mkdtemp(prefix="cc-demo-")
    ctx = {"root": root}
    try:
        print("[cc demo] 一条命令看全链：立卡→泳道并行→机器验收（一次性沙箱；不碰真实账、不联网）")
        for idx, name in enumerate(DEMO_STEPS, 1):
            if a.break_at == name:
                raise _DemoFail(name, "注入失败（--break-at 差分对口）")
            try:
                detail = _DEMO_STAGES[name](ctx)
            except _DemoFail:
                raise
            except subprocess.TimeoutExpired as e:
                raise _DemoFail(name, "子进程超时 %r" % (e,))
            except Exception as e:
                raise _DemoFail(name, repr(e))
            print("[%d/%d] %s OK — %s" % (idx, len(DEMO_STEPS), name, detail))
        print("DEMO_PASS")
        return 0
    except _DemoFail as e:
        print("DEMO_FAIL: %s%s" % (e.step, (" — %s" % e.detail) if e.detail else ""))
        return 1
    finally:
        try:
            _demo_kill_tree(root)  # 成败必自毁（--break-at 注入红也零残留；已删净则静过）
        except OSError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cc", description="cc-team 引擎一键面"
                                 "（无子命令=进交互向导 REQ-CC-WIZARD-1）")
    # REQ-CC-WIZARD-1：`cc` 裸敲=产品入口向导；带子命令=原一键面行为逐字不变
    sub = ap.add_subparsers(dest="cmd", required=False)
    i = sub.add_parser("init", help="生成骨架+配置模板+定时模板+自检")
    i.add_argument("--dir", default=".", help="目标仓根（默认当前目录）")
    i.add_argument("--force-config", action="store_true", help="覆盖已有 config.toml")
    i.add_argument("--install-agent", action="store_true",
                   help="检测到缺 Claude Code 时，明示完整命令后代执行官方安装器（默认只打印指引）")
    i.set_defaults(fn=cmd_init)
    t = sub.add_parser("selftest", help="跑引擎红绿锁套件（需源码树）")
    t.add_argument("--engine-home", default=None, help="含 ops/tests 的 cc-team 根")
    t.set_defaults(fn=cmd_selftest)
    x = sub.add_parser("export", help="按发布白名单导出发行包")
    x.add_argument("--out", required=True, help="输出目录")
    x.add_argument("--force", action="store_true", help="目标非空仍导出")
    x.set_defaults(fn=cmd_export)
    d = sub.add_parser("demo", help="一次性沙箱演示全链（立卡→泳道并行→机验→合入→自毁）；"
                                    "末行 DEMO_PASS / DEMO_FAIL: <环节名>")
    d.add_argument("--break-at", dest="break_at", default=None, choices=list(DEMO_STEPS),
                   help="注入指定环节失败（差分对/彩排用；成败都自毁不残留）")
    d.set_defaults(fn=cmd_demo)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    if a.cmd is None:  # REQ-CC-WIZARD-1：裸 `cc`=交互向导（提需求→方案书→确认→开工→播报）
        from .wizard import run_wizard
        return run_wizard()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
