#!/usr/bin/env python3
# cc-team/ops/ctrld.py — 单进程控制面（REQ-CTRL-PLAIN-1，业主 2026-11-02 16:1x 令
#   「都是CC在干活，为什么借助WSL——设计不合理」；定版见 docs/机制-跨平台判活定版-20261102.md 第六节）。
#
# 是什么（类型=需求，不是 BUG）：控制面三环——发单环(dispatch)/体检环(recycle full)/
#   滞留扫环(recycle sweep)——并入**一个进程一个 loop**，纯 python + 账本文件，
#   纯时间片调度：零 cron、零 WSL，任意主机（Windows/Linux/macOS 同一份 py）可跑。
#   当年绑 WSL 图的是 systemd/cron 便利，代价=进程号两册互查（11-02 误杀案根因）、
#   路径 /mnt/c 混杂、开机需人碰一次 WSL。单世界一本册后 pid 类跨册问题结构性消失；
#   与名牌制(REQ-BEAT-ANCHOR-1)互补——名牌管「跨主机心跳账」，本器管「单机不自我拆墙」，互不替代。
#
# 环节拍（schedule，env 可逐环改秒）：
#   dispatch  默认 60s（CTRLD_DISPATCH_SEC）
#   full      默认 60s（CTRLD_FULL_SEC）——卡文案写「体检每10分」是立卡时旧节拍；同日 16:3x
#             业主另定「巡逻提速 10分→1分」（TODO.md 巡逻提速节：探活 */1），*/10 的省机器
#             理由随锚探测开销降到毫秒级作废。本器取现网最新节拍为准，想回 600 用 env。
#   sweep     默认 60s（CTRLD_SWEEP_SEC）——卡文案「滞留扫每分钟」；WSL cron 侧 */2 是
#             整分对齐的错峰需要，纯时间片无此约束。
#
#   2026-11-03 业主令「定时任务不要用操作系统定时，一个常驻的 Python 进程就可以了，
#   避免依赖操作系统」：cc-fuse / cc-sv-patrol 两个 OS 周期任务已注销，收编为
#   fuse 环（默认 60s，CTRLD_FUSE_SEC）与 svcheck 环（每日 @03:30 对时，T6 锁，
#   CTRLD_SV_AT 可改点）；OS 侧仅剩 cc-ctrld 一个 AtLogon 启动器——它只负责开机
#   拉起本进程，不承载任何周期定时。svcheck 走 WSL 跑 sv.sh check：迁的是**定时器**，
#   StreamVision 应用本体仍在 WSL（本体迁 Windows=待业主拍板项，见候拍板清单）。
#
# 模式：
#   --shadow 影子=只报不动账：dispatch 走 --once --dry（dry 不 claim/不起会话/不写派发心跳，
#            既有假绿禁令），recycle 两环**不带 --apply**（recycle 默认 dry 只报，sweep 干跑
#            在 `if not apply_mode` 闸前逐张 return，绝不进 do_verdict——本卡收口前已核源码行
#            recycle.py:714）。影子只写 ctrld 自己的心跳与环日志（不是账本），WSL 现网环照跑。
#   --live   正式=三环真动手：dispatch --once（claim/起会话/心跳全真），recycle 两环 --apply。
#            切流前须影子对账一圈为绿；切流后观察两个整周期 release 与误杀数字，达标才停 WSL
#            服务（退役不删除，Linux 模板留给开源仓）。runbook 见 docs/机制-控制面通用化-20261102.md。
#
# 活性判据（红绿=boot_check TC11）：每拍续写 <team>/logs/ctrld.heartbeat（节流≥5s 一回，
#   文件首行带 mode=shadow/live）。规则：计划任务在册→心跳必在且龄<3min；未在册（过渡期）→
#   本项豁免不红——免把 BOOT-BRINGUP 的 boot_check 判据拖成他人卡片的假红；CTRLD_REQUIRED=1
#   可强制断言（红绿/上岗后常驻化用）。
#
# 环隔离（BUG-CTRLD-RINGSTALL-1，业主 2026-11-03 立卡；归因实锤见该卡证据文）：
#   原 loop_core 在主线程**同步串行**跑环——单环实测时长（reconcile ~147s×24 次/日、
#   sweep 验判据 330~513s）远超其余环 60s 节拍 ⇒ 整轮停摆，保险丝 fuse 环同堵=保命通道失明
#   （fuse.log 11:11:16→11:17:28 零行实证）。现结构：环 subprocess 起**独立线程**（Wheel.fire），
#   主线程只做排期/心跳/看板/看门；单实例语义从「环间串行天然互斥」收窄为**同脚本互斥组**
#   （recycle full/sweep 仍永不重叠，异脚本可并行）；同组到点撞忙=**记欠账 owed、组内空位
#   补放**（BUG-CTRLD-SWEEPSTARVE-1：full/sweep 同 60s 节拍同相位 :52，旧「跳拍即丢」=
#   低优先环永久 0 拍，REDAUTO 转正通道停摆）；环输出重定向**文件**非管道（Windows
#   run(capture_output,timeout) 超时 kill 后 communicate 仍等管道 EOF，起会话的孙进程持句柄
#   =超时失真的潜在放大件，一并拆）；超时 kill_tree（taskkill /T / killpg）+ 有界 wait；
#   看门兜底：超自身 timeout+宽限未归 ⇒ RING-STUCK 留痕+杀树+放行，堵环不永占环位。
#   节拍 env：CTRLD_TICK_SEC（默认 1.0，支持小数，红绿锁沙箱加速用）。
#
# 常驻化按 host 薄封装（install 子命令）：
#   windows：Register-ScheduledTask cc-ctrld（AtLogon + 失败自动重启 + 无限时限 + IgnoreNew，
#            与 REQ-BOOT-BRINGUP-1 的 cc-boot-bringup 同一机制、同一 EncodedCommand 中文口径）；
#   linux： deploy/systemd/cc-ctrld.service 模板保留（cron 兼容亦保留，WSL 现役服务停用不删除）；
#   macos： deploy/launchd/com.cc.ctrld.plist 预留（REQ-REG-HOST-1 host/launcher 接口即为此铺，
#            业主发话再做实跑）。
#
# 看板（卡面③）：--with-board 时 status-web 由本进程同组带起（纯 py 已核）；子死自动重启，
#   连续快速死亡 >3 次即弃权留痕（防空转烧日志）。注意 WSL2 localhostForwarding：WSL 侧看板
#   在跑时 Windows 侧起 :8790 会撞端口——切流顺序=先停 WSL 看板再开本开关（runbook 第3步）。
#
# 边界（卡面）：worker 启动仍走 run-cc.ps1 原样（不动它，只是 Windows 原生侧经 DISPATCH_PS/
#   DISPATCH_RUNNER 锚指向本机路径）；迁移期不停账；不动 web/src。
# 测试锚：--selftest 全注入（伪时钟/伪执行器/伪看板子进程），不碰真账、不起真进程、
#   心跳写临时根；--fault 供红绿锁「守卫红得起来」自证。
import argparse, json, os, shutil, signal, subprocess, sys, threading, time, traceback
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

HERE = os.path.dirname(os.path.abspath(__file__))          # cc-team/ops（本树）
ROOT = os.path.dirname(HERE)                                # cc-team 根（本树）
sys.path.insert(0, ROOT)
from ccards import config as _cfg  # REQ-OSS-ENGINE-1：配置单入口（环周期/任务名/宿主派生锚，env>toml>默认）


def team_home():
    """台账家（正账）：CC_TEAM_HOME 优先，缺省=本脚本所在树——与 ccards.config 同一口径。"""
    return os.environ.get("CC_TEAM_HOME") or ROOT


def env_num(name, default):
    # CTRLD_<X>_SEC → 配置键 rings.<x>：env 锚 > config.toml > 内置默认（默认=现网节拍）。
    key = "rings." + name.lower().removeprefix("ctrlld_").removesuffix("_sec")
    return _cfg.num_setting(key, name, default, minimum=1)


def _task_path_sim():
    """任务形 PATH 仿真串（T5b/T5c 专用：只含注册表级系统目录+本机 Python/Git 安装位，
    故意无 node——child_env 的补 node 段必须把它加回来，撤补即红）。全派生零宿主字面量
    （REQ-OSS-ENGINE-1）：SystemRoot / config.CC_PY310_DIR / ProgramFiles。"""
    sr = os.environ.get("SystemRoot") or r"C:\Windows"
    git = os.path.join(os.environ.get("ProgramFiles") or r"C:\Program Files", "Git", "cmd")
    return ";".join([os.path.join(sr, "system32"), sr, os.path.join(sr, "System32", "Wbem"),
                     _cfg.CC_PY310_DIR, git])


def child_env():
    """三环子进程环境：把 WSL 风格缺省路径按当前 host 推导钉好（两侧同盘同文件，值等价）。
    路径类全部 setdefault——外部已显式给的（沙箱/自测）优先；**出口编码类例外=硬钉**
    （BUG-TRAIN-HOSTPIN-1②：setdefault 会放行继承的 gbk/cp936 毒值=赌 locale——12:22:49
    队列复验中文 fail_detail 乱码落 events.log 不可逆实锤后改口；火车收口定义在
    mergetrain.judge_env，本处为同钉第二面——两面平权，parity 由 train-hostpin-test T4c
    红绿锁钉死，任何一面改形另一面漂移=锁红）。"""
    env = dict(os.environ)
    # MECH-WIN-ALL-1 回归修复（业主 2026-11-03 00:3x「面板不能访问了」）：Windows 侧
    # python 默认文本编码=GBK，看板读 UTF-8 中文账本/子进程输出即崩（refresher 线程
    # UnicodeDecodeError 循环=页面永停「初始化中」）。强制子进程 UTF-8 模式（含看板）；
    # BUG-TRAIN-HOSTPIN-1② 起不再保留继承毒值——不赌 locale（与 mergetrain.judge_env 同钉，见 T4c）。
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    th = team_home()                                   # ...\dataworks-ai\cc-team 或 /mnt/c/.../cc-team
    code_root = os.path.dirname(os.path.dirname(th))   # ...\code（两册同内容，一块盘）
    env.setdefault("DISPATCH_CODE_ROOT", code_root)
    env.setdefault("DISPATCH_HB", os.path.join(code_root, ".cc", "state", "dispatcher.heartbeat"))
    user_home = os.path.dirname(code_root)             # 宿主用户主目录（双侧随仓派生，/home/x 亦可）
    env.setdefault("DISPATCH_PROJ", os.path.join(user_home, ".claude", "projects"))
    if os.name == "nt":
        ps = shutil.which("powershell.exe")
        if ps:
            env.setdefault("DISPATCH_PS", ps)
        # 判据宿主钉死（2026-11-03 业主「假红又是怎么回事」实锤）：本机 PATH 里的裸 `bash`
        # 会解析到 WSL bash（HOME=/home/administrator），验收命令里所有 Windows 路径判定
        # 集体失败=假红。verdict/自动复验一律用 Git Bash 绝对路径；配了却找不到=红（ccards 同语义）。
        # 03:4x 第三例假红实锤（毒轮三卡连坐，两条毒径全封）：①which("bash.exe") 在任务形
        # PATH 下命中的是 C:\Windows\System32\bash.exe=WSL 启动器；②外部会话把该启动器值
        # 显式导进了环境（03:5x 现证：进程环境挂着 system32\bash.exe，注册表两 hive 却为空
        # ——注入点不在仓内启动器，钉值须按内容验而不按「谁先声明」认）。双态判据守卫
        # NOT-WIN-HOST 被火车交卷即验误判「真红」→ DONE→OPEN REJECT 循环。规矩：凡最终
        # 钉值落在 %SystemRoot% 下（含显式传入）一律弃用改派 Git Bash（T5c 锁红绿同源）。
        _sr = (os.environ.get("SystemRoot") or r"C:\Windows").lower()

        def _bad_bash(v):
            return (not v) or (not os.path.isfile(v.replace("/", os.sep))) or \
                   os.path.normpath(v).lower().startswith(_sr)
        if _bad_bash(env.get("CARD_VERIFY_BASH")):
            _cands = [os.path.join(os.environ.get("ProgramFiles") or r"C:\Program Files",
                                   "Git", "bin", "bash.exe"),
                      r"C:\Program Files\Git\bin\bash.exe"]
            _w = shutil.which("bash.exe")
            if _w and not os.path.normpath(_w).lower().startswith(_sr):
                _cands.append(_w)
            for cand in _cands:
                if os.path.isfile(cand.replace("/", os.sep)):
                    env["CARD_VERIFY_BASH"] = cand
                    break
        # 判据宿主 PATH 补 node（2026-11-03 03:1x 实锤第二例假红：任务 PATH=注册表 system+user，
        # 不含 C:\Program Files\nodejs——board-collapse 的 DOM 桩要 node，环内每圈 rc=1/tail=[]
        # 循环 40+ 圈「待验收」，同一判据交互 shell rc=0。缺则补、已在不动，绝不改写继承值）。
        if shutil.which("node", path=env.get("PATH") or os.defpath) is None:
            nd = os.path.join(os.environ.get("ProgramFiles") or r"C:\Program Files", "nodejs")
            if os.path.exists(os.path.join(nd, "node.exe")):
                env["PATH"] = (env.get("PATH") or "") + os.pathsep + nd
    return env


def build_rings(shadow, ops=None):
    """控制面环清单：argv 按模式组装。影子=只报不动账（--dry / 不带 --apply），正式=全真。
    这是「动账面」唯一闸口，selftest T2 对其上红绿锁。
    MECH-WIN-ALL-1（业主 2026-11-03「都要在windows」）：原 WSL crontab 的信箱看门/记账/
    对账三件收进本环。BUG-CTRLD-RINGSTALL-1 起环 subprocess 挪到独立线程（Wheel），
    旧「环间串行天然单实例」（=ledger-run.sh flock 语义的替代）由 "script" 同脚本互斥组
    承接——同脚本环（recycle full/sweep）永不重叠，异脚本环并行不再互相饿死。"""
    ops = ops or HERE
    py = sys.executable
    dry = ["--dry"] if shadow else []
    apply_flag = [] if shadow else ["--apply"]
    code_root = os.path.dirname(os.path.dirname(os.path.dirname(ops)))   # ...\code
    # "script"=同脚本互斥组键（BUG-CTRLD-RINGSTALL-1）：Wheel 并发化后，原「环间串行天然
    # 单实例」（旧 ledger-run.sh 的 flock 语义替代）收窄为**同脚本不重叠**——full/sweep 共用
    # recycle.py 仍互斥，异脚本环并行。T2 锁只查 argv/mode_exempt，加键不影响。
    return [
        {"name": "dispatch", "script": "dispatch.py", "every": env_num("CTRLD_DISPATCH_SEC", 60),
         "timeout": env_num("CTRLD_DISPATCH_TIMEOUT", 240), "argv":
             [py, "-I", os.path.join(ops, "dispatch.py"), "--once"] + dry},
        {"name": "full", "script": "recycle.py", "every": env_num("CTRLD_FULL_SEC", 60),
         "timeout": env_num("CTRLD_FULL_TIMEOUT", 300), "argv":
             [py, "-I", os.path.join(ops, "recycle.py")] + apply_flag},
        {"name": "sweep", "script": "recycle.py", "every": env_num("CTRLD_SWEEP_SEC", 60),
         "timeout": env_num("CTRLD_SWEEP_TIMEOUT", 1800), "argv":
             [py, "-I", os.path.join(ops, "recycle.py"), "--ring", "sweep"] + apply_flag},
        {"name": "inbox", "script": "inbox_watchdog.py", "every": env_num("CTRLD_INBOX_SEC", 60),
         "timeout": env_num("CTRLD_INBOX_TIMEOUT", 120), "argv":
             [py, "-I", os.path.join(ops, "inbox_watchdog.py")] + apply_flag},
        {"name": "ledger", "script": "ledger.py", "every": env_num("CTRLD_LEDGER_SEC", 300),
         "timeout": env_num("CTRLD_LEDGER_TIMEOUT", 300), "argv":
             [py, "-I", os.path.join(code_root, ".cc", "ledger", "ledger.py")] + apply_flag},
        # 保障互盯（业主 seq=10 定版：保障不占卡账，账本=docs/系统保障清单.md 机器块；
        # 旧 resident_watch 读卡面路径随岗卡作废退役。--apply 才写状态/告警账，影子只报。）
        {"name": "guards", "script": "guard_watch.py", "every": env_num("CTRLD_GUARD_SEC", 300),
         "timeout": env_num("CTRLD_GUARD_TIMEOUT", 240), "argv":
             [py, "-I", os.path.join(ops, "guard_watch.py")] + apply_flag},
        {"name": "reconcile", "script": "reconcile_events.py", "every": env_num("CTRLD_RECON_SEC", 1800),
         "timeout": env_num("CTRLD_RECON_TIMEOUT", 600), "argv":
             [py, "-I", os.path.join(ops, "reconcile_events.py"), "--alarm-only"] + apply_flag},
        {"name": "fuse", "script": "fuse.py", "every": env_num("CTRLD_FUSE_SEC", 60),
         "timeout": env_num("CTRLD_FUSE_TIMEOUT", 240), "argv":
             [py, "-I", os.path.join(ops, "fuse.py")] + apply_flag},
        # 产线停摆哨（业主 2026-11-03 03:2x「5 分钟没有消耗就要系统卡并汇报」）：三源活性
        # （events 流水/会话转录/心跳）龄>300s 即立 SYS-FLOWSTOP-<日> 卡+报警，恢复自绿。
        {"name": "idlepulse", "script": "idle_pulse.py", "every": env_num("CTRLD_IDLE_SEC", 300),
         "timeout": env_num("CTRLD_IDLE_TIMEOUT", 200), "argv":
             [py, "-I", os.path.join(ops, "idle_pulse.py")] + apply_flag},
        # 夜间岗位学习（REQ-NIGHT-LEARN-1，业主 seq=23③/24④c）：逐岗（=标注项目）00:00-06:00
        # 自动一圈 FlywheelRun + 晨复核异常单列 + 经验→SOP 草案。窗内每小时推圈、窗过点出报，
        # 窗外一拍只打一行 tail（不查账）。`at` 对时=起进程不抢跑（旧系统定时行随删 WSL 退役，
        # 本环是 cc-ctrld 进程内时间轮，零 OS 定时器）。
        # 旗标遵 T2 环规：live 带 --apply，影子不带动账旗（引擎无 --apply 即默认只点名）。
        {"name": "postlearn", "script": "night_post_learn.py",
         "every": env_num("CTRLD_NIGHT_SEC", 3600),
         "at": os.environ.get("CTRLD_NIGHT_AT") or "00:05",
         "timeout": env_num("CTRLD_NIGHT_TIMEOUT", 1500), "argv":
             [py, "-I", os.path.join(ops, "night_post_learn.py")] + apply_flag},
        # 健康日报（REQ-HEALTH-WEEKLY-1，业主信箱 seq=25）：早间出一页六项口径推业主微信，
        # 纯本地台账聚合零模型调用（health_report.py 自带 AST 自检+双跑字节一致锁）。
        # at 对时走 T6 惯例（起进程不抢跑，准点 07:00）；影子=dry 只报不落盘不寄信，
        # 正式 --apply 才写 reports/ 并追加 PM-REPLIES 推送块（日级幂等，fuse-S6 同通道）。
        {"name": "health", "script": "health_report.py", "every": env_num("CTRLD_HEALTH_SEC", 86400),
         "at": os.environ.get("CTRLD_HEALTH_AT", "07:00"),
         "timeout": env_num("CTRLD_HEALTH_TIMEOUT", 180), "argv":
             [py, "-I", os.path.join(ops, "health_report.py")] + apply_flag},
        # svcheck 环 PARKED（业主 2026-11-03 令删 WSL，Ubuntu 已注销）：被测 stream-agent
        # 本体原住 WSL，随删失联；SV 迁 Windows=另立项候拍板（§二 svcheck 行同步改注）。
        # 恢复条件=SV 应用侧有新宿主锚后按本环模板重登记（at 对时+T6 锁），勿引回 wsl.exe。
    ]


def secs_to_at(hhmm, now=None):
    """距本机下次 HH:MM 的秒数（每日环对时；过点=滚明天）。"""
    now = now if now is not None else time.time()
    lt = time.localtime(now)
    h, m = (int(x) for x in hhmm.split(":")[:2])
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1)) - now
    return target if target > 0 else target + 86400


def init_schedule(rings, now=None, align_at=True):
    """起拍排期：普通环=0（即刻先来一轮，影子对账取此语义）；
    带 at 的每日环=对时到下一个 HH:MM（业主 2026-11-03「定时任务不要用操作系统定时」，
    cc-sv-patrol 迁进本环后仍须 03:30 准点，不能起进程就抢跑）。"""
    now = now if now is not None else time.time()
    for r in rings:
        if align_at and r.get("at"):
            r["next_due"] = now + secs_to_at(r["at"], now)
        else:
            r["next_due"] = 0.0


def loop_core(rings, clock, run, log=None):
    """纯时间片一拍（可注入时钟/执行器=红绿锁直测点）：到点环各跑一次。
    排期规则 next_due = max(原定到点+周期, 完成时刻)——准点追赶。
    执行器返回 None=同脚本组在跑（拒发）：**跳拍不再即丢**——
    · 到点撞组忙：记欠账 owed（首笔记 RING-OWED 留痕），节拍锚照常 +every 推进
      （下个到点仍准点），此拍不计 runs（旧版把拒发记成跑过=账面假象）；
    · 每拍对欠账环试补放：组内空位即放行一次（RING-CATCHUP），多笔欠账并成一次，
      绝不叠跑；补放不动节拍锚，之后的到点拍照旧节律走；
    · 组内公平轮到：同脚本组里若有**更久未跑**的同伴也在等（到点或欠账），本环让位
      （RING-YIELD 留痕，锚不推、账保留）。只记欠账不够——高优先环占位≥自身节拍时
      「空位先到先得」=空位永远被它先抢（G 案实证）；让位规则=旧串行轮天然交替
      （full 跑完顺位就轮到 sweep）的并发化，撤之低优先环仍饿死。
    （BUG-CTRLD-SWEEPSTARVE-1：full/sweep 同脚本同节拍同相位 :52，旧「跳拍即丢」下
    sweep 永不到点=REDAUTO 转正通道停摆。T1 同步模型不经拒发/让位路径，规则不动。）"""
    now = clock()
    log = log or (lambda m: None)
    fired = []

    def _grp(x):                                # 同脚本互斥组键（与 Wheel.fire 同口径）
        s = x.get("script")
        if s is None:
            av = x.get("argv")
            s = os.path.basename(av[2]) if av and len(av) > 2 else None
        return s

    def _waiting(x):
        return bool(x.get("owed")) or now >= x["next_due"]

    for r in rings:
        due = now >= r["next_due"]
        if not due and not r.get("owed"):
            continue
        g = _grp(r)
        if g is not None and any(w is not r and _grp(w) == g and
                                 w.get("last", -1e18) < r.get("last", -1e18)
                                 for w in rings if _waiting(w)):
            # 组内有更久未跑的同伴在等=让位：不试 fire（试了也是白撞），账保留锚不推
            if due and not r.get("owed"):
                log("RING-YIELD %s 同脚本组内同伴更久未跑 → 本拍让位（欠账保留，锚不推）"
                    % r["name"])
                r["owed"] = 1
            continue
        res = run(r)
        if res is None:                        # 组忙拒发：到点这拍记欠账；补放下一拍再试
            if due:
                if not r.get("owed"):
                    log("RING-OWED %s 到点撞上同脚本组在跑 → 记欠账（组内空位补放，不叠跑）"
                        % r["name"])
                r["owed"] = r.get("owed", 0) + 1
                r["skips"] = r.get("skips", 0) + 1
                r["next_due"] = r["next_due"] + r["every"]
            continue
        spent = res or 0
        if r.get("owed") and not due:
            log("RING-CATCHUP %s 组内空位 → 补放欠账 %d 拍（多笔并一次，节拍锚不动）"
                % (r["name"], r["owed"]))
        r["runs"] = r.get("runs", 0) + 1
        r["last"] = now
        if due:
            r["next_due"] = max(r["next_due"] + r["every"], now + spent)
        r["owed"] = 0
        fired.append((r["name"], spent))
    return fired


_log_fn = print
LOG_STAMP = ""
_LOG_LOCK = threading.Lock()      # 环并线后多线程写同一 ctrld.log（BUG-CTRLD-RINGSTALL-1）


def log_line(home, msg):
    line = "%s %s" % (time.strftime("%F %T"), msg)
    try:
        d = os.path.join(home, "logs")
        os.makedirs(d, exist_ok=True)
        with _LOG_LOCK:
            with open(os.path.join(d, "ctrld.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass
    try:
        with _LOG_LOCK:
            print(line, flush=True)
    except UnicodeEncodeError:      # GBK 控制台打不出 UTF-8 尾行=降级重打安全版，绝不断环
        try:
            print(line.encode(sys.stdout.encoding or "ascii", "replace").decode(
                sys.stdout.encoding or "ascii", "replace"), flush=True)
        except Exception:
            pass


def kill_tree(p):
    """杀掉整棵进程树（BUG-CTRLD-RINGSTALL-1 候选②的根治件）：Windows 下
    subprocess.run(capture_output=True, timeout) 超时 kill 只杀直系子，起会话的孙进程
    （run-cc.ps1→claude 类长命）仍持继承句柄=communicate 等 EOF 无限堵；posix 下孙进程
    同理脱管。Windows=taskkill /T /F（连孙带叶），posix=killpg（Popen 侧配
    start_new_session 自成进程组）。杀不杀得动都不抛——看门与有界 wait 兜底。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True, timeout=30, creationflags=NO_WIN)
        else:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        p.kill()
    except Exception:
        pass


def _tail_file(path):
    """环输出文件尾行（末 64KB 内），口径与旧 capture_output 版一致：取最后一行压空白截 220。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > 65536:
                f.seek(-65536, os.SEEK_END)
            raw = f.read()
        out = raw.decode("utf-8", "replace").strip().splitlines()
        return " ".join((out[-1] if out else "").split())[:220]
    except OSError:
        return ""


def run_ring(home, r, reg=None):
    """跑一环（有界），返回墙钟耗时秒——**本函数在 Wheel 的环线程里跑，不在时间轮主线程**。
    BUG-CTRLD-RINGSTALL-1 三改：
    ① 输出走**临时文件**而非管道：句柄被孙进程继承只影响文件写，不堵 wait()（管道版
      timeout kill 后仍要等 EOF=超时失真的潜在放大件）；
    ② wait(timeout) 到点 **kill_tree 整树** + 有界 wait(20)：环超时=子树留痕弃跑，
      绝不无限等；孙进程脱管续写文件的残留由看门兜底；
    ③ stdin=DEVNULL：无控制台（NO_WIN）下子进程读 stdin=永久堵的口子一并封死。
    单环崩≠整体死：超时/异常留痕后下拍准点再排（dispatch.py TICK-ERR 同纪律）。
    reg=可选登记簿（Wheel 看门用）：reg["proc"] 挂 Popen 句柄供外部杀树。"""
    t0 = time.time()
    d = os.path.join(home, "logs")
    os.makedirs(d, exist_ok=True)
    # 每环**固定一个**输出文件、起拍截断复用：若按次 mkstemp，被派生长命会话（run-cc.ps1→
    # claude 类）继承的句柄会让文件删不掉，每 60s 一拍=垃圾无限累积。固定名=稳态每环一个，
    # 被残孙写脏也只污染 tail 文案（下一次起拍即截断），不进账本。
    outpath = os.path.join(d, "ring-out.%s" % r["name"])
    kw = {"stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "env": child_env()}
    if os.name == "nt":
        kw["creationflags"] = NO_WIN
    else:
        kw["start_new_session"] = True          # 自成进程组，killpg 连树杀
    try:
        # fd 必须在 Popen（CreateProcess dup 于斯）**之后**才关：先关再传=多线程下
        # fd 槽位会被别的线程 open 复用，句柄错拿或 WinError 9 闪现（沙箱烟测实锤）。
        with open(outpath, "wb") as out_f:
            p = subprocess.Popen(r["argv"], stdout=out_f.fileno(), **kw)
        if reg is not None:
            reg["proc"] = p
        try:
            rc = p.wait(timeout=r["timeout"])
            log_line(home, "RING %s rc=%d tail=[%s]" % (r["name"], rc, _tail_file(outpath)))
        except subprocess.TimeoutExpired:
            kill_tree(p)
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                log_line(home, "RING-TIMEOUT %s >%ds → 整树弃（直系子 kill 未倒=看门计数），"
                               "不叠跑（下拍照排）" % (r["name"], r["timeout"]))
            else:
                log_line(home, "RING-TIMEOUT %s >%ds → 已 kill_tree 整树（taskkill /T 或 killpg），"
                               "本轮弃不叠跑（下拍照排）" % (r["name"], r["timeout"]))
    except Exception as e:
        log_line(home, "RING-ERR %s %r → 单环崩≠整体死，下拍自愈重试" % (r["name"], e))
    return time.time() - t0


class Wheel:
    """环轮（BUG-CTRLD-RINGSTALL-1 主修）：到点环起**独立线程**跑 subprocess，主线程
    立即回到排期/心跳/看板/看门——单环慢（sweep 复验判据实测 330~513s、reconcile ~147s）
    只堵自己那条环，dispatch/fuse/inbox 照常按拍（fuse.log 同窗零行的保命失明结构性消除）。
    语义承接：
    · 同脚本互斥组：r["script"] 相同的环永不重叠（recycle full/sweep 单实例=旧串行的
      flock 语义）；busy 中到点的环**记欠账不丢拍**，组内空位即补放（多笔并一次、
      不叠跑）——BUG-CTRLD-SWEEPSTARVE-1：旧「跳过即丢」遇 full/sweep 同节拍同相位
      =低优先环永久 0 拍；补放规则=旧串行轮「谁空谁上、账不丢」的并发化重建。
    · 看门兜底：环超自身 timeout+宽限（CTRLD_STUCK_GRACE_SEC 默认 120s）仍未归=杀不干净的
      堵环——RING-STUCK 留痕、kill_tree、放行环位（泄漏线程 daemon 化，进程退出不等它）。
    · 收工 drain：退出前等已发环收线，超 CTRLD_DRAIN_SEC（默认 60s）逐个杀树后弃——
      收工路径同样不被堵环永绊。"""

    def __init__(self, home, grace=None):
        self.home = home
        self.grace = env_num("CTRLD_STUCK_GRACE_SEC", 120) if grace is None else grace
        self._lock = threading.Lock()
        self._busy = {}                          # name -> dict(script,t0,timeout,reg,thread,dropped)

    def fire(self, r):
        """loop_core 的执行器位：登记+起线程立即返回（spent≈0，排期仍准点追赶）；
        本环或同脚本组在跑=返回 **None**（拒发）。BUG-CTRLD-SWEEPSTARVE-1：旧版拒发返回
        0.0 与「真跑了 0 秒」不可分，loop_core 照常推进拍数——同相位同节拍的低优先环
        （full/sweep 每 60s 同点 :52）每拍必撞组忙=**永久 0 拍**（跳拍即丢，无补放语义；
        cron IgnoreNew 的前提「下一拍重来」在此不成立，下一拍照样同撞）。拒发交还调度层
        记欠账+空位补放，互斥本身不动。"""
        now = time.time()
        script = r.get("script") or os.path.basename(r["argv"][2])
        with self._lock:
            if r["name"] in self._busy or any(b["script"] == script for b in self._busy.values()):
                return None                      # 本环在跑 或 同脚本组在跑=拒发（不叠跑），由调度层记欠账
            b = {"script": script, "t0": now, "timeout": r["timeout"], "reg": {}, "dropped": False}
            self._busy[r["name"]] = b
        th = threading.Thread(target=self._work, args=(r, b), daemon=True,
                              name="ctrld-ring-%s" % r["name"])
        b["thread"] = th
        th.start()
        return 0.0

    def _work(self, r, b):
        try:
            run_ring(self.home, r, b["reg"])
        except Exception as e:                   # 环线程自身崩=只留痕，槽位必放（绝不留永久 busy）
            log_line(self.home, "RING-THREAD-ERR %s %r → 槽位已放，下拍自愈" % (r["name"], e))
        finally:
            with self._lock:
                if not b["dropped"] and self._busy.get(r["name"]) is b:
                    self._busy.pop(r["name"], None)

    def watchdog(self, now=None, log=None):
        """看门（独立超时+兜底的收口件）：超 timeout+grace 未归的环=留痕+杀树+放行环位。"""
        now = now if now is not None else time.time()
        log = log or (lambda m: log_line(self.home, m))
        n = 0
        with self._lock:
            for name in list(self._busy):
                b = self._busy[name]
                el = now - b["t0"]
                if el <= b["timeout"] + self.grace:
                    continue
                log("RING-STUCK %s 已 %.0fs > 超时 %ds+宽限 %.0fs → kill_tree+放行环位"
                    "（线程泄漏 daemon 记账，堵环不永占道）" % (name, el, b["timeout"], self.grace))
                p = b["reg"].get("proc")
                if p is not None:
                    try:
                        kill_tree(p)
                    except Exception:
                        pass
                b["dropped"] = True
                self._busy.pop(name, None)
                n += 1
        return n

    def drain(self, deadline=None, poll=0.2):
        """退出前收线：等 busy 清空；超 deadline 仍未清=逐个 kill_tree 后弃（有界收工）。"""
        t0 = time.time()
        while True:
            with self._lock:
                items = dict(self._busy)
            if not items:
                return 0
            if deadline is not None and time.time() - t0 > deadline:
                with self._lock:
                    for b in list(self._busy.values()):
                        b["dropped"] = True
                    self._busy.clear()
                for b in items.values():
                    p = b["reg"].get("proc")
                    if p is not None:
                        try:
                            kill_tree(p)
                        except Exception:
                            pass
                log_line(self.home, "RING-DRAIN 收工超时 >%.0fs：弃跑 %d 环（kill_tree 后收线）"
                         % (deadline, len(items)))
                return len(items)
            time.sleep(poll)

    def busy(self):
        with self._lock:
            return dict(self._busy)


def hb_path(home):
    return os.path.join(home, "logs", "ctrld.heartbeat")


def write_hb(home, shadow, rings, board_pid, now=None, throttle=5.0, state={}):
    """心跳只写 ctrld 自己（mode 落文件），不是账本；节流防每秒敲盘。"""
    now = now if now is not None else time.time()
    if state.get("home") == home and now - state.get("t", 0) < throttle:
        return hb_path(home)
    state["home"], state["t"] = home, now
    p = hb_path(home)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    txt = "%s ctrld mode=%s pid=%d board=%s rings=%s\n" % (
        time.strftime("%F %T"), "shadow" if shadow else "live", os.getpid(),
        board_pid or "-", ",".join("%s/%ss" % (r["name"], r["every"]) for r in rings))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(txt)
    os.replace(tmp, p)
    return p


def board_spawn(home):
    """看板 status-web 同进程组带起（默认继承=同组；ctrld 退出时 terminate 兜底）。"""
    port = os.environ.get("CTRLD_BOARD_PORT", "8790")
    script = os.path.join(ROOT, "board", "status-web.py")
    lf = open(os.path.join(home, "logs", "status-web.log"), "a", encoding="utf-8")
    return subprocess.Popen([sys.executable, "-I", script, port],
                            stdout=lf, stderr=subprocess.STDOUT, env=child_env(), creationflags=NO_WIN)


def supervise_board(board, spawn_fn, log, now=None, fast_die_s=30.0, max_fast=3):
    """纯决策（可注入=红绿锁直测）：看板子进程死了返回 (新句柄|None, 文案)。
    连续快速死亡>max_fast 次=多半端口占用（如 WSL 看板未停）→弃权留痕不空转。"""
    now = now if now is not None else time.time()
    if board is None:
        return None, None
    code = board.poll() if hasattr(board, "poll") else board
    if code is None:
        return board, None
    age = now - getattr(board, "_ctrld_spawn_t", now)
    streak = getattr(board, "_ctrld_streak", 0) + 1
    if age < fast_die_s and streak > max_fast:
        log("BOARD-GIVEUP 子进程连续快速死亡%d次(最近一命%.0fs)→弃权留痕，不再重起——"
            "先查 :8790 是否被 WSL 看板占（切流 runbook 第3步）" % (streak, age))
        return None, "giveup"
    nb = spawn_fn()
    nb._ctrld_spawn_t = now
    nb._ctrld_streak = streak
    log("BOARD-RESTART 子进程死(rc=%s 命%.0fs) → 重起（连快死计数=%d）" % (code, age, streak))
    return nb, "restart"


STOP = {"flag": False}


def _on_term(signum, frame):
    STOP["flag"] = True


def _pid_alive(pid):
    """判活唯一正解=转 ccards.config.pid_alive（MECH-PIDALIVE-WIN-1 权威件，红绿锁
    sweeplock-deadpid-test 在册）。本函数自装的一套在 Windows 有两条死路（今夜实锤）：
    ctypes 句柄异常→except→os.kill(pid,0)——而 sig=0 在 Windows 恰是 CTRL_C_EVENT，
    会被广播给进程组=**探针变处决**（26004 案 01:38:36 即此）。导不到正解时保守判活
    （宁拒不错杀；确死误活由 5min/120s 龄窗兜底）。"""
    try:
        return _cfg.pid_alive(pid)
    except Exception:
        if os.name == "nt":
            return True
        try:
            os.kill(pid, 0)
            return True
        except (OSError, SystemError, RuntimeError):
            return False


def _singleton_ok(home, shadow):
    """常驻互斥（00:08:43 现网实锤：调度器 RestartCount 在旧实例健在时又拉起一份=双引擎
    双环双写账+双看板重叠影，23:32 三起同理）。规则：影子沙箱不管；live 起前读心跳，
    若另有 live pid 且进程仍活⇒本实例拒生退场。确需换血=先杀对方 pid 再起。"""
    if shadow:
        return True
    try:
        with open(hb_path(home), encoding="utf-8") as f:
            first = f.readline()
    except OSError:
        return True
    import re as _re
    m_mode = _re.search(r"mode=(\w+)", first)
    m_pid = _re.search(r"pid=(\d+)", first)
    if not m_mode or not m_pid or m_mode.group(1) != "live":
        return True
    other = int(m_pid.group(1))
    # 时效门槛（00:28:16 案补刃：被杀瞬间调度器秒级复起，对头进程对象残存使 _pid_alive
    # 假报“仍活”，九次重启全被自己拒掉=车队瞎 31min）。活引擎每拍写心跳（throttle 5s），
    # 心跳龄 >120s 一律视为死，放行新生；龄窗内且真活才拒。
    fresh = False
    try:
        import datetime as _dt
        t_hb = _dt.datetime.strptime(first[:19], "%Y-%m-%d %H:%M:%S").timestamp()
        fresh = (time.time() - t_hb) <= 120
    except (ValueError, OSError):
        fresh = True                         # 时刻读不懂=保守按活（宁拒不错双跑）
    if other != os.getpid() and fresh and _pid_alive(other):
        log_line(home, "SINGLETON-REJECT hb live pid=%d 仍活着⇒本实例(%d)拒生退场"
                 "（防双引擎双写账；确需换血先杀 %d 再起）" % (other, os.getpid(), other))
        return False
    return True


def _reap_orphans(home):
    """上岗收孤儿环/看板（BUG-FUSE-SELFKILL-1 ②死循环断点：Stop-ScheduledTask/kill 只杀
    引擎根，在跑环与看板变孤儿进程继续持有「同脚本组」锁 → 新引擎 dispatch 永远 RING-OWED
    → dispatcher.heartbeat 断更 → S1 误判派发器死亡把新引擎也处决，今夜四连案
    00:28/01:00/01:05/01:15 全链由此）。规则保守：只杀 python* 且命令行命中
    CTRLD_REAP_WILD（缺省 ops/board 两形）且**父进程已死**的进程；有活父的（本引擎亲儿、
    PM 会话、车道 worker 起的）一律不动。taskkill /T 连树收，孙代不留。返回收杀数。"""
    ps = shutil.which("powershell.exe") or "powershell"
    if os.name != "nt" or not ps:
        return 0
    wilds = [x.strip() for x in (os.environ.get("CTRLD_REAP_WILD")
                                 or r"*cc-team\ops*,*cc-team\board*").split(",") if x.strip()]
    cond = " -or ".join("($_.CommandLine -like '%s')" % w.replace("'", "''") for w in wilds)
    q = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and (%s) } | "
         "ForEach-Object { '{0};{1}' -f $_.ProcessId, $_.ParentProcessId }" % cond)
    try:
        p = subprocess.run([ps, "-NoProfile", "-Command", q], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60, creationflags=NO_WIN)
    except (OSError, subprocess.TimeoutExpired):
        log_line(home, "REAP-SKIP powershell 探场失败（rc=127 语义=不拦上岗，孤儿锁候下一轮）")
        return 0
    me = os.getpid()
    killed = []
    for ln in (p.stdout or "").splitlines():
        f = ln.strip().split(";")
        if len(f) < 2 or not f[0].isdigit() or not f[1].isdigit():
            continue
        pid, ppid = int(f[0]), int(f[1])
        if pid == me or _pid_alive(ppid):
            continue                              # 自己/有活父=正常在跑，不动（保守面）
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=30, creationflags=NO_WIN)
            killed.append(pid)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if killed:
        log_line(home, "REAP-ORPHAN 收孤儿环/看板 n=%d pids=%s"
                 "（父死留儿=持组锁锁死新引擎的上游病根，上岗先扫场）" % (
                     len(killed), ",".join(map(str, killed))))
    return len(killed)


def loop(shadow, with_board=False, once=False, max_ticks=None, rings=None, wheel=None,
         tick_sec=None):
    """主线程=纯排期+心跳+看板+看门（BUG-CTRLD-RINGSTALL-1）：环 subprocess 全部经
    Wheel.fire 挪进独立线程，任何单环慢/堵都不再冻结其余环与心跳（旧版环内同步跑
    sweep 复验判据 330~513s/reconcile ~147s=整轮停摆+保险丝同窗失明，见卡面归因）。
    rings/wheel/tick_sec 仅注入沙箱红绿锁用（缺省=现网行为不变）；once/max_ticks 收工
    前 drain 已发环收线（有界，超时杀树弃跑），保持 run-once 报告语义。"""
    home = team_home()
    if not once and not shadow:
        _reap_orphans(home)                      # 上岗先扫场：孤儿环持组锁=今夜自杀环的上游病根
    if not once and not _singleton_ok(home, shadow):
        return []
    rings = rings if rings is not None else build_rings(shadow)
    wheel = wheel if wheel is not None else Wheel(home)
    if tick_sec is None:
        try:
            tick_sec = max(0.05, float(os.environ.get("CTRLD_TICK_SEC") or 1.0))
        except ValueError:
            tick_sec = 1.0
    if not once:
        write_hb(home, shadow, rings, None)        # 先占心跳再排环：竞窗从 1s 缩到毫秒级（双起竞态兜底）
    init_schedule(rings)                           # 普通环即刻先来一轮；带 at 的每日环对时（业主 2026-11-03 令：定时任务不用操作系统定时）
    log_line(home, "ctrld 起 mode=%s board=%s 环=%s（零cron零操作系统定时器，纯时间片；环起独立线程互不冻结）" % (
        "shadow" if shadow else "live", "on" if with_board else "off",
        " ".join("%s:%ss%s" % (r["name"], r["every"], ("@%s" % r["at"]) if r.get("at") else "")
                 for r in rings)))
    board = None
    if with_board:
        board = board_spawn(home)
        board._ctrld_spawn_t = time.time()
        board._ctrld_streak = 0
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _on_term)
        except (ValueError, OSError):
            pass
    ticks = 0
    # 拍体吞异常必留痕（2026-11-03 23:27:30 静退案：宿主=pythonw 无 stderr，拍体任何
    # 异常旧码=直接掀翻整个常驻进程且 traceback 蒸发，仅凭「收 ticks STOP=False」一行
    # 无法归因；finally 的 board.terminate 还会连带杀看板）。修复=拍体 try/except：
    # 首见签落全栈、同签 60s 内只计数（退场时补报抑制数）、换签再落全栈；引擎不死。
    tick_err = {"sig": None, "n": 0, "t": 0.0}
    try:
        while not STOP["flag"]:
            try:
                loop_core(rings, time.time, wheel.fire,
                          lambda m: log_line(home, m))
                wheel.watchdog()
                write_hb(home, shadow, rings, getattr(board, "pid", None))
                if with_board:
                    board, _ = supervise_board(board, board_spawn, lambda m: log_line(home, m))
            except Exception:
                tb = traceback.format_exc().strip()
                sig = tb.splitlines()[-1][:160] if tb else "?"
                now = time.time()
                if sig != tick_err["sig"] or now - tick_err["t"] >= 60:
                    log_line(home, "TICK-ERR %s" % tb.replace("\n", " ⏎ ")[:900])
                    tick_err.update(sig=sig, t=now)   # n 不清零=跨签累计
                else:
                    tick_err["n"] += 1
            ticks += 1
            if once or (max_ticks and ticks >= max_ticks):
                break
            time.sleep(tick_sec)
    finally:
        # 收工有界 drain（防堵环绊死退出）；但 run-once 对账通道保留旧语义=等全部环
        # 自然收线（单环时长自有 subprocess timeout 兜底），不等 60s 就杀。
        try:
            wheel.drain(None if once else env_num("CTRLD_DRAIN_SEC", 60))
        except Exception as e:
            log_line(home, "RING-DRAIN-ERR %r" % (e,))
        if board is not None:
            try:
                board.terminate()
            except OSError:
                pass
        if tick_err["n"]:
            log_line(home, "TICK-ERR 抑制重复 n=%d 末签=%s" % (tick_err["n"], tick_err["sig"]))
        log_line(home, "ctrld 收 ticks=%d STOP=%s" % (ticks, STOP["flag"]))
    return rings


def run_once(shadow):
    """对账通道：跑一拍出报告（影子迁移=「只报不动账对账一圈」的载体）。"""
    home = team_home()
    rings = loop(shadow=shadow, once=True)
    rep = {"ts": time.strftime("%F %T"), "mode": "shadow" if shadow else "live",
           "rings": [{"ring": r["name"], "every": r["every"], "runs": r.get("runs", 0),
                      "argv_tail": os.path.basename(r["argv"][2]) + " " +
                                 " ".join(r["argv"][3:])} for r in rings]}
    p = os.path.join(home, "logs", "ctrld-%s-report.json" % ("shadow" if shadow else "live"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("CTRLD-ONCE 报告→%s" % p)
    return rep


def status():
    home = team_home()
    p = hb_path(home)
    if not os.path.isfile(p):
        print("ctrld: 心跳不存在 %s（未上岗/未跑过）" % p)
        return 1
    age = time.time() - os.path.getmtime(p)
    with open(p, encoding="utf-8") as f:
        first = f.readline().strip()
    print("ctrld 心跳 %s\n  龄=%.0fs mode行=[%s]" % (p, age, first))
    print("  环=%s" % " ".join("%s:%ss" % (r["name"], r["every"]) for r in build_rings(False)))
    return 0


# ---------- 常驻化薄封装 ----------

def host_kind():
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def install_windows(live, board, start_now):
    """计划任务 cc-ctrld：AtLogon 触发 + 失败自动重启 + 无限时限 + IgnoreNew——与
    cc-boot-bringup（REQ-BOOT-BRINGUP-1）同一注册机制、同一 EncodedCommand 中文保字口径。"""
    ps = shutil.which("powershell.exe") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    task = _cfg.CC_TASK_CTRLD  # 计划任务名：env CTRLD_TASK_NAME > tasks.ctrld > 默认 cc-ctrld
    desc = ("%s — REQ-CTRL-PLAIN-1 单进程控制面（三环并一 loop，纯时间片，零cron零WSL）："
            "%s 常驻，心跳 cc-team/logs/ctrld.heartbeat。勿随手删：删了它=Windows 原生控制面"
            "不再随登录起，车队发单/体检/滞留扫全停（WSL 旧环退役后无兜底）。"
            "重注册: python -I cc-team/ops/ctrld.py install --host windows%s；"
            "自检: boot_check TC11") % (task, "live(动账)" if live else "shadow(只报)",
                                        " --live" if live else "")
    args = '%s loop --%s%s' % ('"%s"' % os.path.join(HERE, "ctrld.py").replace("/", os.sep),
                               "live" if live else "shadow", " --with-board" if board else "")
    ps_script = """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$name = '@TASK@'
$action = New-ScheduledTaskAction -Execute '@PY@' -Argument '@ARGS@' -WorkingDirectory '@WD@'
$tLogon = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -Hidden `
  -RestartCount 9 -RestartInterval (New-TimeSpan -Minutes 1)
$settings.ExecutionTimeLimit = 'PT0S'
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $tLogon -Settings $settings -Principal $principal -Description '@DESC@' | Out-Null
Write-Output "registered=$name"
(Get-ScheduledTask -TaskName $name).Triggers | ForEach-Object { "trigger=" + $_.CimClass.CimClassName }
(Get-ScheduledTask -TaskName $name).Actions | ForEach-Object { "action=" + $_.Execute + ' ' + $_.Arguments }
"limit=" + (Get-ScheduledTask -TaskName $name).Settings.ExecutionTimeLimit
"state=" + (Get-ScheduledTask -TaskName $name).State
"""
    # 黑窗口根除第二刀（业主 2026-11-03「为什么弹出很多个黑窗口」）：任务动作钉 pythonw.exe。
    # 交互模式计划任务给控制台 python.exe 分配可见 conhost=常驻黑窗是 ctrld 自己；pythonw 无控制台，
    # 环内派生又全部自带 NO_WIN（上一刀补的 34 处）→ 整树零窗口。非 Windows/找不到 pythonw 原样。
    _py = sys.executable
    _pw = os.path.join(os.path.dirname(_py), "pythonw.exe")
    PY = _pw if os.name == "nt" and os.path.exists(_pw) else _py
    ps_script = ps_script.replace("@TASK@", task).replace("@PY@", PY)
    ps_script = ps_script.replace("@ARGS@", args.replace("'", "''"))
    ps_script = ps_script.replace("@WD@", ROOT)
    ps_script = ps_script.replace("@DESC@", desc.replace("'", "''"))
    if start_now:
        ps_script += 'Start-ScheduledTask -TaskName $name; "started=1"\n'
    import base64
    b64 = base64.b64encode(ps_script.encode("utf-16-le")).decode()
    p = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", b64],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WIN)
    out = ((p.stdout or "") + (p.stderr or "")).replace("\r", "")
    print(out.strip())
    if p.returncode != 0 or ("registered=%s" % task) not in out:
        print("INSTALL=FAIL task=%s rc=%d" % (task, p.returncode))
        return 1
    print("INSTALL=PASS task=%s limit=PT0S(无限) restart=9x1min 触发=AtLogon" % task)
    return 0


def install_template(kind):
    # 模板收编进引擎树（cc-team/deploy/，REQ-OSS-PREP-1）：独立 clone 也自带 Linux/macOS 常驻模板
    tpl = {"linux": os.path.join(ROOT, "deploy", "systemd", "cc-ctrld.service"),
           "macos": os.path.join(ROOT, "deploy", "launchd", "com.cc.ctrld.plist")}[kind]
    tpl = os.path.abspath(tpl)
    if kind == "linux":
        print("TEMPLATE=%s\n  装：cp '%s' ~/.config/systemd/user/ && systemctl --user daemon-reload "
              "&& systemctl --user enable --now cc-ctrld\n  （现役 WSL cc-dispatch+crontab 保留为兼容路径，"
              "开源仓直接用它=零 WSL 依赖）" % (kind, tpl))
    else:
        print("TEMPLATE=%s\n  装：cp '%s' ~/Library/LaunchAgents/ && launchctl load 对应标签\n"
              "  （REQ-REG-HOST-1 host/launcher 接口已预留，macOS 实跑按业主发话再做）" % (kind, tpl))
    print("  模板路径=%s exists=%s" % (tpl, os.path.isfile(tpl)))
    return 0


def install(host, live, board, start_now):
    host = "auto" if host in (None, "auto") else host
    if host == "auto":
        host = host_kind()
    if host != host_kind():
        print("INSTALL=拒 host=%s 与本机(%s)不符——本机注册只认 %s（模板安装请用 install --host linux/macos 打印步骤）"
              % (host, host_kind(), host_kind()))
        return 2
    if host == "windows":
        return install_windows(live, board, start_now)
    return install_template(host)


# ---------- --selftest 红绿锁（全注入：伪时钟/伪执行器/伪看板，不碰真账不起真进程） ----------

def _selftest(fault=None):
    """锁组（T1~T7），全注入不碰真账。--fault=<锁名> 注入偏差（行为面或期望面，注释各钉一处），
    对应锁必红——「守卫红得起来」自证，撤了必绿。"""
    import tempfile
    home = tempfile.mkdtemp(prefix="ctrld-selftest-")
    results = {}

    # T1 纯时间片排期确定性：A(每10,秒回) / C(每10,单跑25s)。三拍虚拟时钟。
    #    规则：准点追赶 next_due=到点+周期；跑超=完成时刻重排，失手拍跳过不叠跑。
    clock = {"t": 0.0}
    ra = {"name": "A", "every": 10, "timeout": 999}
    rc = {"name": "C", "every": 10, "timeout": 999}
    for r in (ra, rc):
        r["next_due"] = 0.0
    spent_map = {"A": 0.0, "C": 25.0}
    if fault == "schedule":
        ra["every"] = 1000                         # 偏差注入（行为面）：A 周期失真
    fired = []

    def vrun(r):
        fired.append((r["name"], round(clock["t"], 1)))
        return spent_map[r["name"]]

    loop_core([ra, rc], lambda: clock["t"], vrun)              # @0: A,C 都跑
    clock["t"] = 10.0
    loop_core([ra, rc], lambda: clock["t"], vrun)              # @10: 只 A（C 在跑超后 25 才到点）
    clock["t"] = 25.0
    loop_core([ra, rc], lambda: clock["t"], vrun)              # @25: A,C（C 失手拍 10/15/20 不补跑）
    t1 = (fired == [("A", 0.0), ("C", 0.0), ("A", 10.0), ("A", 25.0), ("C", 25.0)]
          and ra["next_due"] == 30.0 and rc["next_due"] == 50.0)
    results["T1 时间片排期：追赶/失手跳过/不叠跑（撤规则必红）"] = t1

    # T2 模式 argv 闸（动静账唯一闸口，逐环语义）：
    #   影子=dispatch 带 --dry（既有假绿禁令）、其余动账环**无 --apply**（默认 dry 只报）；
    #   live=dispatch 无 --dry、动账环必带 --apply。dispatch 环本无 --apply 概念，不混判；
    #   mode_exempt=纯只读环（如 svcheck），两套名单必须一致。
    sh, lv = build_rings(True, ops="/x"), build_rings(False, ops="/x")
    if fault == "argv":
        for r in lv[1:]:                           # 偏差注入（行为面）：live 掉 --apply=空闸
            r["argv"] = [x for x in r["argv"] if x != "--apply"]
    ok2 = ("--dry" in sh[0]["argv"] and "--apply" not in sh[0]["argv"]
           and all(("--apply" not in r["argv"]) and ("--dry" not in r["argv"][2:]) for r in sh[1:])
           and "--dry" not in lv[0]["argv"] and "--apply" not in lv[0]["argv"]
           and all("--apply" in r["argv"] for r in lv[1:] if not r.get("mode_exempt"))
           # mode_exempt（只读环无 --apply 概念）名单两侧必须一致，防「加只读环不登记」溜闸
           and [r["name"] for r in sh if r.get("mode_exempt")]
           == [r["name"] for r in lv if r.get("mode_exempt")])
    results["T2 影子只报不动账/live 才带 --apply（撤闸必红）"] = ok2

    # T3 心跳：写临时根；mode/rings 字段齐；节流窗(<5s)内不重敲；影子心跳混 live=红。
    p = write_hb(home, True, sh, None, now=1000.0)
    p2 = write_hb(home, True, sh, None, now=1001.0)
    p3 = write_hb(home, True, sh, None, now=1006.0)
    wrote = open(p, encoding="utf-8").read()
    ok3 = (p == p2 == p3 and os.path.isfile(p) and "mode=shadow" in wrote and "rings=" in wrote)
    if fault == "hb":
        write_hb(home, False, sh, None, now=2006.0)            # 偏差注入（行为面）：影子写成 live
        wrote = open(p, encoding="utf-8").read()
        ok3 = "mode=shadow" in wrote
    results["T3 心跳文件 mode=shadow/rings 齐+节流（写假=红）"] = ok3

    # T4 看板监护：子死→重起一次；连续快速死亡>3→弃权留痕（多半 :8790 被占，防空转烧日志）。
    logs = []

    class FakeProc:
        def __init__(self, seq, spawn_t, streak=0):
            self.seq = list(seq)
            self._ctrld_spawn_t = spawn_t
            self._ctrld_streak = streak
            self.pid = id(self)

        def poll(self):
            return self.seq.pop(0) if self.seq else None

    b = FakeProc([1], spawn_t=0.0, streak=0)
    nb, act = supervise_board(b, lambda: FakeProc([None], spawn_t=10.0),
                              lambda m: logs.append(m), now=10.0)
    t4a = nb is not None and act == "restart" and any("BOARD-RESTART" in m for m in logs)
    streak4 = 0 if fault == "board" else 3         # 偏差注入（行为面）：弃权阈值失效（streak 不涨）
    b4 = FakeProc([1], spawn_t=0.0, streak=streak4)
    nb2, act2 = supervise_board(b4, lambda: FakeProc([None], spawn_t=5.0),
                                lambda m: logs.append(m), now=5.0)
    t4b = nb2 is None and act2 == "giveup" and any("BOARD-GIVEUP" in m for m in logs)
    results["T4 看板子死重起/连快死>3弃权（撤阈值必红）"] = t4a and t4b

    # T5 子环境按 host 推导：两册同文件口径（WSL /mnt/c 与 Windows C:\ 指同一块盘同一文件）。
    saved = {k: os.environ.pop(k) for k in
             ("DISPATCH_HB", "DISPATCH_CODE_ROOT", "DISPATCH_PROJ", "DISPATCH_PS") if k in os.environ}
    try:
        ce = child_env()
    finally:
        os.environ.update(saved)
    exp_root = os.path.dirname(os.path.dirname(team_home()))
    exp_hb = os.path.join(exp_root, ".cc", "state", "dispatcher.heartbeat")
    if fault == "env":
        exp_hb = os.path.join(exp_root, ".cc", "state", "WRONG.heartbeat")   # 偏差注入（期望面）
    ok5 = (ce["DISPATCH_CODE_ROOT"] == exp_root
           and ce["DISPATCH_HB"] == exp_hb
           and os.path.basename(ce["DISPATCH_PROJ"]) == "projects")
    results["T5 子环境推导 DISPATCH_HB/CODE_ROOT 随台账家（漂移必红）"] = ok5

    # T5b 判据宿主 PATH 含 node（03:1x 假红第二例：任务 PATH=注册表 system+user 无 nodejs，
    #     board-collapse DOM 桩环内必红、交互必绿）。手法=换任务形 PATH **现调** child_env——
    #     任何终端跑自测都等效任务现场；撤 child_env 的补 node 段必红；fault=env 同路注红。
    ok5b = True
    _p_save = os.environ.get("PATH")
    try:
        if os.name == "nt" and os.path.exists(r"C:\Program Files\nodejs\node.exe"):
            os.environ["PATH"] = _task_path_sim()
            _ce2 = child_env()
            ok5b = shutil.which("node", path=_ce2.get("PATH")) is not None
            if fault == "env":
                ok5b = False
    finally:
        os.environ["PATH"] = _p_save
    results["T5b 子环境 PATH 含 node（任务形 PATH 现调，撤补即红）"] = ok5b

    # T5c 判据宿主 bash 钉死=Git Bash（03:4x 毒轮三卡连坐实锤第三例假红：任务形 PATH 下
    #     which("bash.exe") 命中 C:\Windows\System32\bash.exe=WSL 启动器，双态判据守卫
    #     NOT-WIN-HOST 被火车交卷即验误判真红→REJECT 循环）。手法=与 T5b 同任务形 PATH
    #     现调 child_env，三断言：①钉出值存在（没钉不猜派）②不在 %SystemRoot% 下
    #     ③真跑 `uname -s` 命中 MINGW/MSYS/CYGWIN（红绿同源，不比对路径字符串）。
    #     撤 System32 排除段必红；fault=env 同路注红。
    ok5c = True
    _p_save2 = os.environ.get("PATH")
    _cvb_save = os.environ.pop("CARD_VERIFY_BASH", None)   # 现场无关：两径自造，不赖环境
    try:
        if os.name == "nt":
            os.environ["PATH"] = _task_path_sim()
            _sr2 = (os.environ.get("SystemRoot") or r"C:\Windows").lower()
            _ce3 = child_env()                             # 径①无显式钉值（which 命中面）
            os.environ["CARD_VERIFY_BASH"] = os.path.join(_sr2, "system32", "bash.exe")
            _ce4 = child_env()                             # 径②继承显式毒值（03:5x 现证面）
            for _ce in (_ce3, _ce4):
                _b = _ce.get("CARD_VERIFY_BASH") or ""
                ok5c = ok5c and bool(_b) and os.path.isfile(_b) and \
                    not os.path.normpath(_b).lower().startswith(_sr2)
            if ok5c:
                import subprocess as _sp
                _r = _sp.run([_ce3.get("CARD_VERIFY_BASH"), "-c", "uname -s"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=NO_WIN)
                ok5c = _r.returncode == 0 and (_r.stdout or "").strip().upper().startswith(
                    ("MINGW", "MSYS", "CYGWIN"))
            if fault == "env":
                ok5c = False
    finally:
        os.environ["PATH"] = _p_save2
        if _cvb_save is None:
            os.environ.pop("CARD_VERIFY_BASH", None)
        else:
            os.environ["CARD_VERIFY_BASH"] = _cvb_save
    results["T5c 判据宿主 bash 钉死 Git Bash（任务形 PATH 现调+uname 实测非 WSL；撤排除段=红）"] = ok5c

    # T6 每日环对时（业主 2026-11-03「定时任务不要用操作系统定时，一个常驻 Python 进程就够了」：
    #    原 cc-sv-patrol 每日 03:30 OS 任务收进 svcheck 环后，仍须准点而非起进程就抢跑）。
    #    带 at 的环：首拍 = now + 距下次 HH:MM 秒数（当日未到=等当日，已过点=滚明天）；
    #    不带 at 的环：首拍 = 0（即刻）。撤对时（fault=daily：init_schedule 拒绝对齐）必红。
    base = time.mktime(time.strptime("2026-11-03 02:00:00", "%Y-%m-%d %H:%M:%S"))
    d1 = {"name": "sv", "every": 86400, "at": "03:30", "argv": []}
    d2 = {"name": "sv", "every": 86400, "at": "03:30", "argv": []}
    pln = {"name": "p", "every": 60, "argv": []}
    init_schedule([d1, d2, pln], now=base, align_at=(fault != "daily"))
    init_schedule([d2], now=base + 9000, align_at=(fault != "daily"))   # 04:30 → 明天 03:30
    ok6 = (d1["next_due"] == base + 5400          # 02:00→当日 03:30（+90min）
           and d2["next_due"] == base + 9000 + 82800   # 04:30→次日 03:30（+23h）
           and pln["next_due"] == 0.0)            # 普通环即刻
    results["T6 每日环 @HH:MM 对时（当日等/过点滚明天；撤对时=抢跑必红）"] = ok6

    # T7 同组拒发→记欠账→空位补放+组内让位（BUG-CTRLD-SWEEPSTARVE-1 纯决策面）：
    #    f1(跑12s)/f2(跑3s) 同脚本组（带 "script" 键=走 loop_core 公平轮到路径）、每 10s 同锚
    #    ——饱和形：f1 需求>节拍，若只「记欠账+先到先得」，空位永远被 f1 抢回（G 案实证同缺），
    #    必须「更久未跑者先上」才解。假执行器组内一次只容一活；拒发新码返回 None，
    #    --fault=starve 走旧「跳拍即丢」legacy 排期（busy 返 0.0 被记成跑过=现网 12:36:51 后
    #    sweep 15min 零拍的形）。46 虚拟拍断言：两环都有真跑（f1≥4、f2≥3）、低优先环不饿死
    #    （f2≥0.6×f1）、永不叠跑。撤任一环（欠账/让位）f2=0 必红。
    g1 = {"name": "f1", "script": "g.py", "every": 10, "timeout": 999, "next_due": 0.0}
    g2 = {"name": "f2", "script": "g.py", "every": 10, "timeout": 999, "next_due": 0.0}
    gt = {"t": 0.0}
    gact = {"name": None, "until": 0.0}
    gdur = {"f1": 12.0, "f2": 3.0}
    gruns = []                                   # (name, start_t) 真跑才记

    def grun(r):
        if gact["name"] is not None:
            return 0.0 if fault == "starve" else None
        gact["name"], gact["until"] = r["name"], gt["t"] + gdur[r["name"]]
        gruns.append((r["name"], gt["t"]))
        return 0.0

    def _legacy_core(rings, clock, run):         # 撤修复形（仅 starve 注入用，不进现网路径）
        now = clock()
        for rr in rings:
            if now < rr["next_due"]:
                continue
            spent = run(rr) or 0
            rr["next_due"] = max(rr["next_due"] + rr["every"], now + spent)

    for _t in range(0, 46):
        gt["t"] = float(_t)
        if gact["name"] is not None and gt["t"] >= gact["until"]:
            gact["name"] = None                  # 虚拟「组收线」：环归位放
        (_legacy_core if fault == "starve" else loop_core)([g1, g2], lambda: gt["t"], grun)
    c1 = sum(1 for n, _s in gruns if n == "f1")
    c2 = sum(1 for n, _s in gruns if n == "f2")
    _segs = sorted([(s, s + gdur[n]) for n, s in gruns])
    ok7 = (c1 >= 4 and c2 >= 3 and c2 >= 0.6 * c1
           and all(_segs[i][1] <= _segs[i + 1][0] for i in range(len(_segs) - 1)))
    results["T7 同组拒发记欠账+空位补放+让位轮到不饿死（--fault=starve 走旧丢拍形必红）"] = ok7

    import shutil as _sh
    _sh.rmtree(home, ignore_errors=True)
    n_bad = sum(1 for v in results.values() if not v)
    for name, v in results.items():
        print("%s %s" % ("PASS" if v else "FAIL", name))
    tag = "ALL-GREEN" if n_bad == 0 else "HAS-RED(%d)" % n_bad
    print("CTRLD_SELFTEST %s fault=%s" % (tag, fault or "-"))
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    # pythonw 常驻形无 sys.stdout——不接 devnull 则任何 print 炸 AttributeError 连累主环。
    # 记账权威本在 log_line/文件，控制台输出只是便利品，可弃。
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    ap = argparse.ArgumentParser(prog="ctrld.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="loop",
                    choices=["loop", "run-once", "install", "status", "reap"])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--shadow", action="store_true", help="影子=只报不动账（迁移对账用）")
    g.add_argument("--live", action="store_true", help="正式=三环真动手（切流后）")
    ap.add_argument("--with-board", action="store_true", help="同组带起看板 status-web")
    ap.add_argument("--host", choices=["auto", "windows", "linux", "macos"], default="auto")
    ap.add_argument("--start", action="store_true", help="install 后立即 Start（首启）")
    ap.add_argument("--max-ticks", type=int, default=None, help="loop 跑 N 拍退出（观察窗用）")
    ap.add_argument("--selftest", action="store_true", help="红绿锁：全注入不碰真账")
    ap.add_argument("--fault", default=None, choices=[None, "schedule", "argv", "hb", "board", "env", "daily", "starve"],
                    help="selftest 故障注入（守卫红得起来自证用，撤了必绿）")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(_selftest(a.fault))
    if a.cmd == "status":
        sys.exit(status())
    if a.cmd == "reap":
        n = _reap_orphans(team_home())
        print("REAP n=%d" % n)
        sys.exit(0)
    if a.cmd == "install":
        sys.exit(install(a.host, a.live, a.with_board, a.start))
    shadow = not a.live                        # 缺省=shadow：手滑裸跑也不动账（动账须显式 --live）
    print("ctrld mode=%s（显式 --live 才动账）" % ("shadow" if shadow else "live"))
    if a.cmd == "run-once":
        run_once(shadow)
    else:
        loop(shadow, with_board=a.with_board, max_ticks=a.max_ticks)
