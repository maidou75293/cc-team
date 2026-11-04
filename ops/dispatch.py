#!/usr/bin/env python3
# cc-team/ops/dispatch.py — graph 原生最小派发器 v3（MECH-DISP-V3，业主 2026-11-01 令「先复活」）。
# 一分钟一 tick：engine.tick()（MECH-ENG1 决策面，只读）出 claimable → cards_dag 写闸 claim
#   → 组 brief（卡面 title/desc/verify 全文+纪律尾） → run-cc.ps1（起会话核，原样复用不改）起 worker
#   → 续心跳 .cc/state/dispatcher.heartbeat（看板 PMQ-DEAD-1 红灯唯一数据源，续上即熄）。
# 明确不读 .cc/tasks/*.md：该账 10-31 随 MECH-UX1/PURE1 退役，旧 dispatcher 整链路 md 原生——
#   原样复活=账本裂脑复发（17 张正账卡零 md 文件，一张都派不动）。旧看门狗保持 Disabled。
# 保险丝：DISPATCH_CAP（默认 6）——REQ-POOL-ELASTIC-1 起**只当「最大不超过」的保险丝，
#   不当配额**（业主 2026-11-02 18:5x 定版「要多少是业务需求，性能控制并发就行了」；
#   缺省 2→6 与现网 systemd 显式值同调；停=systemctl --user stop cc-dispatch）；
#   --once 单趟；--dry 只决策不 claim 不起会话不建桌不写心跳（dry 写心跳=假绿，禁止）；
#   --redauto 单跑一轮 DONE 红卡复跑（自测/手动催办：不派卡不写心跳）。
#   并发唯一闸门=资源闸（REQ-POOL-ELASTIC-1，取代旧 MEMGATE 固定地板在**派发侧**的整轮拦截）：
#   放行新增工人条件=空闲内存 ≥ 单工实测成本（滚动中位数**自学习**，起标 2560MB）且
#   在岗数 < DISPATCH_CAP；放不下的逐张点名（pool-gate.json blocked + log），圈首喊
#   MEMGATE-SKIP 保旧排锚。旧地板 env MEMGATE_MIN_FREE_MB 派发侧退役（配额时代产物），
#   recycle.py 同面报警（MEMGATE-WARN）沿用不动。探针在 ops/memgate.py（host_free_mem_mb
#   含 MEMGATE_FORCE_MB 伪读数锚），池体在 ops/pool.py（FIFO/成本学习/建收桌/闸纯函数）。
#   红绿锁：--selftest（伪水位 stub 全链，不碰正账/不起会话/心跳与池账写临时 POOL_HOME=tmp）；
#   池专项锁 scripts/e2e/pool-elastic-test.py。
# 弹性工位池集成（REQ-POOL-ELASTIC-1）：lanes=["ANY"] 卡走 engine.pool_ready 全局 FIFO，
#   有空位即派——工位=动态工作树 wf-slot-<n>（git worktree add 秒级建桌，claim 拒/起班败
#   即收 fresh 桌防幽灵；交卷转正与躺平桌由 recycle sweep 环收桌，见 ops/pool.py collect_round）。
#   命名道先行（具名 lanes=同文件族显式互斥声明，语义逐字不变），卡级去重=同一 cid 一圈一桌。
# MECH-BOARD-LOGBTAIL-1（业主 2026-11-02 02:0x 令「运行日志必须关联到 DAG 卡节点」）：
#   起班后把 worker 会话 id 经写闸 bind 通道回进卡运行记录（见「会话日志锚」一节）；dry 一律不绑。
# STALLED 回收不在此自造：cron ops/recycle.py --apply 每 10 分钟已跑（graph 原生，会释放超时在制）。
# MECH-REDAUTO-1（补50残账）：done 当场 auto_verify 红（或开关未启）后卡停留 DONE，
#   红转绿此前只能等 PM 批 verdict/修复卡碰巧（REG-2 人肉复跑实锤）——本器每 REDAUTO_EVERY 圈
#   对 DONE 红卡**轮转**复跑一张卡面判据（锁外跑，走 cmd_verdict 进锁重载+cmd_sha 换痕防护，与
#   verdict 补50 同纪律）；绿=瞬转 VERIFIED（verdict 痕 by=redauto@dispatcher），
#   红=卡留 DONE 且 logs/redauto.tail 续痕；红卡龄由 ops/recycle.py 上报。在制(CLAIMED)卡不打扰。
# MECH-FLEET-ALARM-1（业主 2026-11-02 令）：tick 末连看「ready 积压 × 无主活道」，连续 2 拍
#   即写 ALARM IDLE-WARN 进 dispatch.log + alerts 告警面 + 看板被拒页签（细节见 idle_scan 处）。
# BUG-SPAWN-TRACE-1（2026-11-02 实锤两例）：起会话进程 stdout/stderr 全进 DEVNULL=起班即死无死因可查
#   （18:35 m10 / 18:47 m2 双例：pid 双册查死、转录零新起、占道到人工放卡）——① spawn_runner 输出
#   重定向 logs/runner-<道>-<时刻>.log；② 每拍对在制卡尸检：起班 pid win+wsl 双册都查无 且 该道无新
#   转录 → dispatch.log 记 WORKER-DIED（卡/道/pid/死因日志）并立刻 release 回队，不等 recycle
#   10 分钟兜底窗；③ --selftest 加红绿锁（留痕落盘/即死检出/活或起班成功不误杀；原编 T5-T7，
#   与主干 REQ-REG-HOST T5/T6 撞号，整合后让号 T7-T9——尸检双册探针随 REQ-BEAT-ANCHOR-1 名牌制
#   从 recycle.py 退役，本文件自带 _ns_* 探针承担旁证，不触 #76 退役锁与名牌唯一权威）。
# 测试锚（生产不设=原路径不变）：DISPATCH_HB 心跳文件、DISPATCH_CODE_ROOT 车队根（wf-<lane> 所在）、DISPATCH_PROJ 会话转录目录、DISPATCH_BIND_WAIT 起班后找会话的有界等待秒数、DISPATCH_DEAD_GRACE_S 尸检起班宽限秒数。
# REQ-REG-HOST-1（2026-11-02 跨平台第二步，接口预备零功能变化）：起会话处 spawn_runner 改为按泳道
#   读 registry.json 名册的 host/launcher 分派——本机 run-cc.ps1 一支已实现；远程道 host=ssh:<addr>
#   形态占位与未知 launcher=接口留白（响亮 raise→tick 的 LAUNCH-FAIL 通道，不 claim 不脏账；
#   现在不写远程实现防镀金，fleet 铺机时再补支）。名册不可读/无该道=回退默认本机 ps，
#   旧纯字符串道目同样认。判活不在本卡面改：verify/recycle 已按 BEAT 卡走账本事件，跨主机自然可用。
#   REQ-CTRL-PLAIN-1 补两枚同纪律测试锚：DISPATCH_PS / DISPATCH_RUNNER——控制面单进程 ctrld.py 在
#   Windows 原生侧起派发环时需指本机 powershell（/mnt/c 前缀在 win python 不可达）；缺省值不动，
#   WSL 现网行为逐字不变。
# BUG-LANE-ZOMBIECLAIM-1（2026-11-04 业主点名看板成片「未绑定会话」黄字，严重）：死道重认领
#   三规——①活性闸 lane_dead_gate：道被确死（spawn-death/unbound-timeout 通道 release）后冷却窗内
#   且无新名牌续写⇒不发牌不起班不 claim（改投活道或留队；死主赖道刷锁面 ts/epoch 从此断路）；
#   ②unbind_watch：kind=lane 在制卡超窗未绑会话锚且本任期无名牌⇒release 回 OPEN 重排记痕，
#   同圈卡级去重（BUSY-CARD，claim 成功即写 inflight）杜绝「卡 CLAIMED 后仍每分钟重试 claim」；
#   ③锁面死主重写=直接判 zombie 接管，见 ccards/locks.lane_lock_acquire（活性三信号单源
#   locks.lane_owner_dead：活 pid/活 beat/活绑定全灭才算死）。红绿锁 tests/dispatch-lanebinding-test.py。
# 整合修（2026-11-02）：三区并集时 wm5 的 import datetime / import glob 两件被主干单行
# import 吞掉——discover_session 的 glob（selftest #85「会话发现失灵」）与 backfill_binds 的
# datetime（#85「补绑失灵」BACKFILL-BIND-ERR NameError）运行期才炸，py_compile 全绿拦不住。
# 按字母序并入原行。MECH-INT-OPS5-1：re/shutil 升模块级——尸检双册探针（_ns_*）名归一与
# powershell 定位要用，同样是「运行期才炸」教训，py_compile 之前先过 import 面。
import argparse, contextlib, datetime, glob, io, json, os, re, shutil, subprocess, sys, time
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

OPS = os.path.dirname(os.path.abspath(__file__))
TEAM = os.path.dirname(OPS)                                    # cc-team 根（WSL 视角 /mnt/c/...）
sys.path.insert(0, OPS)                                        # ops 同目录件（memgate）
sys.path.insert(0, TEAM)
import memgate                                                 # MECH-MEMGATE-1 水位探针（同面 recycle.py 亦 import 它）
import pool                                                    # REQ-POOL-ELASTIC-1 弹性工位池（FIFO/资源闸/建收桌）
from ccards import config as _cfg                              # CC_TEAM_HOME 感知的账本路径
from ccards import alerts as _AL
from ccards import events as _EV                               # REQ-SOP-CONFIRM-1 R2：SOP-MISS 机读行进 events 流
from ccards import locks as _locks                             # BUG-LANE-ZOMBIECLAIM-1 活性三信号（lane_owner_dead/lane_beat_read 单源）
GATE = [sys.executable, "-I", os.path.join(TEAM, "cards_dag.py")]
# REQ-OSS-ENGINE-1：五锚重落 ccards/config 单入口（REQ-OSSPATH-1 定版件被 11-02 13:16
# 合并 66a19cff 回退成字面量的修复；主干常驻锁 tests/oss-release-check.py 防再回退）。
# 取值序=专用 env 锚 > config.toml 文件层 > 内置默认（默认=现网值，逐字等价见证据文）。
PS = _cfg.CC_PS                                        # 起会话 powershell（nt 随 PATH；WSL 随盘根派生）
RUNNER = os.environ.get("DISPATCH_RUNNER") or _cfg.CC_RUNNER   # 起会话核（不改它）
HB = _cfg.CC_DISP_HB                                   # 心跳锚 .cc/state/dispatcher.heartbeat（随车队根派生）
PROJ = os.environ.get("DISPATCH_PROJ") or _cfg.CC_PROJ  # 测试锚 DISPATCH_PROJ（生产不设=原路径不变）
CODE = os.environ.get("DISPATCH_CODE_ROOT") or _cfg.CC_CODE  # 车队根 wf-<lane> 所在
CAP = _cfg.num_setting("dispatch.lane_cap", "DISPATCH_CAP", 6, minimum=1)   # 保险丝帽（REQ-POOL-ELASTIC-1：只当「最大不超过」，非配额；现网 systemd 显式 6 同调）
# REDAUTO 复跑节奏（MECH-REDAUTO-1）：每 N 圈跑一张 DONE 红卡；env/配置可调（自测钉 1）。
REDAUTO_EVERY = _cfg.num_setting("dispatch.redauto_every", "DISPATCH_REDAUTO_EVERY", 10, minimum=1)
_tick_n = 0          # 进程内圈计数（--redauto 单跑不计圈：那是手动通道不是看护轮）
_redauto_cur = 0     # 轮转游标：重启归零最坏=重看一张旧卡，无害（复跑幂等，绿才落账）
# 忙判据=**台账态**（补49 BUG2，业主 2026-11-01 20:5x 令「严格按 DAG 执行」）：
#   旧 guard「30 分钟内有转录即 BUSY」是转录税——m7 交卡后空道仍被压 30 分钟，
#   ready 9 张全挤 m7 单道排队时=「车队瘫痪」观感主因。防双开由 run-cc.ps1 道锁负责；
#   会话不起/不干=卡留 CLAIMED，由 recycle cron 超时回收重派。


def trace(m):
    print("%s %s" % (time.strftime("%F %T"), m), flush=True)


def run_gate(*args):
    p = subprocess.run(GATE + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NO_WIN)
    lines = [l for l in ((p.stdout or "") + (p.stderr or "")).strip().splitlines() if l.strip()]
    return p.returncode, (lines[-1] if lines else "")


def compose_brief(card, lane, win_wd, epoch=None):
    # REQ-BEAT-ANCHOR-1 名牌制：brief 发牌（epoch=派发前 lane_epoch_advance 入账的任期号）。
    # 判活=账本事件流（心跳龄/圈号/任期三问），worker 不碰进程号；断流=死→回收重派换新班，
    # 旧班一切上报（done/note/progress）写闸侧错班拒收——僵尸乱报免疫。
    cid = card["id"]
    ep = str(epoch) if epoch is not None else "<claim 输出所示>"
    txt = ("你是泳道 %s 的 worker，一话一卡只干这一张。\n\n"
           "# 卡 %s：%s\n\n## 任务说明\n%s\n\n## 完成判据（verify，收口前逐条自查）\n%s\n\n"
           "## 纪律（正账=cards_dag 写闸，违者收不了口）\n"
           "- 名牌：本道任期 epoch=%s（判活不认进程号，只认账本续写）。**每完成一个工作步骤就续拍一笔**"
           "——≤5 分钟是天花板不是保险垫（BUG-POOL-SLOTCHURN-1 频度规，业主 11-03 定版）："
           "领牌即首拍；编译/测试/长命令等工具链**开跑前先拍一笔、跑完再拍**；"
           "ring=圈号本班从 1 起逐笔 +1 必须递增（圈号持平连圈=判死线，比心跳龄先到，别赌窗）；"
           "断流=死→回收重派，重派后旧班作废）：\n"
           "  python -I cc-team/cards_dag.py progress %s '<干了什么一句>' --lane %s --epoch %s --ring <圈号>\n"
           "- 进度沉淀：python -I cc-team/cards_dag.py note %s --owner %s --text '<一句>' --epoch %s\n"
           "- 完工：先在**本工作树** git commit（消息格式 动词(%s): 一句），再\n"
           "  python -I cc-team/cards_dag.py done %s --owner %s --repo '%s' --commit <hash> --evidence <路径> --epoch %s\n"
           "  （done 会自动拉起卡面判据复跑：绿=瞬间落 VERIFIED；红=卡留 DONE 如实报，"
           "修完重 done，或等 dispatcher 例行复跑(REDAUTO 轮转自动转正)/Verifier）\n"
           "- 只动本道工作树；看板/正账以外文件/别人模块共享文件不碰；密钥不打印；中文 UTF-8，python 带 -I\n"
           % (lane, cid, card.get("title", ""), card.get("description", ""),
              card.get("verify", ""), ep, cid, lane, ep, cid, lane, ep, cid, cid, lane, win_wd, ep))
    d = os.path.join(TEAM, "logs")
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, "brief-%s-%s.txt" % (lane, time.strftime("%H%M%S")))
    with open(fn, "w", encoding="utf-8") as f:
        f.write(txt)
    return fn, _cfg.to_win(TEAM) + "\\logs\\" + os.path.basename(fn)  # ps1 侧一律 Windows 路径


# ---------- MECH-BOARD-LOGBTAIL-1：会话日志锚（业主 2026-11-02 02:0x 令） ----------
# 病根：bind 通道（cli.py bind --id --session，MECH-S1 现成）自 v3 派发器上线以来一直闲置——
# 派出的卡 session_id 恒 "-"，看板点开卡无从把运行日志关联到 DAG 卡节点（业主点名「不是泳道页」）。
# 发现=**确定性匹配**而非「猜最新」：本道项目目录
#   <会话转录根>/C--Users-<宿主用户>-code-wf-<lane>/*.jsonl 里（目录名=config.proj_dir_name 现算，
#   编码=工作树绝对路径非字母数字全换 '-'），首行=claude -p 开新会话
#   落的 queue-operation enqueue，其 content **原样是派发 brief**，同时含「# 卡 <cid>」与
#   「你是泳道 <lane>」双锚才认；mtime≥起班时刻（-5s 容差）排除 recycle 重派的历史残渣。
#   subagent 支线/他卡会话没有这对双锚，绑不上去（防串锚=防假绿）。
# 时机：起会话+claim 成功后有界等待找一次（DISPATCH_BIND_WAIT 默认 15s，找不到不拦派发主链）；
#   其后每拍对「CLAIMED 且 claim.json 仍无锚」的卡在制名单补探一次（bind 覆写幂等，命令现成）。
# 缺锚不遮：没绑上=看板台账亮黄字「缺锚」（规格③），与本文件上方 BUG-DISPATCH-PID-1 的**进程锚**
#   互补——那个钉「道上的活人」，这个钉「卡读哪份运行日志」。


def _proj_dir(lane):
    # 转录目录名=本道工作树路径编码（config.proj_dir_name 单源；Windows 形
    # encode(<车队码根>\wf-<lane>)，生产值与旧硬前缀逐字相等。自测换 PROJ/CODE 全局即随沙箱）。
    return os.path.join(PROJ, _cfg.proj_dir_name(CODE, lane))


def discover_session(cid, lane, since):
    """只读扫描一次：返回匹配本卡双锚的会话 id，无= None。since=起班 epoch 下限。"""
    best, best_mt = None, 0.0
    for f in glob.glob(os.path.join(_proj_dir(lane), "*.jsonl")):
        try:
            mt = os.path.getmtime(f)
            if mt < since - 5:
                continue
            with open(f, "rb") as fh:
                head = fh.read(8192).decode("utf-8", "ignore")
            ln = head.split("\n", 1)[0]
            if '"queue-operation"' not in ln or '"content"' not in ln:
                continue                       # 只认 -p 主会话开场行（subagent 支线的开场不是它）
            if ("# 卡 %s" % cid) not in ln or ("你是泳道 %s" % lane) not in ln:
                continue                       # 卡号+道名双锚：内容对得上才算本卡会话
            stem = os.path.basename(f)[:-6]
            sid = stem
            try:
                sid = json.loads(ln).get("sessionId") or stem
            except ValueError:
                pass
            if mt > best_mt:                   # 同双锚多文件（重派旧档仍在）=取最后写的活会话
                best, best_mt = sid, mt
        except OSError:
            continue
    return best


def bind_gate(cid, sid):
    rc, msg = run_gate("bind", cid, "--session", sid)
    if rc == 0:
        trace("BOUND %s session=%s（日志锚进卡运行记录，看板运行尾面板即有米）" % (cid, sid))
    else:
        trace("BIND-拒 %s：%s（不拦主链：看板缺锚黄字+下拍补绑兜底）" % (cid, msg))
    return rc == 0


BIND_WAIT = float(_cfg.num_setting("dispatch.bind_wait_s", "DISPATCH_BIND_WAIT", 15, minimum=0))


def bind_after_launch(cid, lane, t0):
    """起班即找：有界等待窗内探测+bind；超时没找到=如实放行（tick 补绑兜底）。"""
    deadline = time.time() + max(0.0, BIND_WAIT)
    while True:
        sid = discover_session(cid, lane, t0)
        if sid:
            return bind_gate(cid, sid)
        if time.time() >= deadline:
            trace("BIND-WAIT %s：%.0fs 未见会话转录（缺锚黄字+每拍补绑兜底）" % (cid, BIND_WAIT))
            return False
        time.sleep(min(3.0, max(0.1, deadline - time.time())))


# ---------- BUG-BIND-DEADPATH-1：BIND-MISS 连拍升级（不许无限 WAIT） ----------
# 病灶（2026-11-04 夜实锤：当日 BIND-WAIT 91 次、零 BIND-OK、全部 CLAIMED 缺锚）：起会话核失踪/
# 转录根漂移时会话从未落盘，补绑每拍探测每拍静默 miss——起班只喊一次 15s BIND-WAIT，此后再无人
# 升级=缺锚卡永远挂黄字，而 recycle/dispatch 尸检按「无新转录」判 WORKER-DIED=误杀风暴随时复发。
# 闸形（与 MECH-FLEET-ALARM-1 同族）：补绑拍给每张建连拍账，连续 ≥BIND_MISS_STREAK 拍仍无转录可绑
# = ALARM BIND-MISS 走 alarm_emit 三面落笔（键=恒定文案防打地鼠，时距不进键→alerts 同键聚合），
# 其后每 IDLE_REPEAT 拍重申；绑上/离账=清连拍。RUNNER-MISSING 缺位闸（BUG-RUNNER-GONE-1）已拦
# 「起不成不脏账」于上游，本闸兜的是「账上已 CLAIMED 却永远绑不到转录」这类漂移/死路径。
BIND_MISS_STREAK = int(_cfg.num_setting("dispatch.bind_miss_streak",
                                         "DISPATCH_BIND_MISS_STREAK", 5, minimum=2))
BIND_STREAK = os.path.join(_cfg.STATE, "bind-streak.json")


def _bind_streak_load():
    try:
        d = _cfg.read_json(BIND_STREAK) or {}
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _bind_streak_save(d):
    try:
        _cfg.atomic_write_json(BIND_STREAK, d)
    except OSError as e:
        trace("BIND-STREAK-SAVE-FAIL %r" % (e,))       # 旁证层炸不断补绑主链


def backfill_binds(claimed):
    """每拍补绑：在制 CLAIMED 卡里 session_id 仍 "-" 的，各做一次只读探测。claimed={卡:道}。
    + BUG-BIND-DEADPATH-1 连拍账：连续无转录可绑到阈=ALARM BIND-MISS（见上注释块）。"""
    streak = _bind_streak_load()
    alive = set()
    for cid, lane in sorted((claimed or {}).items()):
        try:
            d = _cfg.read_json(os.path.join(_cfg.CLAIMS, cid, "claim.json")) or {}
            if (d.get("session_id") or "-") != "-":
                streak.pop(cid, None)          # 已有锚=清账（防旧连拍残留误升级）
                continue
            alive.add(cid)
            ts = str(d.get("ts") or "")
            try:
                t0 = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except ValueError:
                t0 = 0.0                       # 无 ts=不设下限，仍靠双锚内容匹配把关
            sid = discover_session(cid, lane, t0)
            if sid:
                bind_gate(cid, sid)
                streak.pop(cid, None)          # 探得到=解困（bind 拒与否另有 BIND-拒 响铃）
            else:
                n = int(streak.get(cid) or 0) + 1
                streak[cid] = n
                if n >= BIND_MISS_STREAK and (n - BIND_MISS_STREAK) % IDLE_REPEAT == 0:
                    alarm_emit("ALARM BIND-MISS %s@%s：CLAIMED 连拍无会话转录可绑"
                               "——查 RUNNER 与转录根；尸检按无转录判死中，防误杀风暴"
                               % (cid, lane))
        except Exception as e:
            trace("BACKFILL-BIND-ERR %s：%r" % (cid, e))
    for cid in [c for c in streak if c not in alive]:
        streak.pop(cid)                        # release/归档=离账，防连拍表漂移
    _bind_streak_save(streak)


# ---------- REQ-SOP-CONFIRM-1 R2：起而未签 SOP-MISS 升级（派了≠领了，不无限 WAIT） ----------
# 病灶（业主 seq=34 S2/seq=35① 拍板）：起会话成迹但 bind 未落=CONFIRM 签收痕（R1，见
# ccards/commands.py cmd_bind）缺位——「账上 CLAIMED 却无人知道道到底接没接手」。与 BIND-MISS
# 并存不互替：那面测「无转录可绑」（连拍账），这面测「起而未签」（签收可见）。
# 判据：kind=lane 的 CLAIMED 卡，超 env SOP_CONFIRM_TIMEOUT_MIN（缺省 10；config 键
# sop.confirm_timeout_min，PM 可裁）仍无卡面 confirm 痕=MISS。三面落笔：①trace→dispatch.log
# （lane/epoch/龄全量详情）；②events 机读行 SOP-MISS card=<cid> lane=<道> epoch=<N>（每
# (卡,任期)一行，state/sop-miss.json 防惊群，形同 bind-streak）；③ccards.alerts.record
# 稳定键 SOP-MISS card=<cid>——键名严禁「业主/FUSE-S6」头（S6 跳自触发键=静默丢报，
# CLAIM-COUNTER R6 同纪律）；每拍续记同键聚合 count++，停铃后 autoack 自然退账。升级=
# 走既有 S6 闭环（≥30min 唤 PM 班、≥60min 直达信箱），本闸不自造叫醒通道。
# 只做签收可见，不做处置（MISS 卡处置归 fuse/PM；无 beat 的绑不上锚卡由 unbind_watch 回收，
# 账随 release 离账清零）。kind=session=PM 直做卡无 dispatcher 签收通道=不判（同 unbind_watch
# 口径，防手工卡误报「起而未签」）。
SOP_CONFIRM_TIMEOUT_MIN = _cfg.num_setting("sop.confirm_timeout_min", "SOP_CONFIRM_TIMEOUT_MIN",
                                           10, minimum=1)
SOP_MISS_STATE = os.path.join(_cfg.STATE, "sop-miss.json")


def _sop_age_min(iso):
    """claimed_at（nowz 形=ISO8601 带冒号偏移，config.nowz 定版）→ 已龄分钟。
    不可解析=None=不判（宁漏不误，同 unbind ts 坏口径）；裸时间（无偏移）按本机时区算，不猜未来。"""
    s = str(iso or "")
    dt = None
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
    return max(0.0, (time.time() - dt.timestamp()) / 60.0)


def sweep_confirm_miss(claimed):
    """每拍对 CLAIMED 在制卡核签收账一遍（claimed={卡:道}=engine.claimed 原样）。
    只读账+三面落笔，绝不动卡；alerts 每拍记（同键 count++=连发聚合），events 行每
    (卡,任期)只出一遍。"""
    try:
        acct = _cfg.read_json(SOP_MISS_STATE)
        acct = acct if isinstance(acct, dict) else {}
    except (OSError, ValueError):
        acct = {}
    try:
        cards = (_cfg.read_json(_cfg.GRAPH) or {}).get("cards") or {}
    except (OSError, ValueError) as e:
        trace("SOP-SWEEP-READ-FAIL %r（graph 不可读=本圈不判，下圈重试）" % (e,))
        return
    alive = set()
    for cid, lane in sorted((claimed or {}).items()):
        try:
            c = cards.get(cid) or {}
            if c.get("status") != "CLAIMED":
                continue
            d = _cfg.read_json(os.path.join(_cfg.CLAIMS, cid, "claim.json")) or {}
            if str(d.get("kind") or "lane") != "lane":
                continue                         # PM 直做卡无 dispatcher 签收通道=不判
            age = _sop_age_min(c.get("claimed_at"))
            if age is None or age < SOP_CONFIRM_TIMEOUT_MIN:
                continue                         # 没法计时/未超阈=不判
            alive.add(cid)
            if c.get("confirm"):
                acct.pop(cid, None)              # 迟到签收=退账（报警键停铃后自然 autoack）
                continue
            ep_s = str(c.get("claimed_epoch")) if c.get("claimed_epoch") is not None else "-"
            if acct.get(cid) != ep_s:
                acct[cid] = ep_s
                trace("SOP-MISS %s@%s e%s：CLAIMED 起而未签 %.0fmin（CONFIRM 痕缺位）——"
                      "查 bind 通道/R1 三件缺位 WARN；处置归 fuse/PM，升级走 S6 既有闭环"
                      % (cid, lane, ep_s, age))
                try:
                    _EV.event(cid, "CLAIMED", "CLAIMED", "dispatcher",
                              "SOP-MISS card=%s lane=%s epoch=%s" % (cid, lane, ep_s))
                except OSError as e:
                    trace("SOP-MISS-EVENT-FAIL %s：%r（events 旁证炸不断主链）" % (cid, e))
            try:
                _AL.record("SOP-MISS card=%s" % cid)   # 稳定键=卡片身份，R6 纪律非业主/FUSE-S6 头
            except Exception as e:
                trace("ALARM-RECORD-FAIL %r" % (e,))   # 旁证层失败绝不断派发主链（alarm_emit 同式）
        except Exception as e:
            trace("SOP-SWEEP-ERR %s：%r（单卡炸不拦整拍）" % (cid, e))
    for cid in [x for x in acct if x not in alive]:
        acct.pop(cid)                            # release/重派未超阈=离账清账，防漂移（bind-streak 同式）
    try:
        _cfg.atomic_write_json(SOP_MISS_STATE, acct)
    except OSError as e:
        trace("SOP-MISS-SAVE-FAIL %r" % (e,))    # 旁证层炸不断下圈签收核对


# ---------- REQ-SOP-CONFIRM-1 R3：业主消息催办 +1 超编临时槽（seq=34 S2 末项最小形） ----------
# 语义：催办痕在场时，弹性池上限**本圈** +1 临时槽=一次超编加派判定；零改判活三层权威，
# 零改 pool.admission 纯函数——只在 dispatch 调用点动 cap 参。
# 催办痕唯一现件源=inbox_watchdog 现账 state/inbox-watch.json（last_n>0=业主消息未 ack 在场）；
# 「一次」账=state/sop-nudge.json：同催办签名（last_n|last_sig）授过即不再加（痕常驻≠永久超编），
# 签名变=新催办=再授一圈。dry 圈只判不消费痕账。读不到账=0（保守，不扰正常派发节奏）。
SOP_NUDGE_STATE = os.path.join(_cfg.STATE, "sop-nudge.json")
INBOX_WATCH = os.path.join(_cfg.STATE, "inbox-watch.json")


def owner_nudge_bonus(grant=True):
    """1=本圈 cap +1 临时槽（催办痕在场且本签名未授过）；0=照常。grant=False 只判不落账（dry）。"""
    try:
        w = _cfg.read_json(INBOX_WATCH) or {}
        n = int(w.get("last_n") or 0)
    except (OSError, ValueError, TypeError):
        return 0
    if n <= 0:
        return 0
    sig = "%s|%s" % (n, w.get("last_sig") or "")
    try:
        st = _cfg.read_json(SOP_NUDGE_STATE)
        st = st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        st = {}
    if st.get("granted_sig") == sig:
        return 0                                 # 该催办痕已授过=回归常规队列
    if grant:
        try:
            _cfg.atomic_write_json(SOP_NUDGE_STATE, {"granted_sig": sig, "at": _cfg.now()})
        except OSError as e:
            trace("SOP-NUDGE-SAVE-FAIL %r（账不落=下圈再授，宁多授一圈不误催办）" % (e,))
    return 1


# ---------- MECH-FLEET-ALARM-1：ready 积压 × 无主活道 连看警报 ----------
# 病根（2026-11-01 深夜实锤）：四条道闲置 + 7 张 ready 全钉 m7 堵死数小时，dispatch 每分钟
# SKIP/BUSY/CAP/GATE-拒各喊各的、从不连看 = PM 失职无兜底。
# 判据（每拍 tick 末评估）：backlog（有队头且本圈没派出去的卡）非空 且 idle（无主活道：
# 盘上有 wf-<lane> 工作树、台账无在制、无队头、本圈未派）非空，**连续 2 拍**才出警
# （单拍=正常错峰，连拍=车队真堵）；持续则每 IDLE_REPEAT 拍重申一次——防 alerts.py
# 开头的病根「每分钟正确报警、永远正确地被忽略」复发。消息附卡 id 与可用道，PM 一眼
# 判「改道 or 建道」。三处落笔：①trace→dispatch.log（systemd stdout 落盘）；②alerts
# 告警面（recycle 同面，处置 cards_dag ack）；③rejects.log→看板被拒页签同现。
# 连拍计数落 state/idle-streak.json：进程重启/单趟 --once 都不丢连看（健康拍清零）。
IDLE_NEED = 2
IDLE_REPEAT = 10
STREAK = os.path.join(_cfg.STATE, "idle-streak.json")


def _fleet_lanes(claimable):
    """车队 = 台账队列道 ∪ 盘上 wf-<lane> 工作树（无队列文件的活道也是道）。
    弹性工位（wf-slot-<n>，REQ-POOL-ELASTIC-1）**不算道**：桌随卡开收、池桌躺平由 recycle
    sweep 收桌点名，混进「无主活道」会把池桌误报成『请建道』——池的候派积压看 pool-gate。"""
    lanes = set(claimable)
    try:
        lanes |= {d[3:] for d in os.listdir(CODE) if d.startswith("wf-")
                  and not d[len("wf-"):].startswith(pool.SLOT_PREFIX)}
    except OSError:
        pass
    return lanes


def idle_scan(claimable, inflight, dispatched, blocked):
    """纯函数（selftest 可直测）：返回 (backlog=[(卡,道,因)], idle=[道])。
    inflight=engine.claimed 原样 {卡:道}，在制道取其**值**。"""
    owners = set(inflight.values())
    backlog = [(cid, lane, blocked.get(lane, "本圈未派"))
               for lane, cid in sorted(claimable.items()) if cid and lane not in dispatched]
    idle = [lane for lane in sorted(_fleet_lanes(claimable))
            if lane not in owners and lane not in dispatched and not claimable.get(lane)
            and os.path.isdir(os.path.join(CODE, "wf-%s" % lane))]
    return backlog, idle


def fleet_watch(claimable, inflight, dispatched, blocked):
    """tick 末连看：满足「连续 IDLE_NEED 拍积压×闲道并存」返回警报文本，否则 None。"""
    try:
        streak = int((_cfg.read_json(STREAK) or {}).get("streak") or 0)
    except (OSError, ValueError):
        streak = 0
    backlog, idle = idle_scan(claimable, inflight, dispatched, blocked)
    if not (backlog and idle):
        if streak:
            _cfg.atomic_write_json(STREAK, {"streak": 0, "at": _cfg.nowz()})
        return None
    streak += 1
    _cfg.atomic_write_json(STREAK, {"streak": streak, "at": _cfg.nowz(),
                                    "backlog": backlog, "idle": idle})
    if streak < IDLE_NEED or (streak - IDLE_NEED) % IDLE_REPEAT:
        return None
    cards_txt = " ".join("%s@%s(%s)" % (c, l, w) for c, l, w in backlog)
    return ("ALARM IDLE-WARN：ready 积压 %d 张(%s) 与无主活道 %d 条(%s) 并存已连 %d 拍"
            "——钉错道请改道，车队不够请建道，PM 拍板"
            % (len(backlog), cards_txt, len(idle), " ".join(idle), streak))


def alarm_emit(msg):
    trace(msg)                                                 # ①dispatch.log
    try:
        _AL.record(msg)                                        # ②alerts 告警面
    except Exception as e:                                     # 旁证层失败绝不断派发主链
        trace("ALARM-RECORD-FAIL %r" % e)
    try:
        os.makedirs(_cfg.STATE, exist_ok=True)
        with open(_cfg.REJECTS, "a", encoding="utf-8") as f:   # ③看板被拒页签同现
            f.write("%s|dispatch|%s\n" % (_cfg.now(), msg.replace("\n", " ")))
    except OSError as e:
        trace("ALARM-REJECTLOG-FAIL %r" % e)

def _redauto_mark(cid, errtext):
    """红续痕：logs/redauto.tail 一行 `ts|cid|err尾300`——PM 复核与看板红卡龄侧的原始凭证。
    落盘走台账家（CC_TEAM_HOME 优先，沙箱测试不污染代码树）；写失败只 WARN 不断复跑轮。"""
    from ccards import config as _cfg
    p = os.path.join(_cfg.TEAM, "logs", "redauto.tail")
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s|%s|%s\n" % (time.strftime("%F %T"), cid,
                                    " ".join(errtext.split())[-300:]))
    except OSError as e:
        trace("REDAUTO-MARK-FAIL %s：%r（痕没落成，判定本身不变）" % (cid, e))
    return p


def redauto_pass(dry=False):
    """MECH-REDAUTO-1 一轮：在 DONE 卡（判据停留态=红卡池）里**轮转**挑一张复跑。
    只碰 DONE——OPEN/CLAIMED 不是复跑对象（在制卡不打扰；判据红也绝不静默改账）。
    复跑=进程内调 cmd_verdict PASS：套件长执行在台账写锁**外**、进锁重载卡面+cmd_sha
    换痕防护（补50 原纪律，不自造第二套落账通道）。绿=卡面 VERIFIED+verdict 痕；
    红=留 DONE、tail 续痕。cwd 钉主树（判据是相对路径，复跑语义=「修复合入后是否转绿」；
    DISPATCH_REDAUTO_CWD 可覆盖，沙箱自测用）。"""
    global _redauto_cur
    from ccards import commands as _cmd
    from ccards import config as _cfg
    from ccards import graph as _g
    reds = sorted(cid for cid, c in _g.all_cards().items() if c.get("status") == "DONE")
    if not reds:
        trace("REDAUTO: DONE 红卡=0，本圈无账可复跑")
        return
    cid = reds[_redauto_cur % len(reds)]
    _redauto_cur += 1
    if dry:
        trace("REDAUTO-DRY：会复跑 %s（DONE 红卡 %d 张轮转，本圈轮到它；dry 不落账不写痕）" % (cid, len(reds)))
        return
    trace("REDAUTO：复跑 %s（DONE 红卡 %d 张轮转，判据锁外跑）" % (cid, len(reds)))
    cwd = os.environ.get("DISPATCH_REDAUTO_CWD") or os.path.dirname(_cfg.TEAM)
    ns = argparse.Namespace(id=cid, result="PASS",
                            reason="redauto@dispatcher 例行复跑绿（MECH-REDAUTO-1）",
                            as_role=None, reverify=False)
    old = os.getcwd()
    err = io.StringIO()
    try:
        try:
            os.chdir(cwd)
        except OSError as e:
            trace("REDAUTO %s SKIP：复跑宿主目录不可达 %r(%r)——宁跳过不误跑" % (cid, cwd, e))
            return
        with contextlib.redirect_stderr(err):
            _cmd.cmd_verdict(ns)
        trace("REDAUTO %s 绿→VERIFIED（锁内重载对账落章，verdict 痕=redauto@dispatcher）" % cid)
    except SystemExit as e:
        # die 语义（判据红/执行窗内被 REJECT-释放/换痕拒）：rc≠0=红，卡留 DONE 如实喊；
        # rc=0 的 SystemExit 按成功收尾（闸内正常出口不该出现，防御性归类）
        if e.code and e.code != 0:
            p = _redauto_mark(cid, err.getvalue())
            trace("REDAUTO %s 红：复跑仍不过——留 DONE；痕→%s；龄由 recycle 报，等修复卡合入下轮自动转绿"
                  % (cid, os.path.basename(p)))
        else:
            trace("REDAUTO %s 绿→VERIFIED（rc0 出口，同上语义）" % cid)
    except OSError as e:
        trace("REDAUTO %s 不可跑(%r)——留痕待 PM 处置，不静默" % (cid, e))
    finally:
        os.chdir(old)
# ---------- REQ-REG-HOST-1：按名册 host/launcher 分派起会话（跨平台接口预备） ----------
# 名册 cc-team/registry.json projects[].lanes 条目={lane,host,launcher}：
#   host=道会话所在宿（本机 win/wsl；远程 ssh:<addr> 形态占位，未铺机）；
#   launcher=起会话核种类（现仅 run-cc.ps1 一支）。远程支**接口留白**=本卡不写实现（防镀金），
#   命中未实现形态时响亮拒绝，绝不静默按本机猜起。
LOCAL_HOSTS = ("win", "wsl")                                   # 已实现支的宿主名集合
DEFAULT_ENDPOINT = {"host": "win", "launcher": "run-cc.ps1"}   # 名册缺目/读不到=现行为回退


def registry_lanes():
    """只读名册→{lane: {host, launcher}}。每次现读不缓存（防陈旧快照，同 graph 短命读纪律）；
    坏文件/缺字段=空表或默认补，派发主链永不因名册炸。旧纯字符串道目同样认=回退默认本机 ps。"""
    try:
        data = json.load(open(os.path.join(TEAM, "registry.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for proj in (data.get("projects") or []):
        for ln in (proj.get("lanes") or []):
            if isinstance(ln, str):
                out[ln] = dict(DEFAULT_ENDPOINT)
            elif isinstance(ln, dict) and ln.get("lane"):
                out[str(ln["lane"])] = {"host": str(ln.get("host") or DEFAULT_ENDPOINT["host"]),
                                        "launcher": str(ln.get("launcher") or DEFAULT_ENDPOINT["launcher"])}
    return out


def lane_endpoint(lane):
    """泳道名册条目；无目=默认本机 run-cc.ps1（零功能变化）。"""
    return registry_lanes().get(lane) or dict(DEFAULT_ENDPOINT)


def spawn_supported(ep):
    """纯判据（selftest 直测不碰 subprocess）：名册条目是否命中本机已实现 ps 支。
    此判若退化成恒真=「无视名册照样起会话」的空检查，比没检查更糟——T5 红自证。"""
    return (str(ep.get("launcher") or "") == "run-cc.ps1"
            and str(ep.get("host") or "") in LOCAL_HOSTS)


# ---------- BUG-SPAWN-TRACE-1：起会话留痕 + 每拍尸检（2026-11-02） ----------
# 病根实锤：旧 spawn_runner 把 powershell 的 stdout/stderr 全接 DEVNULL——起班即死的死因
#   （内存挤死/ps1 报错/PATH 缺 node）永远无据可查，只能靠人脑补。先留痕再谈修：
#   ① 输出重定向 logs/runner-<道>-<时刻>.log（日志开不出来=退 DEVNULL 照起班+喊
#      RUNNER-LOG-FAIL，留痕坏了不拦派发生命线）；
#   ② 尸检在 tick 头对在制 CLAIMED 卡执行：**身份=进程名字，不用进程号**（业主 2026-11-03
#      定版「不允许用进程号，进程名字就是岗位编号+名称」）——启动器 run-cc.ps1 给每个
#      worker 的命令行注入 CCLANE=<道号>-<岗位名> 标记，尸检只问「本道有没有带名字的人」
#      （_census_lanes：Get-CimInstance 一查 claude.exe）。旧「Win+WSL 双册 pid 名实对拍」
#      （_ns_* 族/BUG-ANCHOR-NS-1）整族删除：舰队=全 Windows（11-03 切流令），WSL 侧无工人
#      可查，双册=WSL 时代遗骸；pid 只留在 claim.json 当留档，不参判。且 _proj_dir(lane)
#      起班后无新转录（会话真起来过必写 jsonl；ps1 包装退场≠worker 死，有转录就不杀）
#      → WORKER-DIED 行入 dispatch.log（卡/道/死因日志路径）+ 立刻 release
#      回队头，本圈腾出的槽位即可重派，不等 recycle 10 分钟兜底窗；
#      尸检**自带于本文件**：REQ-BEAT-ANCHOR-1 名牌制已把 recycle.py
#      的进程表判定退役（selftest #76 退役锁钉 recycle 非注释行零进程探测），尸检旁证不再
#      import recycle——只问「本道在不在人」，不碰判活唯一权威 lane_alive；
#   ③ 宁漏不误杀同 recycle 纪律：任一册 alive 或 unknown=跳过（释放失败下拍重试）；
#      DEAD_GRACE_S（默认 60，DISPATCH_DEAD_GRACE_S 调）防「起会话→进程落册/转录落地」窗内误杀。
DEAD_GRACE_S = float(_cfg.num_setting("dispatch.dead_grace_s", "DISPATCH_DEAD_GRACE_S", 60, minimum=0))
Popen = subprocess.Popen          # 测试缝：_selftest 换它=T7 走**真** spawn_runner 留痕路径不真起会话
_RUNNER_LOG = {}                  # lane → [日志路径, 父侧句柄]（换道重派覆盖旧项=旧句柄随手放）

# —— 进程在场普查（业主 2026-11-03 定版「不允许用进程号，进程名字就是岗位编号+名称」）——
# 身份=worker 命令行里的名字标记 CCLANE=<道号>-<岗位名>（run-cc.ps1 经 --append-system-prompt
# 注入）。全舰队 Windows，一册即真相；旧「Win Get-Process + WSL /proc」双册 pid 探针
# （_ns_* 族，BUG-ANCHOR-NS-1/MECH-INT-OPS5-1 时代）整族删除不留兼容层——留着=旧又冒头。
def _census_lanes():
    """在场工人道号集合（claude.exe 且命令行带 CCLANE= 标记）。查询失败=None（unknown=不杀，
    宁漏不误杀）。无缓存每拍现查；pid 全程不参与判定（claim.json 里的 pid 仅留档）。"""
    try:
        r = subprocess.run(
            [PS, "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\").CommandLine "
             "| ForEach-Object { if ($_ -match 'CCLANE=([A-Za-z0-9_]+)-') { $Matches[1] } } "
             "| Sort-Object -Unique"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25, creationflags=NO_WIN)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return {x.strip() for x in (r.stdout or "").splitlines() if x.strip()}


def _popen_runner(lane, win_wd, win_brief, out_kw):
    return Popen([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", RUNNER,
                  "-Brief", win_brief, "-Workdir", win_wd, "-Lane", lane],
                 stdin=subprocess.DEVNULL, **out_kw, creationflags=NO_WIN)


def spawn_runner(lane, win_wd, win_brief):
    """起会话核 run-cc.ps1，按泳道读名册 host/launcher 选支（REQ-REG-HOST-1）；
    包一层=--selftest 能 stub 掉，红绿锁不真起会话。名册指远程 host(ssh:<addr> 占位)或
    未知 launcher=接口留白：响亮 raise 由 tick 的 LAUNCH-FAIL 通道按「未领卡、账本原地
    不动」处理——宁拒得名明白，不静默猜错机器。
    BUG-SPAWN-TRACE-1：stdout/stderr 汇入 runner-<道>-<时刻>.log——死因从此有据。
    留痕是旁路不是命脉：日志开不出/重定向起班炸（interop 层万一不兼容）=退回 DEVNULL 重试，
    Popen 抛错即子进程未落地，重试不会双开；两次都炸原样上抛=tick 既有 LAUNCH-FAIL 面。"""
    ep = lane_endpoint(lane)
    if not spawn_supported(ep):
        raise NotImplementedError("泳道 %s 名册 host=%s/launcher=%s 起会话支未实现"
                                  "（本机仅 run-cc.ps1 一支；远程待 fleet 铺机再补，REQ-REG-HOST-1）"
                                  % (lane, ep["host"], ep["launcher"]))
    # BUG-RUNNER-GONE-1 缺位闸：起会话核文件不在盘上=当场响亮 raise（进 tick 的 LAUNCH-FAIL 通道，
    # 不 claim 不脏账）。旧缝：Powershell 照起、-File 报「不存在」秒退——claim 已写、会话从未活，
    # 看板成片「未绑定会话」鬼 claim，尸检 release→重派→再空转的死循环。
    if not os.path.isfile(RUNNER):
        raise FileNotFoundError("RUNNER-MISSING 起会话核缺席：%s（engine.runner_cmd/CC_RUNNER 可改锚；"
                                "先修核再派发，宁可不派不造鬼 claim）" % RUNNER)
    d = os.path.join(TEAM, "logs")
    os.makedirs(d, exist_ok=True)
    logp = os.path.join(d, "runner-%s-%s.log" % (lane, time.strftime("%Y%m%d-%H%M%S")))
    try:
        logf = open(logp, "w", encoding="utf-8", errors="replace")
    except OSError as e:
        trace("RUNNER-LOG-FAIL %s：%r——本次起班退 DEVNULL（死因无留痕，派发不拦）" % (logp, e))
        return _popen_runner(lane, win_wd, win_brief,
                             {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL})
    try:
        proc = _popen_runner(lane, win_wd, win_brief,
                             {"stdout": logf, "stderr": subprocess.STDOUT})
    except Exception as e:
        trace("RUNNER-SPAWN-REDIR-FAIL %s：%r——重定向起班炸，退 DEVNULL 重试（留痕文件已开=%s）"
              % (lane, e, os.path.basename(logp)))
        try:
            logf.close()
        except OSError:
            pass
        proc = _popen_runner(lane, win_wd, win_brief,
                             {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL})
    _RUNNER_LOG[lane] = [logp, logf]           # 子进程经 dup 的 fd 写入；父侧持句柄即可
    return proc


def _lane_has_transcript(lane, since):
    """该道起班时刻后有无可读转录（mtime≥since-5s；历史残渣 mtime 旧=不算——双锚细判归
    discover_session，尸检只问「会话有没有起来过」这一件事）。目录缺/全读不动=False。"""
    for f in glob.glob(os.path.join(_proj_dir(lane), "*.jsonl")):
        try:
            if os.path.getmtime(f) >= since - 5:
                return True
        except OSError:
            continue
    return False


def _runner_log_for(lane):
    """死因日志定位：本进程记的句柄路径优先（存在即认），否则回退扫 logs 里该道最新
    runner-<lane>-*.log——dispatcher 重启过/重派过都还能指到最近一次起班留痕。"""
    e = _RUNNER_LOG.get(lane)
    if e and os.path.isfile(e[0]):
        return e[0]
    cands = []
    for f in glob.glob(os.path.join(TEAM, "logs", "runner-%s-*.log" % lane)):
        try:
            cands.append((os.path.getmtime(f), f))
        except OSError:
            continue
    return max(cands)[1] if cands else ""


def spawn_death_watch(inflight):
    """BUG-SPAWN-TRACE-1② 尸检：inflight={卡:道}（engine.claimed 原样）。返回已确死+释放成功的
    [(卡, 道, pid, 死因日志)]。只认「双册都 dead」；无 pid 锚卡归 recycle session 档不碰。
    探针=本文件 _census_lanes（按名字 CCLANE= 一册；不 import recycle：名牌制后 recycle 判定侧被 #76 退役锁钉死零进程探测）。"""
    died = []
    for cid, lane in sorted((inflight or {}).items()):
        try:
            d = _cfg.read_json(os.path.join(_cfg.CLAIMS, cid, "claim.json")) or {}
            pid = d.get("pid")                 # 仅留档（trace 里可查），不参与判定
            if str(d.get("kind") or "lane") != "lane" or pid is None:
                continue                       # 手工/session 档不归名字尸检，归 recycle 保守面
            try:
                t0 = datetime.datetime.strptime(str(d.get("ts") or ""),
                                                "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except ValueError:
                t0 = time.time()               # ts 坏=当刚起班看待（宽限兜底，宁漏不误杀）
            if time.time() - t0 < DEAD_GRACE_S:
                continue                       # 起班宽限窗：进程落册/开场转录未及
            # 豁免论归属（业主 2026-11-03 01:5x「不是早就取消进程了么……你怎么还是记不住」
            # 定修）：旧判据「道有新转录(mtime)就豁免」=上轮卡迟退会话续写，正好把本卡夭折
            # 豁免掉——m2 僵尸挂 13min 无人管走的就是这条缝（今晚实锤：skip-014707-22624.log
            # 与 mtime 豁免双证）。新判据三档：①本卡会话锚已绑=真起班，直接豁免；②双锚内容
            # 匹配找到本卡会话（与补绑同源）=真活过，豁免并顺手补绑；③找不到本卡会话——
            # **别人**的转录不豁免，落到双册查无即杀。探针自身炸才回退 mtime 豁免（宁漏不误杀）。
            if str(d.get("session_id") or "-") != "-":
                continue                       # 本卡已绑会话锚=起班真成功
            _sid, _disc_ok = None, True
            try:
                _sid = discover_session(cid, lane, t0)
            except Exception as e:
                _disc_ok = False
                trace("DEADWATCH-DISCOVER-ERR %s：%r（回退道转录 mtime 豁免）" % (cid, e))
            if _sid:
                bind_gate(cid, _sid)
                continue
            if not _disc_ok and _lane_has_transcript(lane, t0):
                continue
            names = _census_lanes()
            if names is None or lane in names:
                continue                       # 本道有带名字的人/普查不可核=不杀，recycle 兜底照在
            logp = _runner_log_for(lane)
            trace("WORKER-DIED %s@%s：普查无带名字进程 CCLANE=%s-*（身份=名字，pid=%s 仅留档不参判）"
                  "且道 %s 无新转录——死因留痕 %s；立刻 release 回队，不等 recycle 10min 窗"
                  % (cid, lane, lane, pid, lane, logp or "无（runner 日志未留成）"))
            rc, msg = run_gate("release", cid, "--who", "spawn-death")
            if rc != 0:
                trace("WORKER-DIED-RELEASE-拒 %s：%s（下拍重试；recycle 10min 兜底不断）" % (cid, msg))
                continue
            mark_lane_dead(lane, cid, "spawn-death")   # BUG-LANE-ZOMBIECLAIM-1 ①：确死的道进冷却
            died.append((cid, lane, str(pid), logp or ""))
        except Exception as e:
            trace("DEADWATCH-ERR %s：%r（单卡尸检炸不拦整拍）" % (cid, e))
    return died


# ---------- BUG-LANE-ZOMBIECLAIM-1：死道重认领三规（业主 2026-11-04 04:37 点名看板成片黄字） ----------
# 病根实锤（ctrld.log 04:40~04:48：`GATE-拒 claim REQ-UX-DEFAULTS-1@m9：状态 CLAIMED 不可领`
# 连刷；三张 CLAIMED 卡的道锁 meta pid 全死、session='-'、beat 停 11-03 10~22h）：
#   ①认领无泳道活性闸——死道（无活 pid/活 beat/活绑定）照样发牌起班认领，重试方每圈把
#     lane-<道>.epoch/道锁 ts 刷「活」=死主赖道、recycle zombie=0 判不走；
#   ②同一张卡钉两条具名道（lanes=[m9,m8]）同圈排队时，先道 claim 成、后道拿**陈旧快照**
#     仍发牌→起班→claim=GATE 拒+杀会话，每分钟重试刷锁面（卡级去重 v3 只做了池侧，具名道侧漏）；
#   ③僵尸 CLAIMED 占着 DISPATCH_CAP 在岗数→真工人（pool slot）领不到并发位=队列饿死。
# 三规修法（本卡规格）：
#   ①lane_dead_gate：道被「工人确死」通道 release 后写冷却账 state/lane-<道>.cool；冷却窗内
#     且道上无新名牌续写（活心跳早退）⇒ 不 claim（不发牌、不起班、不领卡；卡留队或走同卡别道）；
#   ②unbind_watch：kind=lane 在制卡超 UNBIND_TIMEOUT_S 未绑会话锚且本任期无名牌 ⇒ release 回
#     OPEN 重排（记痕 who=unbound-timeout），不再无限补绑+重派循环刷锁面；
#   ③卡级去重+认领后 inflight 即时更新：同圈一卡一道，杜绝后道重试；锁面死主重写判 takeover
#     在 ccards/locks.lane_lock_acquire（规格③「锁面 ts 只能由活主续」）。
# 红绿锁 tests/dispatch-lanebinding-test.py。测试锚 DISPATCH_LANE_COOL_S / DISPATCH_UNBIND_TIMEOUT_S
# （生产默认 600/900s，均可 env 调；最小值防 0 关闭闸假绿）。
LANE_COOL_S = float(_cfg.num_setting("dispatch.lane_cool_s", "DISPATCH_LANE_COOL_S", 600, minimum=60))
UNBIND_TIMEOUT_S = float(_cfg.num_setting("dispatch.unbind_timeout_s", "DISPATCH_UNBIND_TIMEOUT_S", 900, minimum=60))


def mark_lane_dead(lane, cid, why):
    """确死道冷却账落笔（规格①的发端）：spawn-death/unbound-timeout 两条「人死实锤」通道
    release 成功后调用。读侧 lane_dead_gate 见账拒认领；活工人续写名牌=早退冷却（自愈通道）。"""
    try:
        _cfg.atomic_write_json(_locks._lane_file("cool", lane),
                               {"at": time.time(), "ts": _cfg.now(), "cid": cid, "why": why})
    except OSError as e:
        trace("COOL-MARK-FAIL %s：%r（无冷却账=下圈照常派，recycle 兜底不断）" % (lane, e))


def lane_dead_gate(lane, now_ts=None):
    """规格①活性闸（纯判据）：True=该道现在不许领卡。条件=冷却账在场未过期
    **且** 道上没有比确死更晚的名牌续写（复活工人一笔 progress 即早退冷却）。
    不读进程号（名牌制纪律）：活性凭据=账本续写；pid 只活在道锁 meta 留档与 locks 侧三信号。"""
    try:
        cool = _cfg.read_json(_locks._lane_file("cool", lane)) or {}
    except OSError:
        return False
    if not cool:
        return False
    now = now_ts if now_ts is not None else time.time()
    try:
        at = float(cool.get("at") or 0.0)
    except (TypeError, ValueError):
        return False                             # 账坏了=不拦（同 fail-open 纪律，喊话归 recycle）
    if now - at >= LANE_COOL_S:
        return False                             # 冷却窗过：允许再试（道可能已被修活）
    beat = _locks.lane_beat_read(lane) or {}
    try:
        bt = float(beat.get("t") or 0.0)
    except (TypeError, ValueError):
        bt = 0.0
    if bt > at:
        return False                             # 确死之后有活心跳=道复活，早退冷却
    return True


def unbind_watch(inflight):
    """规格②：CLAIMED 超时未绑定→回收重排（release 回 OPEN+记痕），禁无脑重试刷锁面。
    只动 kind=lane 的派发型认领（手工/session 卡无 runner 锚、天生不绑，归 recycle 保守面）；
    本任期有名牌续写（beat.epoch==claim.epoch 且 t 晚于认领）=工人活着只是绑不上锚，不打扰。
    返回已回收 [(卡, 道)]——调用方从 inflight 摘除并冷却该道。"""
    died = []
    for cid, lane in sorted((inflight or {}).items()):
        try:
            d = _cfg.read_json(os.path.join(_cfg.CLAIMS, cid, "claim.json")) or {}
            if str(d.get("kind") or "lane") != "lane":
                continue
            if str(d.get("session_id") or "-") != "-":
                continue                         # 已绑=有锚，在制不打扰
            try:
                t0 = datetime.datetime.strptime(str(d.get("ts") or ""),
                                                "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except ValueError:
                continue                         # ts 坏=没法计时，宁漏不误杀
            if time.time() - t0 < UNBIND_TIMEOUT_S:
                continue                         # 未超窗：bind/backfill 通道还在（那是绑锚不是刷锁面）
            beat = _locks.lane_beat_read(lane) or {}
            try:
                if (int(beat.get("epoch") or -1) == int(d.get("epoch") or -2)
                        and float(beat.get("t") or 0.0) > t0):
                    continue                     # 本任期在续写名牌=活工人慢绑定，豁免
            except (TypeError, ValueError):
                pass
            trace("CLAIMED-UNBOUND %s@%s：认领超 %.0fs 未绑会话锚且本任期无名牌续写——"
                  "release 回 OPEN 重排+道冷却（不再让「重试认领循环」刷锁面 ts/epoch，规格②）"
                  % (cid, lane, UNBIND_TIMEOUT_S))
            rc, msg = run_gate("release", cid, "--who", "unbound-timeout")
            if rc != 0:
                trace("CLAIMED-UNBOUND-RELEASE-拒 %s：%s（recycle beat-stale 兜底照在，下拍只读重试不刷锁面）"
                      % (cid, msg))
                continue
            mark_lane_dead(lane, cid, "unbound-timeout")
            died.append((cid, lane))
        except Exception as e:
            trace("UNBIND-WATCH-ERR %s：%r（单卡炸不拦整拍）" % (cid, e))
    return died


def _pool_release(slot, why):
    """即收新桌（起班失败/claim 拒）：桌刚建在瞬、必净，force 兜底；桌账记 collected 留痕。
    收失败只喊不抛——幽灵桌由 recycle sweep 躺平判据兜收（双保险）。"""
    try:
        st = pool.desks()
        rec = (st.get("desks") or {}).get(slot)
        if not rec:
            rec = {"card": "", "dir": pool.desk_dir(CODE, slot), "status": "occupied"}
            st.setdefault("desks", {})[slot] = rec
        how, err = pool.remove_desk(rec.get("dir") or pool.desk_dir(CODE, slot),
                                    code=CODE, force=True)
        rec.update(status="collected", collected=time.strftime("%F %T"),
                   collect_why="%s(%s)" % (why, how))
        st.setdefault("history", []).append({"slot": slot, "card": rec.get("card") or "",
                                             "why": why, "how": how, "at": rec["collected"]})
        st["history"] = st["history"][-200:]
        pool.save_desks(st)
        if how == "failed":
            trace("POOL-即收失败 %s：%s｜%s（sweep 躺平判据兜收，不默毁）" % (slot, why, err))
    except Exception as e:
        trace("POOL-RELEASE-ERR %s：%r" % (slot, e))


# ---------- 模型归口（业主 2026-11-03 03:2x 批准：「匹配由 dispatch 来做，它调用大模型裁决」） ----------
# 只管「无牌卡」：OPEN 且 lanes 为空、或整串都是名册外幽灵道。有牌卡照声明派，模型零介入
# （显式声明>裁决；防每拍烧 token）。答复必须命中名册道号才落账（走 set 写闸）；
# 名册外/读不懂/模型炸=不猜派，ALARM ROUTE 按卡聚合=「待归口」，卡原样留账给 PM。
CLAUDE_EXE = os.environ.get("DISPATCH_CLAUDE_EXE") or os.path.join(
    os.environ.get("APPDATA", ""), "npm", "node_modules", "@anthropic-ai",
    "claude-code", "bin", "claude.exe")
ROUTE_N = int(os.environ.get("DISPATCH_ROUTE_N", "1"))
ROUTE_TIMEOUT = int(os.environ.get("DISPATCH_ROUTE_TIMEOUT", "150"))


def roster_roles():
    """{道号: 岗位名}：岗位册优先 state/registry.json（含 role），并上 host/launcher 册。
    两册都缺=空表（无册不裁决——防拿空名册把卡派到虚空）。"""
    roles = {}
    try:
        d = json.load(open(os.path.join(TEAM, "state", "registry.json"), encoding="utf-8"))
        for ln in (d.get("lanes") or []):
            if isinstance(ln, dict) and ln.get("lane"):
                roles[str(ln["lane"])] = str(ln.get("role") or "")
    except (OSError, ValueError):
        pass
    for lane in registry_lanes():
        roles.setdefault(lane, "")
    return roles


def _ask_model(prompt):
    """一发一答的纯文本裁决（不带工具不碰账）。异常上抛由调用方归因，这里不吞。"""
    if not os.path.exists(CLAUDE_EXE):
        raise RuntimeError("claude.exe 不在场: %s" % CLAUDE_EXE)
    r = subprocess.run([CLAUDE_EXE, "-p", prompt],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=ROUTE_TIMEOUT, cwd=CODE, creationflags=NO_WIN)
    if r.returncode != 0:
        raise RuntimeError("模型 rc=%s %s" % (r.returncode, (r.stderr or "")[:80]))
    return r.stdout or ""


def route_unrouted(dry=False):
    """归口分工（业主 2026-11-03 批准件；五点整改收口版）：无牌/幽灵牌 OPEN 卡问模型，
    **答复原样交校验器** set --lanes——道名在册与否只由校验器裁决（ccards.commands.
    _check_lanes），本侧不复刻名册比对、不自造正则规范。收=记 ROUTE 落账；拒/空/模型
    炸=记 ROUTE-REJECT/ROUTE-ERR + ALARM（同键按卡聚合），卡原样留账等 PM，绝不猜派。
    每拍≤ROUTE_N 张封顶成本；有牌卡不进名单；dry（影子）只记候归口不喂模型不动账。"""
    from ccards import graph as _graph
    from ccards import alerts as _alerts
    roles = roster_roles()
    if not roles:
        return 0                          # 无册不裁决：防拿空名册问出虚空答复
    try:
        cards = _graph.all_cards()
    except OSError:
        return 0
    todo = []
    for cid, c in sorted(cards.items()):
        if not isinstance(c, dict) or c.get("status") != "OPEN":
            continue
        ln = [str(x).strip() for x in (c.get("lanes") or [])]
        if ln and ("ANY" in ln or set(ln) & set(roles)):
            continue                      # 有牌（含显式 ANY 池）——不归模型管
        todo.append((cid, "空牌" if not ln else "幽灵牌=" + ",".join(ln)))
    table = "；".join("%s=%s" % (k, v or "无名册注记") for k, v in sorted(roles.items()))
    n = 0
    for cid, why in todo[:ROUTE_N]:
        c = cards[cid]
        if dry:
            trace("DRY-ROUTE %s(%s) 候归口（影子不喂模型不动账）" % (cid, why))
            continue
        prompt = ("岗位名册（道号=岗位名）：%s。这张卡该归哪个岗？只回一行、"
                  "只写道号，别的字一个都不要。\n卡号：%s\n标题：%s\n描述：%s"
                  % (table, cid, str(c.get("title") or "")[:160],
                     str(c.get("description") or "")[:800]))
        try:
            ans = (_ask_model(prompt) or "").strip()[:40]
        except Exception as e:
            trace("ROUTE-ERR %s(%s)：模型不可用 %r——待归口" % (cid, why, e))
            _alerts.record("ALARM ROUTE %s: 归口未成(详见 dispatch.log)——待归口" % cid)
            continue
        if not ans:
            trace("ROUTE-REJECT %s(%s)：模型空答复——不动账待归口" % (cid, why))
            _alerts.record("ALARM ROUTE %s: 归口未成(详见 dispatch.log)——待归口" % cid)
            continue
        rc, msg = run_gate("set", cid, "--lanes", ans)
        if rc == 0:
            trace("ROUTE %s(%s)→%s 校验器收账" % (cid, why, ans))
            n += 1
        else:
            trace("ROUTE-REJECT %s(%s)：答复 %r 校验器拒收：%s——不猜派待归口"
                  % (cid, why, ans, str(msg)[:80]))
            _alerts.record("ALARM ROUTE %s: 归口未成(详见 dispatch.log)——待归口" % cid)
    return n


def tick(once=False, dry=False):
    from ccards import engine
    t = engine.tick()
    cards = t.get("claimable") or {}
    inflight = dict(t.get("claimed") or {})
    pool_ready = list(t.get("pool_ready") or [])
    graph = json.load(open(_cfg.GRAPH, encoding="utf-8"))["cards"]
    # BUG-SPAWN-TRACE-1② 尸检在**资源闸计数前**：确死的卡立刻 release，从 inflight 摘除后
    # 本圈在岗数即变小、腾出的并发位当圈重派（18:35 m10 案=占道到人工放卡；dry 零写不尸检
    # ——release 是写账动作）。REQ-POOL-ELASTIC-1 后 slots=CAP-len(inflight) 固定地板退役，
    # 尸检收益经 admission(len(inflight)) 照达——两侧语义在此合流（MECH-INT-OPS5-1）。
    if not dry:
        for _dead_cid, _, _, _ in spawn_death_watch(inflight):
            inflight.pop(_dead_cid, None)
        # BUG-LANE-ZOMBIECLAIM-1 规格②：超时未绑定的僵尸 CLAIMED 回收重排（在资源闸计数前，
        # 摘掉僵尸后在岗数即变小，被饿死的 pool 卡当圈就能领位——规格③的饿死面根治）。
        for _ub_cid, _ in unbind_watch(inflight):
            inflight.pop(_ub_cid, None)
    owners = set(inflight.values())
    dispatched, blocked = set(), {}
    # —— 命名道先预检（具名 lanes=同文件族显式互斥声明，原语义逐字不变）——
    named = []
    for lane in sorted(cards):
        cid = cards[lane]
        if not cid:
            continue
        if cid in inflight:
            # BUG-LANE-ZOMBIECLAIM-1 规格②卡级去重（实锤：04:4x GATE-拒 claim
            # REQ-UX-DEFAULTS-1@m9 连刷——同卡钉 [m9,m8] 两道，m8 先领后 m9 仍拿陈旧快照
            # 发牌→起班→claim=拒+杀会话每分钟重试）。一圈一卡一道：在制（含本圈刚领走）不重试。
            trace("BUSY-CARD %s：已在制（owner=%s），道 %s 不重复认领" % (cid, inflight[cid], lane))
            blocked[lane] = "卡已在制"
            continue
        if not os.path.isdir(os.path.join(CODE, "wf-%s" % lane)):
            blocked[lane] = "无工作树"
            trace("SKIP %s(%s)：道 wf-%s 无工作树，候派" % (cid, lane, lane))
            continue
        if lane in owners:
            trace("BUSY %s：道 %s 台账在制（CLAIMED），本圈不派" % (cid, lane))
            blocked[lane] = "道台账在制"
            continue
        if lane_dead_gate(lane):
            # BUG-LANE-ZOMBIECLAIM-1 规格①活性闸：死道不 claim（不发牌不起班不领卡——
            # 发牌入账就是「重试方写活锁面 ts/epoch」，recycle zombie=0 判不走的根源）。
            # 卡留队等道复活，同卡别的具名道/改投 ANY 由 PM 定；冷却窗过自动放行再试。
            trace("DEAD-LANE %s(%s)：道确死后冷却窗内且无新名牌续写=无活 pid/活 beat/活绑定——"
                  "不认领（发牌/起班/claim 全不触发），卡留队候道复活或改投" % (cid, lane))
            blocked[lane] = "死道冷却"
            continue
        named.append((lane, cid))
    # —— 并发唯一闸门=资源闸（REQ-POOL-ELASTIC-1；取代旧固定地板整轮拦截）——
    # 放行新增工人条件：空闲内存 ≥ 单工实测成本（滚动中位数自学习，起标 2560MB）
    # 且 在岗数 < DISPATCH_CAP（保险丝）。候派=命名道预检过 + 池 FIFO 全局队列，命名先行；
    # 卡级去重：同一 cid 一圈一桌（inflight 已含的池卡不入名单）。
    free_mb = memgate.host_free_mem_mb()
    cost = pool.cost_now()
    pending = [cid for _, cid in named] + [cid for cid in pool_ready if cid not in inflight]
    # REQ-SOP-CONFIRM-1 R3：业主催办痕在场=本圈 +1 超编临时槽（每签名一次；非 dry 才落消费账）
    cap_eff = CAP + owner_nudge_bonus(grant=not dry)
    if cap_eff != CAP:
        trace("SOP-NUDGE +1：业主消息催办痕在场，本圈弹性池临时超编槽 cap %d→%d"
              "（pool-gate.json 现值=生效帽；一次加派判定，零改判活链）" % (CAP, cap_eff))
    adm = pool.admission(free_mb, cost, len(inflight), cap_eff, pending)
    allow_ids = set(pending[:adm["allow"]])
    why_of = dict(adm["blocked"])
    if adm["allow"] == 0 and pending:
        _memor_fuse = any(w.startswith("资源闸") for _, w in adm["blocked"])
        trace("%s %s——本圈不放新工人（候派 %d），只续心跳；点名见 pool-gate.json｜同面 recycle.py 喊 MEMGATE-WARN"
              % ("MEMGATE-SKIP" if _memor_fuse else "FUSE-SKIP", adm["note"], len(pending)))
    # MECH-HEARTBEAT-1（rebase main 合并 2026-11-02）：环序以心跳裁定为准——
    # 先起会话拿真 pid→claim 必带 --pid 活锚→拒则杀会话。
    for lane, cid in named:
        if cid in inflight:
            # BUG-LANE-ZOMBIECLAIM-1 规格②派发口二次核验：预检与派发同圈，同卡钉两道时
            # 预检双双放行（当时卡不在制），先领道已把 cid 写入 inflight——后道到此必须
            # 收步：不发 epoch、不起班、不 claim（实锤=04:4x m9 重试烧 epoch+GATE-拒+杀
            # 刚起会话，每分钟刷锁面）。预检拦历史、这里拦本圈，两道闸同一口径。
            trace("BUSY-CARD %s：本圈已被道 %s 认领，道 %s 不重复派发" % (cid, inflight[cid], lane))
            blocked[lane] = "卡已在制"
            continue
        if cid not in allow_ids:
            w = why_of.get(cid, "本圈未派")
            blocked[lane] = "资源闸" if w.startswith("资源闸") else ("保险丝帽" if w.startswith("保险丝") else "本圈未派")
            trace("SKIP %s(%s)：%s" % (cid, lane, w))
            continue
        if dry:
            trace("DRY 会派：%s -> %s（wf-%s）" % (cid, lane, lane))
            blocked[lane] = "dry圈"
            continue
        _win_wd = _cfg.to_win(os.path.join(CODE, "wf-%s" % lane))  # ps1 侧 cwd=Windows 形（随 CODE 锚/配置派生）
        # REQ-BEAT-ANCHOR-1 发牌在起会话前：每次派发该道任期 +1 入账（写闸通道），
        # 牌号进 brief 给工人揣兜——done/note/progress 全靠它对班，不经账本代查。
        rc, ep_msg = run_gate("epoch", lane, "--cid", cid)
        if rc != 0:
            trace("EPOCH-FAIL %s@%s：%s（未发牌=不派发，账本原地不动）" % (cid, lane, ep_msg))
            blocked[lane] = "发牌失败"
            continue
        ep = str(ep_msg).split()[-1] if str(ep_msg).split() else ""
        if not ep.isdigit():
            trace("EPOCH-FAIL %s@%s：发牌输出不可解析(%r)——宁不派不糊账" % (cid, lane, ep_msg))
            blocked[lane] = "发牌输出腐"
            continue
        _fn, win_brief = compose_brief(graph.get(cid, {"id": cid}), lane, _win_wd, epoch=ep)
        _t_launch = time.time()  # LOGBTAIL-1：会话发现窗下限=起会话之前取时刻
        try:
            proc = spawn_runner(lane, _win_wd, win_brief)
        except Exception as e:                     # 起班失败=未领卡，账本原地不动
            trace("LAUNCH-FAIL %s：%r（未 claim，不脏账）" % (cid, e))
            blocked[lane] = "起会话失败"
            continue
        # pid 仅留档（名牌制不判活），--epoch 与台账对班=占主核验的新权威
        rc, msg = run_gate("claim", cid, "--owner", lane, "--pid", str(proc.pid),
                           "--pid-ns", "win", "--epoch", ep)
        if rc != 0:
            try:
                proc.kill()
            except OSError:
                pass
            trace("GATE-拒 claim %s@%s：%s（已杀会话防幽灵）" % (cid, lane, msg))
            blocked[lane] = "GATE拒"
            continue
        trace("DISPATCHED %s -> %s e%s（brief=%s pid=%d 留档锚 起会话留痕=%s）"
              % (cid, lane, ep, os.path.basename(_fn), proc.pid,
                 os.path.basename(_RUNNER_LOG[lane][0]) if lane in _RUNNER_LOG else "无"))
        inflight[cid] = lane                     # BUG-LANE-ZOMBIECLAIM-1 规格②：认领即对在制名单生效，
        #                                        （同圈后续道见 BUSY-CARD，不再重试 claim 刷锁面）
        # LOGBTAIL-1：claim 成立即找会话双锚走 bind 回写（有界等待，绑不上不拦主链）
        bind_after_launch(cid, lane, _t_launch)
        dispatched.add(lane)
    # —— 池桌派发（动态工位 slot-<n>；有空位即派=资源闸 allow 说了算）——
    _dry_seq = None
    for cid in pool_ready:
        if cid in inflight:
            trace("BUSY %s：池卡在制（CLAIMED），不双桌" % cid)
            continue
        if cid not in allow_ids:
            trace("SKIP %s(pool)：%s" % (cid, why_of.get(cid, "本圈未派")))
            continue
        if dry:
            if _dry_seq is None:
                _dry_seq = int(pool.desks().get("seq", 0))
            _dry_seq += 1
            trace("DRY 会开桌：%s -> slot-%d（全局 FIFO；dry 不建工作树不占号）" % (cid, _dry_seq))
            continue
        slot = wd = None
        for _try in range(3):                      # 撞号只进不退换下一号；三连撞弃本轮不幽灵
            st = pool.desks()
            cand = pool.next_slot(st)
            ok, path, err = pool.open_desk(cand, code=CODE)
            pool.save_desks(st)                    # seq 落盘（无论成败，号不回收=留痕纪律）
            if ok:
                slot, wd = cand, path
                break
            trace("POOL-撞号 %s：%s → 进下一号" % (cand, err))
        if slot is None:
            trace("POOL-建桌失败 %s：连撞 3 号（疑残桌需 git worktree prune），本圈不派" % cid)
            continue
        pool.mark_desk_opened(slot, cid, wd)
        _win_wd = _cfg.to_win(os.path.join(CODE, "wf-%s" % slot))  # 同上：池桌 cwd Windows 形
        # 名牌制对桌同样发牌：slot 名=道名进台账，任期按 lane 维度自增（MECH-INT-OPS5-1
        # 整合裁决：收桌/判活都认 owner+epoch，池桌无牌=done 必错班拒收，两侧语义必须并立）。
        rc, ep_msg = run_gate("epoch", slot, "--cid", cid)
        if rc != 0:
            trace("EPOCH-FAIL %s@%s：%s（未发牌=收新桌不派发，账本原地不动）" % (cid, slot, ep_msg))
            _pool_release(slot, "发牌失败")
            continue
        ep = str(ep_msg).split()[-1] if str(ep_msg).split() else ""
        if not ep.isdigit():
            trace("EPOCH-FAIL %s@%s：发牌输出不可解析(%r)——宁不派不糊账" % (cid, slot, ep_msg))
            _pool_release(slot, "发牌输出腐")
            continue
        _fn, win_brief = compose_brief(graph.get(cid, {"id": cid}), slot, _win_wd, epoch=ep)
        _t_launch = time.time()
        try:
            proc = spawn_runner(slot, _win_wd, win_brief)
        except Exception as e:
            trace("LAUNCH-FAIL %s@%s：%r（即收新桌防幽灵，账本原地不动）" % (cid, slot, e))
            _pool_release(slot, "起班失败")
            continue
        rc, msg = run_gate("claim", cid, "--owner", slot, "--pid", str(proc.pid),
                           "--pid-ns", "win", "--epoch", ep)
        if rc != 0:
            try:
                proc.kill()
            except OSError:
                pass
            trace("GATE-拒 claim %s@%s：%s（杀会话+即收新桌防幽灵）" % (cid, slot, msg))
            _pool_release(slot, "claim拒")
            continue
        trace("DISPATCHED %s -> %s e%s（桌=wf-%s brief=%s pid=%d 留档锚 资源闸=%s 起会话留痕=%s）"
              % (cid, slot, ep, slot, os.path.basename(_fn), proc.pid, adm["note"],
                 os.path.basename(_RUNNER_LOG[slot][0]) if slot in _RUNNER_LOG else "无"))
        inflight[cid] = slot                     # 同具名道侧：认领即在制名单生效（一圈一桌去重补强）
        bind_after_launch(cid, slot, _t_launch)
        dispatched.add(slot)
    # 本圈放行判定值快照（看板「工位池」小节唯一数字出处）+ 成本观察（dry 不学不污染，
    # 伪读数在 pool.learn 内部本就跳过；dry 写快照 mode=dry 供影子圈对账）
    pool.write_gate(adm, len(inflight), cap_eff, cost, mode="dry" if dry else "live")
    if not dry:
        pool.learn(free_mb, len(inflight))
    # LOGBTAIL-1 每拍补绑：在制卡里没绑上会话锚的各探一次（bind 幂等；dry 零写）。
    if not dry:
        backfill_binds(inflight)
        # REQ-SOP-CONFIRM-1 R2 每拍签收核对：起而未签到点=SOP-MISS 三面落笔（与补绑并排，
        # 那面测「无转录可绑」这面测「起而未签」，并存不互替；dry 零写不判）。
        sweep_confirm_miss(inflight)
    # —— tick 末车队连看（MECH-FLEET-ALARM-1）：dry 只报所见不落账，非 dry 走连拍出警三面落笔
    if dry:
        _bl, _idl = idle_scan(cards, inflight, dispatched, blocked)
        if _bl and _idl:
            trace("DRY 连看：ready 积压 %d 张(%s) 与无主活道 %d 条(%s) 并存（dry 不出警不落账）"
                  % (len(_bl), " ".join(c for c, _, _ in _bl), len(_idl), " ".join(_idl)))
    else:
        _alarm = fleet_watch(cards, inflight, dispatched, blocked)
        if _alarm:
            alarm_emit(_alarm)
    if not dry:
        try:
            os.makedirs(os.path.dirname(HB), exist_ok=True)
            with open(HB, "w", encoding="utf-8") as f:
                f.write("%s v3 live\n" % time.strftime("%F %T"))
        except OSError as e:
            trace("HB-WRITE-FAIL %r" % e)
    # 模型归口扫尾（业主 03:2x 批准件）：低水位早退到不了这里=内存紧时天然不喂模型
    # （问模型=起 claude 进程）；有牌卡不进名单，成本只在真有无牌卡时发生。
    try:
        route_unrouted(dry=dry)
    except Exception as e:
        trace("ROUTE-LOOP-ERR %r（归口炸不拦派发主链）" % e)
    # tick 末看护位（与 MECH-FLEET-ALARM-1 的 IDLE-WARN 同处埋点）：
    #   REDAUTO=判据面复跑（写账走 verdict 闸）；FLEET-ALARM=派单面告警（只写日志）。互不依赖。
    global _tick_n
    _tick_n += 1
    if _tick_n % REDAUTO_EVERY == 0:
        redauto_pass(dry=dry)
    if once:
        return
    while True:
        time.sleep(60)
        try:
            tick()
        except Exception as e:                     # 单 tick 崩≠整死：下拍自愈重试，日志留痕
            trace("TICK-ERR %r" % e)


def _selftest():
    """MECH-MEMGATE-1 红绿锁：MEMGATE_FORCE_MB 伪水位 + 全 stub（engine/claim/spawn/
    brief 都换成记账假件），不碰正账、不真起会话、心跳写临时目录。四把锁：
    T1 低水位不发单（红=闸拦得住）；T2 高水位照发单且 claim 带 --pid 活锚（绿=防
    「永拦假闸」，空检查比没检查更糟；锚断言=MECH-HEARTBEAT-1 双保险）；
    T3 探针失败 fail-open（防撑闸不能把车队闸成静默）；
    T4 低水位心跳照续（道内队列/看板数据源不断）。全绿 rc=0，任一红 rc=1。
    （BUSY 判据已按补49 改台账态 inflight，stub 不再假 lane_busy。）
    REQ-REG-HOST-1 加两把锁：T5 起会话分派按泳道读名册 launcher/host（新格式对象目、
    旧纯字符串目回退、无目默认回退三口同钉）；T6 远程 ssh:<addr> 占位支=接口留白必须
    响亮拒起（PS 临时钉不可达路径=变异摘闸时 Popen 只碰 OSError 回红，永不真起会话）。
    BUG-SPAWN-TRACE-1 追加三把锁（走**真** spawn_runner/tick 路径，只换 Popen/探针/账根假件；
    原编 T5-T7 与主干名册锁撞号，MECH-INT-OPS5-1 整合让号 T7-T9）：
    T7 起会话输出必须落 logs/runner-<道>-<时刻>.log 且非空、DISPATCHED 行指到该日志
       （把 stdout 接回 DEVNULL=此锁必红）；
    T8 起班即死检出：claim 锚 pid 双册都 dead + 道无新转录 → WORKER-DIED 行（含 pid 与
       runner 日志路径，且该文件必须真实非空=「实操对账」判据的机器形）+ release 落闸
       （拆尸检调用/拆 release/拆留痕=必红）；
    T9 不误杀双向：a) 名册报活不尸检 b) 双册死但道有新转录=会话真起来过不杀
       （拆转录旁证=必红，防把「ps1 包装正常退场」的活 worker 冤杀回队）。
    BUG-RUNNER-GONE-1 追加 T11：起会话核正本必须在仓（bin/run-cc.ps1 在盘）+ RUNNER 缺位=
       真 spawn_runner 必抛 RUNNER-MISSING 且零 Popen（摘缺位闸=红；旧缝=powershell 空转
       报「-File 不存在」秒退，claim 照写=成片鬼 claim）。"""
    import shutil, tempfile, types as _types
    saved_g = {k: globals()[k] for k in ("TEAM", "HB", "CODE", "PROJ", "Popen", "BIND_WAIT",
                                         "DEAD_GRACE_S", "trace", "run_gate", "compose_brief",
                                         "spawn_runner", "_census_lanes", "_ask_model", "RUNNER")}
    tmp = tempfile.mkdtemp(prefix="memgate-selftest-")
    os.makedirs(os.path.join(tmp, "state"))
    os.environ["POOL_HOME"] = os.path.join(tmp, "pool")   # 池三本账进临时（防污染正账 state）
    with open(os.path.join(tmp, "state", "graph.json"), "w", encoding="utf-8") as f:
        json.dump({"cards": {"SELFTEST-M1": {"id": "SELFTEST-M1", "title": "自测",
                                             "description": "", "verify": ""}}}, f)
    os.makedirs(os.path.join(tmp, "wf-st1"))               # 伪车道工作树（过 isdir 闸）
    claims, spawns, logs = [], [], []

    class _FakeProc:                     # 心跳收编后 claim 要读 proc.pid——stub 必须带 pid
        pid = 424242
        def kill(self):
            spawns.append("killed")

    globals().update(
        TEAM=tmp, CODE=tmp, PROJ=os.path.join(tmp, "proj"),
        HB=os.path.join(tmp, "hb.txt"), BIND_WAIT=0.0,   # 自测探一次即走，不付 15s 有界等待税
        trace=lambda m: logs.append(m),
        # 名牌制后派发圈=两笔闸门调用：epoch(发牌) → claim(对班领卡)；stub 末行恒发牌 7
        run_gate=lambda *args: (claims.append(args), (0, "EPOCH st1 7"))[1],
        compose_brief=lambda card, lane, win_wd, epoch=None: (os.path.join(tmp, "b.txt"), "WINBRIEF"),
        spawn_runner=lambda lane, win_wd, win_brief: (spawns.append(lane), _FakeProc())[1],
        _ask_model=lambda p: "")
    fake_eng = _types.ModuleType("ccards.engine")
    fake_eng.tick = lambda: {"claimable": {"st1": "SELFTEST-M1"}, "claimed": {}}
    import ccards
    eng_saved = getattr(ccards, "engine", None)
    ccards.engine = fake_eng
    sys.modules["ccards.engine"] = fake_eng
    saved_claims = _cfg.CLAIMS                                     # 尸检读 claim.json 的账根→沙箱
    _cfg.CLAIMS = os.path.join(tmp, "claims")
    saved_sg = (_cfg.STATE, _cfg.GRAPH)    # 归口(route)读图+告警落账=同沙箱（防自测污染正账）
    _cfg.STATE = os.path.join(tmp, "state")
    _cfg.GRAPH = os.path.join(_cfg.STATE, "graph.json")
    # BUG-LANE-ZOMBIECLAIM-1 补钉：locks 在 import 期自抓 STATE 具名锚，mark_lane_dead/
    # lane_dead_gate 走 _locks._lane_file 读写冷却账与心跳——不钉就漏进正账（实锤：2026-11-04
    # 05:19 自测跑污染主仓 state/lane-st1.cool=测试道落真账带死亡史，次跑活性闸反过来
    # 拦死自测自己的 st1，T1/T2/T7/T8 连锁红——沙箱漏钉与漏闸同等危险）。
    saved_ls = _locks.STATE
    _locks.STATE = _cfg.STATE
    try:
        os.environ["MEMGATE_FORCE_MB"] = "100"             # 远低于地板 8192
        tick(once=True)
        t1 = (not claims and not spawns
              and any("MEMGATE-SKIP" in m for m in logs))
        t4 = os.path.isfile(HB)                            # 低水位下心跳仍写了（只续不闸死）
        os.environ["MEMGATE_FORCE_MB"] = "32768"           # 高水位：必须走到 claim+起会话
        claims.clear(); spawns.clear(); logs.clear()
        tick(once=True)
        # 名牌制后发单序列=epoch→claim；claim 仍必带 --pid 留档锚（#72 静态锁口径不变），
        # 新增断言：claim 必须带 --epoch=刚发的牌（对班占主，防幽灵派发）
        _claimcall = next((x for x in claims if x[0] == "claim"), None)
        t2 = (claims and claims[0][0] == "epoch" and _claimcall and spawns == ["st1"]
              and "--pid" in _claimcall and "424242" in _claimcall     # 留档锚仍在发单参数里
              and "--pid-ns" in _claimcall
              and "--epoch" in _claimcall and "7" in _claimcall)
        os.environ["MEMGATE_FORCE_MB"] = "非数字"           # 探针炸=fail-open 放行+留痕
        blk, note = memgate.status()
        t3 = (not blk and "PROBE-FAIL" in note)
        # ---- REQ-REG-HOST-1 T5/T6：按泳道读名册 launcher 分派（TEAM 已钉沙箱，名册写沙箱）----
        with open(os.path.join(tmp, "registry.json"), "w", encoding="utf-8") as f:
            json.dump({"projects": [{"lanes": [
                {"lane": "st1", "host": "win", "launcher": "run-cc.ps1"},
                {"lane": "st9", "host": "ssh:10.0.0.9", "launcher": "run-cc.ps1"},
                {"lane": "st7", "host": "wsl", "launcher": "unknown.sh"},
                "st8"]}]}, f)
        ep1, ep9, ep7 = lane_endpoint("st1"), lane_endpoint("st9"), lane_endpoint("st7")
        ep8, epX = lane_endpoint("st8"), lane_endpoint("stX")
        t5 = (spawn_supported(ep1) and ep9["host"].startswith("ssh:")
              and not spawn_supported(ep9)                   # 远程 host 占位=未实现支
              and not spawn_supported(ep7)                   # 未知 launcher=接口留白
              and spawn_supported(ep8)                       # 旧纯字符串目=回退本机 ps
              and epX == DEFAULT_ENDPOINT                    # 名册无目=默认回退
              and set(registry_lanes()) >= {"st1", "st9", "st7", "st8"})
        # T6：真 spawn_runner 对远程支必须响亮拒。PS 临时钉不可达路径——即便有人摘掉
        # 拒起闸做变异验证，Popen 也只碰 OSError 记红，绝不真起会话（防自测放炮）。
        _ps_save = globals()["PS"]
        globals()["PS"] = os.path.join(tmp, "no-such-ps")
        try:
            saved_g["spawn_runner"]("st9", "C:\\wd", "B")
            t6 = False                                       # 走到底没拒=接口留白纪律破
        except NotImplementedError:
            t6 = True                                        # 正确：留白支拒得名明白
        except (OSError, ValueError):
            t6 = False                                       # 摸到 subprocess 才炸=根本没拦
        finally:
            globals()["PS"] = _ps_save

        # ---------- BUG-SPAWN-TRACE-1 · T7 留痕：真 spawn_runner + 假 Popen ----------
        # （名册 T5 已写沙箱 registry=st1 本机支在案，真 spawn_runner 分派闸放行后进留痕路径）
        def _fake_popen(cmd, **kw):
            out = kw.get("stdout")
            if hasattr(out, "write"):                      # 模拟子进程经继承句柄写留痕文件
                out.write("spawn ok (fake runner)\n")
                out.flush()
            spawns.append(cmd[cmd.index("-Lane") + 1] if "-Lane" in cmd else "?")
            return _FakeProc()
        globals().update(spawn_runner=saved_g["spawn_runner"], Popen=_fake_popen)  # 派发走**真**留痕路径
        claims.clear(); spawns.clear(); logs.clear()
        tick(once=True)
        rf = glob.glob(os.path.join(tmp, "logs", "runner-st1-*.log"))
        body = ""
        if rf:
            try:
                body = open(rf[0], encoding="utf-8").read()
            except OSError:
                body = ""
        t7 = (bool(rf) and "spawn ok" in body
              and any("DISPATCHED" in m and "runner-st1" in m for m in logs))
        if not t7:
            logs.append("T7明细 rf=%s body=%r logs=%s" % (rf, body[:60], logs))

        # ---------- T8 起班即死：双册 dead+无新转录 → WORKER-DIED+立刻 release ----------
        os.makedirs(os.path.join(_cfg.CLAIMS, "SELFTEST-M1"), exist_ok=True)
        with open(os.path.join(_cfg.CLAIMS, "SELFTEST-M1", "claim.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"owner": "st1", "pid": 424242, "kind": "lane", "pid_ns": "wsl",
                       "pid_comm": "powershell.exe",
                       "ts": datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")}, f)
        globals()["DEAD_GRACE_S"] = 0.0                    # 自测不等宽限窗（生产默认 60s）
        globals()["_census_lanes"] = lambda: set()   # 普查空=本道无人
        fake_eng.tick = lambda: {"claimable": {}, "claimed": {"SELFTEST-M1": "st1"}}
        claims.clear(); spawns.clear(); logs.clear()
        tick(once=True)
        died = [m for m in logs if "WORKER-DIED" in m]
        t8 = (len(died) == 1 and "424242" in died[0] and "runner-st1" in died[0]
              and rf and os.path.getsize(rf[0]) > 0        # 死因日志真实存在且非空（实操对账的机器形）
              and ("release", "SELFTEST-M1", "--who", "spawn-death") in claims
              and not spawns)                              # 死卡腾位本圈不该又派出去（无候派卡）
        if not t8:
            logs.append("T8明细 died=%s claims=%s spawns=%s rf=%s"
                        % (died, claims, spawns, rf))

        # ---------- T9 不误杀双向：a) 名册报活=不尸检 b) 双册死但道有新转录=不杀 ----------
        claims.clear(); logs.clear()
        globals()["_census_lanes"] = lambda: {"st1"}   # 本道有带名字的人
        fake_eng.tick = lambda: {"claimable": {}, "claimed": {"SELFTEST-M1": "st1"}}
        tick(once=True)
        ok9a = (not any("WORKER-DIED" in m for m in logs)
                and not any(a0 == "release" for a0, *_ in claims))
        globals()["_census_lanes"] = lambda: set()
        td = _proj_dir("st1")   # 夹具随引擎同源编码（旧硬拼 slug 在派生化后必失配）
        os.makedirs(td, exist_ok=True)
        with open(os.path.join(td, "livesession.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"sessionId": "livesession", "type": "queue-operation",
                                "content": "# 卡 SELFTEST-M1 你是泳道 st1 开场行=本卡真会话实证"},
                               ensure_ascii=False) + "\n")
        # 2026-11-03 归属化改造：豁免不再看「道的 mtime」而看「双锚内容是否本卡」——
        # 夹具随之升级为真实开场行形（旧 {} 夹具=别人家的转录，新判据下不再豁免，正确）。
        claims.clear(); logs.clear()
        tick(once=True)
        ok9b = (not any("WORKER-DIED" in m for m in logs)
                and not any(a0 == "release" for a0, *_ in claims)
                and any("BOUND SELFTEST-M1" in m or "bind" in str(claims).lower()
                        for m in logs + [str(claims)]))       # 顺手补绑=找到即锚进账
        # ---- T9c 今晚 m2 形僵尸：双册 dead + 本卡无锚 + 道上有**新但别人**的转录 → 必接住 ----
        with open(os.path.join(td, "livesession.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"sessionId": "livesession", "type": "queue-operation",
                                "content": "# 卡 OTHER-CARD 你是泳道 st1 上轮迟退会话仍在写"},
                               ensure_ascii=False) + "\n")
        claims.clear(); logs.clear()
        tick(once=True)
        ok9c = (any("WORKER-DIED" in m for m in logs)
                and ("release", "SELFTEST-M1", "--who", "spawn-death") in claims)
        t9 = ok9a and ok9b and ok9c

        # ---------- T10 模型归口（五点整改收口版）：只剩三件事——校验器收账落 set /
        # 校验器拒账不落且不猜派 / tick 尾挂点真实走过（拆挂点第三闸必红，不再 grep 源码；
        # 「册外道名必拒」本体在校验器，专测 tests/lane-book-test.py，此处不复刻）----------
        with open(os.path.join(tmp, "state", "graph.json"), encoding="utf-8") as f:
            _g10 = json.load(f)["cards"]
        _g10["SELFTEST-M2"] = {"id": "SELFTEST-M2", "title": "无牌卡", "description": "该归表管理岗",
                               "status": "OPEN", "lanes": [], "verify": "x",
                               "created_at": "2026-11-03T03:29:00+0800"}
        with open(os.path.join(tmp, "state", "graph.json"), "w", encoding="utf-8") as f:
            json.dump({"cards": _g10, "generation": 1}, f)
        globals()["_ask_model"] = lambda p: "m1"
        claims.clear(); logs.clear()
        n10 = route_unrouted()
        ok10a = (n10 == 1 and ("set", "SELFTEST-M2", "--lanes", "m1") in claims
                 and any("ROUTE SELFTEST-M2" in m for m in logs))
        if not ok10a:
            logs.append("T10a明细 n=%s claims=%s logs=%s" % (n10, claims, logs[-3:]))
        # 校验器拒收场景（模拟道名不在册→set rc≠0）：不计数、ROUTE-REJECT、告警按卡键聚合
        _rg10 = globals()["run_gate"]
        globals()["run_gate"] = lambda *ar: (claims.append(ar), (1, "道名不在岗位册"))[1]
        claims.clear(); logs.clear()
        n10b = route_unrouted()
        globals()["run_gate"] = _rg10
        try:
            alr10 = open(os.path.join(tmp, "state", "alerts.json"), encoding="utf-8").read()
        except OSError:
            alr10 = ""
        ok10b = (n10b == 0 and any("ROUTE-REJECT SELFTEST-M2" in m for m in logs)
                 and "ALARM ROUTE SELFTEST-M2" in alr10)
        # tick 尾挂点：dry tick 须现「DRY-ROUTE」——归口经真实调用路径在场为证
        claims.clear(); logs.clear()
        tick(once=True, dry=True)
        ok10c = any("DRY-ROUTE SELFTEST-M2" in m for m in logs)
        t10 = ok10a and ok10b and ok10c

        # ---------- T11 BUG-RUNNER-GONE-1：起会话核正本在仓+缺位响亮拒 ----------
        # 事故本体（2026-11-04 实锤）：起会话核驻仓外 ~/.openclaw/tmp，原生切流整树归档后三路拉起
        # （dispatch/inbox/fuse）静默空转近 6h——powershell 报 -File 不存在秒退、claim 照写、
        # 看板成片「未绑定会话」鬼 claim。两断言：①正本锚 cc-team/bin/run-cc.ps1 在盘（环境锁：
        # 删文件=红，防再次随外部目录清扫失踪）；②RUNNER 指到不存在路径=真 spawn_runner 必抛
        # RUNNER-MISSING 且 Popen 零调用（摘缺位闸=红；走到 Popen 才炸=根本没拦，同 T6 口径）。
        ok11a = (os.path.isfile(saved_g["RUNNER"])
                 and os.path.normpath(saved_g["RUNNER"]).lower().endswith(
                     os.path.normpath(os.path.join("bin", "run-cc.ps1")).lower())
                 and os.path.isfile(_cfg.CC_RUNNER))
        globals()["RUNNER"] = os.path.join(tmp, "gone", "run-cc.ps1")
        spawns.clear()
        ok11b = False
        try:
            globals()["spawn_runner"]("st1", "C:\\wd", "B")
            ok11b = False                          # 走到底没拒=缺位闸被摘
        except FileNotFoundError as e:
            ok11b = "RUNNER-MISSING" in str(e) and not spawns
        except Exception:
            ok11b = False                          # 炸错类型=拦得不明白
        t11 = ok11a and ok11b
        if not t11:
            logs.append("T11明细 ok11a=%s ok11b=%s runner=%s" % (ok11a, ok11b, saved_g["RUNNER"]))
    finally:
        globals().update(saved_g)
        _RUNNER_LOG.clear()
        _cfg.CLAIMS = saved_claims
        _cfg.STATE, _cfg.GRAPH = saved_sg
        _locks.STATE = saved_ls            # BUG-LANE-ZOMBIECLAIM-1 沙箱补钉的归位
        if eng_saved is not None:
            ccards.engine = eng_saved
        sys.modules.pop("ccards.engine", None)
        os.environ.pop("MEMGATE_FORCE_MB", None)
        os.environ.pop("POOL_HOME", None)
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all((t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11))
    for name, v in (("T1 低水位不发单", t1), ("T2 高水位照发单(防永拦假闸)", t2),
                    ("T3 探针失败fail-open", t3), ("T4 低水位心跳照续", t4),
                    ("T5 名册launcher分派(新格式/旧字符串/无目回退)", t5),
                    ("T6 远程支接口留白拒起(防镀金)", t6),
                    ("T7 起会话输出留痕(runner-*.log)", t7),
                    ("T8 起班即死WORKER-DIED+release", t8),
                    ("T9 活/有新转录不误杀", t9),
                    ("T10 模型归口(校验器收/校验器拒/tick挂点)", t10),
                    ("T11 起会话核在仓+缺位响亮拒(BUG-RUNNER-GONE-1)", t11)):
        print("%s %s" % ("PASS" if v else "FAIL", name))
    print("SPAWN-TRACE-SELFTEST %s" % ("ALL-GREEN" if all((t7, t8, t9)) else "HAS-RED"))
    print("REG-HOST-SELFTEST %s" % ("ALL-GREEN" if all((t5, t6)) else "HAS-RED"))
    print("MEMGATE-SELFTEST %s" % ("ALL-GREEN" if ok else "HAS-RED"))
    return 0 if ok else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--redauto" in a:
        redauto_pass(dry="--dry" in a)      # 单跑通道：不派卡不起会话不写心跳（假绿禁令同款）
    elif "--selftest" in a:
        sys.exit(_selftest())
    elif "--once" in a or "once" in a:
        tick(once=True, dry="--dry" in a)
    else:
        trace("dispatcher-v3 起（cap=%d，dry=%s，redauto=%d圈）" % (CAP, "--dry" in a, REDAUTO_EVERY))
        tick(once=False, dry="--dry" in a)
