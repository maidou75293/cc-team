#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oss_cli.py — cc-team 引擎一键面（REQ-OSS-ENGINE-1）。

控制台入口 `cc`（pyproject: cc = "ccards.oss_cli:main"；无安装环境时
`python -I ccards/oss_cli.py <子命令>` 等价）。三个子命令：

  init     --dir <仓根>   生成运行时目录骨架 + config.toml 默认模板 +
                           系统定时模板（Windows 计划任务 / cron 两份）+
                           cards_dag.py 直达启动器；检测 Claude Code（终端
                           agent），缺失时打印官方安装命令，--install-agent
                           才显式执行安装（绝不静默下载执行）；跑完自检 ALL_PASS。
  selftest [--engine-home] 跑引擎自带红绿锁套件（tests/all.sh，需源码树）。
  export   --out <目录>    按发布白名单复制干净发行包（等价一次干净 clone；
                           现账 state/ logs/ claims/ briefs/ registry 实账与
                           运营 docs/ 一律不出包）。

白名单 RELEASE_DIRS / RELEASE_FILES 是本包边界的**唯一权威**，
tests/oss-release-check.py 的 B 判据直接引用本常量对账——改边界必须同批改这里。
"""
import argparse
import json
import os
import shutil
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
                 "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", ".gitattributes")

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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cc", description="cc-team 引擎一键面")
    sub = ap.add_subparsers(dest="cmd", required=True)
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
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
