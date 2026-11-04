#!/usr/bin/env python3
# recycle.py — v5 回收看护·名牌制（REQ-BEAT-ANCHOR-1，业主定版 2026-11-02 令
# 「不搞复杂，一泳道一会话」；cron 侧；release 动作**进程内**调 ccards，MECH-STD1 通道不变）。
# 判命改名牌制，弃用进程号（REQ-BEAT-ANCHOR-1）——判活两层全落账本，零 OS/CC 概念：
#   门牌 = 泳道号 + 任期号(epoch)：claim 入账即盖任期（台账 state/lane-<道>.epoch + 卡面
#          claimed_epoch）；一泳道一会话由既有道锁保证，不引入 pid/session 任何身份。
#   续写 = 进度事件流：工人经写闸通道 progress（三件必齐：道+任期+圈号递增）落
#          state/lane-<道>.beat；**断流=死**。
#   判活三问（lane_alive=唯一权威，看护只认这三条）：
#     ①任期匹配：事件必须来自本任期——旧任期来报一律不算活（重发作废旧班一切上报）；
#     ②心跳龄  ：本任期最近一次续写距今 < --minutes（默认 15≈3 个 cron 圈；
#                工人续写频次要求默认 ≤5 分钟，写卡面/brief 可调）；
#     ③圈号在涨：同任期圈号连两圈持平=死（防戳文件装活）。
#   断流 → cards_dag release <id> --who recycle，回 OPEN 等重派（回队头，新任期号自动
#   作废旧班；写闸侧 done/note/progress 错班拒收并留痕，僵尸乱报当场免疫）。
#   BUG-ANCHOR-NS-1 进程名核验**本卡落地即退役**：跨 OS 进程存在性/进程名比对的整套探测
#   函数与 powershell 查询通道全部退出判活与归属判定（selftest #76 静态锁看守其回潮）；
#   claim 仍记录 pid/pid_ns/pid_comm 仅作留档排障字段（不参与任何判定）。
#   过渡形态（同机）：无 beat 事件时退回道锁 heartbeat 文件/账本目录静默，仍按任期对班；
#   事件源接口可插拔（lane_beat_read 一处替换，判活逻辑不动，未来 worker 换主机不改本器）。
#   kind=session：PM/子会话执行卡（start 落的 kind=session，或无锁无 kind 的存量残渣——R3 事故定版：
#                 写入口命令自记 pid 必死，拿它判活=必误杀）→ 一律只 WARN 不 release；
#                 连续两轮同卡 WARN 升 ALARM（state/recycle-warn.json，仅 --apply 轮更新），PM 经 inbox 处置。
# 报警范围（BUG-REAPER-SCOPE-1，属主表 seq4 同源）：看护报警只认 CLAIMED——升 ALARM 前现读
#   卡状态复核（离制即不入账，SCOPE-SKIP），每圈再扫一遍开放警账：看护格式「ALARM <cid>: …」
#   涉及卡已非 CLAIMED（DONE/VERIFIED/CANCELED/无卡）= 僵尸警 → autoack 清账（留痕不删行，
#   复响自动复活）；图读不到/空=整轮拒清（误灭铃比不清更糟）。--sweep-only 只跑清账，
#   供存量一次性收口不动回收主体。
# BUG-LANE-SILENCE-1 在制道断流探针（实锤 2026-11-02 17:0x：m1(UTF8-1)/m10(BEAT-ANCHOR) 两泳道
#   「锚活/转录零行/工作树零写入」冻结 65+ 分钟，看板 laneact 已算出 65.4/63.7min，巡逻环
#   warn=0 静默放行——旧判活只探进程存在与 comm 名，ALIVE→不动 一路豁免，不测「工作是否在续写」）：
#   lane 卡判活后加续写探针。判据序（规格②③）：工作树文件 mtime 在动=无条件放行（防误杀：
#   长判据自测/playwright 合法零转录可 >45min）→ 新认领未足窗不抢跑 → progress 事件（state/
#   progress.log，REQ-BEAT-ANCHOR-1 在账后优先判其龄）→ 缺 progress 才降级双源=会话转录 jsonl
#   mtime（与看板 laneact/MECH-FLEET-VIS-1 同源）→ 信号龄 ≥ CARD_SILENCE_RELEASE_MIN(缺省 45)
#   且窗内无绿 done 事件（events ->DONE/->VERIFIED）=判「断流」：APPLY 自动 release(注明
#   silence-detach)+ALARM 稳定键留痕（键不含分钟值 OPT1；release 后同轮被状态迁移 autoack
#   =铃响即处置、账存 trace），DRY 只点名。任一源缺/不可读=保守放行（宁漏不误杀不破）。
#   MECH-INT-BEAT13A-1 三向整合后（本卡面②「BEAT 落地合并退役」条款兑现）：名牌续写在账的卡
#   由 lane_alive 全权判活、探针不介入；本探针降级为 progress 缺账时的备用路径，挂点收敛为两处
#   （①存量无名牌 ②名牌在而本任期 beat 缺账）——调用点哨兵针 4→2，同步于 selftest #89 与
#   lane-silence-check.py S12。常驻锁 scripts/e2e/lane-silence-check.py + selftest #89(SILENCE)/
#   #91(BEAT 名牌，卡面原 #77 避让落号)。
# BUG-POOL-SLOTCHURN-1（2026-11-03）：pool 槽连环误杀——保护形①②=beatzombie_exempt 放流前
#   第三信号闸（PM D款代落，双挂点①beat-stale②legacy-noepoch，env RECYCLE_TRANSCRIPT_GRACE_MIN
#   缺省5；红绿锁 tests/slot-churn-test.py）。本工续做③=do_release 放流成功**同轮杀旧会话**
#   防幽灵（release 不杀会话→旧桌照跑+新桌同卡再起=一卡双工，slot-21/22 实锤；dispatch 侧
#   「已杀会话防幽灵」同款语义）。认人不认牌：CCLANE=<道>- 启动器标记匹配 claude.exe，
#   pid 快照在放流**前**取=只杀当班不伤新班；env 缝 RECYCLE_SESSION_PID_CMD(%L)/RECYCLE_KILL_CMD(%P)
#   仅测试，生产走 CIM+os.kill。判活权威仍=账本事件流，本节是善后清理（#76/#91 静态锁语义零触碰，
#   杀函数与判活函数物理隔离，lane_alive 内禁字样一节不动）。根治项另落 plateau 可配：env
#   LANE_PLATEAU_DEATH 只许上调持平判死阈（缺省=下限=2，畸形回退——#91「戳文件装活」语义不可
#   击穿；针线 `if plateau >= 2:` 逐字保留=MUTA 哨兵不漂移；P 款锁三断言）。
# 残渣 GC：v3 道锁指向非 CLAIMED/属主不符 → rmtree；claims 镜像对位卡已不在 CLAIMED → 清镜像。
# v1 普通文件锁（lane-<owner>.lock 是文件）：只读作判活锚，谁建谁清，本脚本不删。
# 红卡龄（MECH-REDAUTO-1）：DONE=判据停留态，dispatcher 每 N 圈轮转复跑，绿即转正；
#   本器每轮报 DONE 卡龄（updated_at 锚）给 PM 盯账，apply 轮超阈记稳定键告警入聚合账。
# 复验宿主随判（BUG-REDAUTO-HOST-1，2026-11-02 两案实锤：13:57 PARITY-5 补跑 FAIL playwright
#   闸2 via=posix/linux——判据需 Windows 侧在位环境；15:58 DONE-SWEEP 跨侧 rc=1 cd 不存在
#   尾痕空——目录明明在=环境瞬时故障被当判据真红）：快环补跑读卡面记录的原验收宿主
#   （host 字段显式钉 或 verify_run.via 痕），该宿主不可达=本机红无驳回权；rc≠0 但尾痕空/
#   命令·路径找不到类=环境性失败，先排除再归因，一律归 done_pending_side 喊话不当 FAIL 打回。
#   首轮 auto@done 通道不动（其宿主选择在制会话，本就正确）。
# 工位池收桌（REQ-POOL-ELASTIC-1，2026-11-02 业主定版「要多少是业务需求，性能控制并发就行了」）：
#   sweep 快环在 DONE 滞留扫描后同锁串行加收桌轮（pool_round→ops/pool.collect_round）：
#   交卷转正(VERIFIED/CANCELED)桌 force 收、躺平桌（卡回 OPEN/查无此卡且桌龄超阈）净收、
#   **脏桌不毁**只点名（POOL-DIRTY 行交 PM）；台账留痕=桌账 history+对应卡 progress 旁证笔。
#   派发侧资源闸/建桌在 ops/dispatch.py+ops/pool.py，本器只管收（收桌=家务事，缺池件缺报不断主链）。
# MERGE-TRAIN 合并火车（REQ-MERGE-TRAIN-1，挂快环 --ring sweep）：
#   ①交卷即验——DONE 未合入卡本泳道立刻复验（宿主钉死 CARD_VERIFY_BASH，REDAUTO 链复用）：
#     红→REJECT 退回返工（不占合并资源）；绿→入候合队列（events 痕 stage=passed@commit）。
#   ②合并队列——队内按交工先后对「最新主干+本卡」试合沙箱复验（py_compile 改动件+本卡判据
#     +改动面锁=tests/select.sh 套件）；绿→自动 `merge --no-ff` 真并线 + verdict PASS 落
#     VERIFIED（REQ-MERGE-GATE-1 主干闸合入后自动满足）；红→打回①归因「队列复验FAIL@新主干」。
#   ③干跑 `git merge-tree --write-tree` 有冲突=integrations=blocked，看板亮「待合·撞车·龄Xm」，
#     不自动拆；②③滞留 >30min→报警行（稳定键 OPT1）。
#   ⑤生效锁（版本钉）——每圈末比对常驻件「进程起时 vs 命脉文件最后提交时刻」，进程起时早于
#     提交=NOT-ENERGIZED <件> <滞后分钟> 报警行落 dispatch.log，白名单件自动
#     systemctl --user restart（看板 AUTORELOAD 除外；cron 环每分新进程天然即时，不钉）。
#   本体见 ops/mergetrain.py；红绿锁 cc-team/tests/merge-train-check.py 五场景。
#   done_sweep_scan 跳过火车持管卡（passed/blocked/merged/refailed）——双跑防重验。
# 默认 dry-run 只报不动手；--apply 才执行；--dry 显式覆盖别处的 --apply。
# cron（MECH-STD1 改路径为 ops/，语义不变；形态=<仓根>/cc-team/ops/recycle.py，具体绝对路径随仓位置派生）:
#   每 10 分钟 --apply，输出追加 <仓根>/cc-team/logs/recycle.log（2026-11-03 起收编 cc-ctrld full 环，
#   OS crontab 已注销，见 docs/机制-全Windows切流-20261103.md）
import argparse, contextlib, hashlib, io, json, os, re, shutil, signal, subprocess, sys, time
from datetime import datetime
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

# 环内打印钉 UTF-8（GBKENC 母卡配方同款，2026-11-03 07:3x 实锤：sweep APPLY 轮打印含
# U+FFFD 的历史账串（events.log v577 行自带合法 \xef\xbf\xbd 字节）撞 cp936 控制台编码
# =UnicodeEncodeError rc=1 连拍；DRY 轮不碰该串所以手跑复现不出。只钉出口不赌 locale）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))          # cc-team/ops
ROOT = os.path.dirname(HERE)                                # cc-team 根（team_home）
sys.path.insert(0, ROOT)
from ccards import config as _cfg  # REQ-OSS-ENGINE-1：配置单入口（阈值/车队锚派生，禁用户目录字面量）
TEAM = os.environ.get("CC_TEAM_HOME") or ROOT
CLAIMS = os.path.join(TEAM, "claims")
STATE = os.path.join(TEAM, "state")
WARN_STATE = os.path.join(STATE, "recycle-warn.json")
WARN_STATE_SWEEP = os.path.join(STATE, "recycle-warn-sweep.json")   # 快环连轮分账（run_seq 不与全环交叉污染）
BEAT_OBS = os.path.join(STATE, "recycle-beat.json")                 # 名牌圈号跨圈观察账（REQ-BEAT-ANCHOR-1；仅 apply 圈写）
DONE_STATE = os.path.join(STATE, "done-sweep.json")                 # DONE 滞留：fails 计数/停跑连拍/pending_sha
SWEEP_LOCK = os.path.join(STATE, ".recycle-sweep.lock")             # 快环防重跑锁（mkdir 原子）
SWEEP_LOCK_STALE_MIN = 40.0   # 锁主死/超龄接管窗：补跑判据最长可达 CARD_VERIFY_TIMEOUT×卡数
GIT_BASH_CANDIDATES = ["/mnt/c/Program Files/Git/bin/bash.exe",
                       "/mnt/c/Program Files/Git/usr/bin/bash.exe"]

# 红卡龄告警阈（MECH-REDAUTO-1）：DONE 停留超此分钟数仍未转正→记告警（键不含分钟值，OPT1 纪律）
try:
    REDCARD_ALARM_MIN = float(os.environ.get("REDCARD_ALARM_MIN", "180"))
except ValueError:
    REDCARD_ALARM_MIN = 180.0
try:
    from ccards import alerts as _AL
except Exception:
    _AL = None
try:
    from ccards.config import SELFHEAL_MIN as ANCHORLESS_HEAL_MIN  # 留名备查：BUG-DISPATCH-PID-1 无锚自愈窗
    # REQ-BEAT-ANCHOR-1 名牌制落地后回收主循环不再引用（「断流=死」统一接管），抢锁窗仍在 locks.py 本尊
except Exception:
    ANCHORLESS_HEAL_MIN = 5
# MECH-MEMGATE-1：与 dispatch.py 同面的内存水位**报警**（探针=ops/memgate.py 共用）。
#   回收本身是救援动作，低水位不拦事只喊 MEMGATE-WARN；alerts 键用稳定文案不带
#   MB 数字（防键增殖=OPT1 老账，同题聚合 count++）。
try:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from memgate import status as _MEM_STATUS
except Exception:
    _MEM_STATUS = None
# REQ-POOL-ELASTIC-1：弹性工位池**收桌**挂 sweep 快环（SWEEP_LOCK 已串行化，不双收；
#   交卷转正 VERIFIED/CANCELED→force 收、躺平桌净收脏留点名）。容错 import：缺池件只缺报
#   不断回收主链——收桌是家务事不是救援动作。
# MECH-INT-OPS5-1：POOL 侧基线上旧 POWERSHELL_CANDIDATES 定义随名牌制(REQ-BEAT-ANCHOR-1)
#   在主干退役，不随本块复活（#76 退役锁面）；尸检双册探针归 dispatch.py 自带。
try:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import pool as _pool
except Exception:
    _pool = None


def slug(owner):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", owner or "-")


def pid_alive(pid):
    """⚠️ 仅用于**看护自身**的 sweep 防重跑锁认领（锁主=上一圈 recycle 进程，pid 即锁本体，
    与卡的生死/归属无关）。REQ-BEAT-ANCHOR-1 名牌制：卡判活权威=lane_alive 三问，
    本函数不得引入任何判活/占道路径（selftest 静态锁看守）。
    BUG-SWEEPLOCK-DEADPID-1：旧 os.kill(pid,0) 探测在 Windows **确死恒活**——查无此进程抛
    OSError(winerror=87)、受保护进程抛 SystemError，两支全被旧保守 except 整口吞 →
    sweep_lock_try 的「死主即接管」永不触发，S4 重启 kill_tree 在途后 .recycle-sweep.lock
    死主不被接管，只能等 SWEEP_LOCK_STALE_MIN=40min 到龄，火车/REDAUTO 全链停摆
    （2026-11-03 实烧 25min）。现统一委派 ccards.config.pid_alive（MECH-PIDALIVE-WIN-1
    同引擎同口径）：Win 侧 ctypes OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)+
    GetExitCodeProcess，退出码==STILL_ACTIVE(259)=活、查无此进程(2/87)=**确死必判死**、
    句柄开不到(5=受保护)/读不到退出码=「拿不到判定」仍保守活——宁漏不误杀不破的语义
    不变；POSIX 分支逐字照旧。红绿锁 cc-team/tests/sweeplock-deadpid-test.py。"""
    return _cfg.pid_alive(pid)


def silence_min(d):
    """目录内文件最大 mtime 距今分钟数；目录空/缺 → inf（最陈旧）。"""
    if not os.path.isdir(d):
        return float("inf")
    mt = 0.0
    for root, _dirs, files in os.walk(d):
        for fn in files:
            try:
                mt = max(mt, os.path.getmtime(os.path.join(root, fn)))
            except OSError:
                pass
    if mt == 0.0:
        return float("inf")
    return (time.time() - mt) / 60.0


def heartbeat_min(d):
    """显式心跳文件 mtime（dispatcher touch 的锚）；无 heartbeat 文件 → inf。
    注意：道锁的 card/meta.json 是创建锚不是心跳——刚认领的卡不该"当下已静默"。"""
    p = os.path.join(d, "heartbeat")
    if os.path.isfile(p):
        try:
            return max(0.0, (time.time() - os.path.getmtime(p)) / 60.0)
        except OSError:
            return float("inf")
    return float("inf")


def card_of(cid):
    from ccards import graph            # MECH-GRF1：事实=graph.json，graph 模块=唯一访问器
    return graph.peek(cid)


def claimed_cards():
    from ccards import graph
    out = []
    for c in graph.all_cards().values():   # 事实图优先（坏节点已被访问器点名跳过）
        if c.get("status") == "CLAIMED":
            out.append(c)
    return out


def load_claim(cid):
    try:
        j = json.load(open(os.path.join(CLAIMS, cid, "claim.json"), encoding="utf-8"))
        return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


def load_lane_lock(owner):
    d = os.path.join(STATE, "lane-%s.lock" % slug(owner))
    if not os.path.isdir(d):
        return None, {}, d
    meta = {}
    try:
        meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
        if not isinstance(meta, dict):
            meta = {}
    except (OSError, ValueError):
        pass
    try:
        cid = open(os.path.join(d, "card"), encoding="utf-8").read().strip()
    except OSError:
        cid = ""
    return (cid or None), meta, d


# ---------- 名牌判活（REQ-BEAT-ANCHOR-1；静态锁盯 lane_beat_read/lane_alive 整段） ----------
def lane_beat_read(owner):
    """事件源①=账本进度流 state/lane-<道>.beat（工人经写闸 progress 续写）。
    **可插拔**：未来 worker 换主机，只替换本函数为远程事件源读取，判活三问一行不动。"""
    try:
        from ccards import locks as _L
        return _L.lane_beat_read(owner) or {}
    except Exception:
        return {}


def lane_beat_present(owner, epoch):
    """progress 在账判定（MECH-INT-BEAT13A-1）：本任期在 beat 账上是否已有续写一笔。
    True=在账（lane_alive 全权，SILENCE 备用路径不介入）；False=缺账（降级走探针）。"""
    beat = lane_beat_read(owner)
    try:
        return int(beat.get("epoch") or 0) == int(epoch) and float(beat.get("t") or 0.0) > 0
    except (TypeError, ValueError):
        return False


def load_beat_obs():
    try:
        j = json.load(open(BEAT_OBS, encoding="utf-8"))
        return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


def save_beat_obs(obs, apply_mode):
    if not apply_mode:
        return  # dry 只读零写（同 warn 账纪律）
    os.makedirs(STATE, exist_ok=True)
    t = BEAT_OBS + ".tmp"
    with open(t, "w", encoding="utf-8") as f:
        json.dump(obs, f, ensure_ascii=False)
    os.replace(t, BEAT_OBS)


def _plateau_death():
    """圈号持平判死阈可配（BUG-POOL-SLOTCHURN-1 根治项，活卡面 PM 10:28「plateau>=2 硬编仍在」）：
    env LANE_PLATEAU_DEATH 只许**上调**持平圈数（缺省=下限=2——2 是 #91「戳文件装活」持平即死
    语义的下限，任何值不得击穿；env 畸形回退 2）。进程级读一次：改阈随 ctrld 重启生效，
    与常驻件「新进程=改码即时生效」同款姿势。"""
    try:
        return max(2, int(os.environ.get("LANE_PLATEAU_DEATH") or 2))
    except ValueError:
        return 2


def lane_alive(owner, epoch, minutes, cid, obs):
    """判活三问（唯一权威）：①任期匹配 ②心跳龄<阈 ③圈号在涨。断流=死。
    本函数不问进程号——pid/pid_ns/session 为留档字段，不参与生死与归属判定
    （BUG-ANCHOR-NS-1 进程名核验已退役；selftest #89 静态锁看守进程探测字样）。"""
    key = slug(owner)
    beat = lane_beat_read(owner)
    try:
        b_ep = int(beat.get("epoch") or 0)
        b_ring = int(beat.get("ring") or 0)
        b_t = float(beat.get("t") or 0.0)
    except (TypeError, ValueError):
        return False, "beat 账不可解析 %r" % (beat or {})
    if b_ep == epoch and b_t > 0:
        age = max(0.0, (time.time() - b_t) / 60.0)
        last = obs.get(key) or {}
        plateau = 0
        try:
            if int(last.get("epoch") or -1) == int(epoch):
                plateau = int(last.get("plateau") or 0) + 1 \
                    if b_ring <= int(last.get("ring") or -1) else 0
        except (TypeError, ValueError):
            plateau = 0
        obs[key] = {"epoch": epoch, "ring": b_ring, "plateau": plateau, "cid": cid}
        if plateau >= 2:
            if plateau >= _plateau_death():
                return False, "圈号连 %d 圈持平(e%s ring=%s 不涨；阈%d)" % (plateau, epoch, b_ring, _plateau_death())
            # env 上调未及阈=本轮不判死（plateau 可配根治项；不设 env 时阈=2，本行不可达=零行为变化）
        if age >= minutes:
            return False, "心跳龄 %.0fmin≥阈 %.0f（本任期无续写）" % (age, minutes)
        return True, "名牌 e%s ring=%s 心跳龄 %.0fmin" % (epoch, b_ring, age)
    # 事件源②同机过渡形态：道锁 heartbeat 文件/账本静默——仍须同任期（claim 时盖章的 meta）
    _lc, meta, lockdir = load_lane_lock(owner)
    try:
        m_ep = int(meta.get("epoch") or 0)
    except (TypeError, ValueError):
        m_ep = 0
    cdir = os.path.join(CLAIMS, cid)
    if m_ep == epoch and os.path.isdir(lockdir):
        age = min(heartbeat_min(lockdir), heartbeat_min(cdir), silence_min(cdir))
        if age == float("inf") or age >= minutes:
            return False, "过渡心跳断流 age=%s 阈 %.0f" % (
                "∞" if age == float("inf") else "%.0fmin" % age, minutes)
        return True, "过渡心跳 age=%.0fmin（进度流未起写）" % age
    # 既无名牌事件也无同班道锁（重派换班残渣/跨机 worker 未配事件源）：只问卡锁账本静默
    age = silence_min(cdir)
    if age == float("inf") or age >= minutes:
        return False, "任期 e%s 零上报 age=%s" % (epoch, "∞" if age == float("inf") else "%.0fmin" % age)
    return True, "任期 e%s 过渡静默 %.0fmin" % (epoch, age)


# ---------- WARN→ALARM 连轮状态（仅 --apply 写；dry 只读不写） ----------
def load_warn_state(path=WARN_STATE):
    try:
        j = json.load(open(path, encoding="utf-8"))
        if isinstance(j, dict):
            return int(j.get("run_seq", 0)), dict(j.get("warns", {}))
    except (OSError, ValueError):
        pass
    return 0, {}


def warn_note(cid, run_seq, warns, apply_mode):
    has = cid in warns
    ent = warns.get(cid) or {"run": 0, "count": 0}
    consec = (apply_mode and has and ent.get("run") == run_seq - 1) or \
             (not apply_mode and has and ent.get("count", 0) >= 1)
    if apply_mode:
        ent = {"run": run_seq, "count": (ent.get("count", 0) + 1) if consec else 1}
        warns[cid] = ent
    return consec


def save_warn_state(run_seq, warns, apply_mode, warned_this_run, path=WARN_STATE):
    if not apply_mode:
        return
    warns = {k: v for k, v in warns.items() if k in warned_this_run}
    os.makedirs(STATE, exist_ok=True)
    t = path + ".tmp"
    with open(t, "w", encoding="utf-8") as f:
        json.dump({"updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "run_seq": run_seq, "warns": warns}, f, ensure_ascii=False)
    os.replace(t, path)


def done_age_min(c):
    """DONE 卡停留龄(分钟)：graph.save 每次写账刷新 updated_at，而 DONE 后无人再写它
    （转正走 verdict 改态、重做走 REJECT 改态）——updated_at 即 DONE 落账锚，可作龄。
    解析不了=None：如实报 ?，不猜。"""
    s = (c.get("updated_at") or "").strip()
    if not s:
        return None
    try:
        t = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None
    return max(0.0, (datetime.now(tz=t.tzinfo) - t).total_seconds() / 60.0)

ALARM_KEY_CID = re.compile(r"^ALARM (\S+):")


def in_watch_scope(cid):
    """报警生成时现读闸（BUG-REAPER-SCOPE-1）：看护族键「ALARM <cid>:」只许给 CLAIMED 卡响。
    环首 claimed_cards() 是快照，全环一圈=逐卡探锚（powershell 每探至多 20s），圈时长分钟级——
    扫描与落笔之间属主可能已 done/转正/撤销；终态卡不是看护对象（业主 seq=4 定版：
    CLAIMED→回收看护、DONE→校验环节、VERIFIED/CANCELED→终态不再照看），落笔瞬间现读复核，
    非 CLAIMED 一律不响也不落账。结算侧（autoack）只能葬已生的警，生成侧不关闸就每圈
    给局外卡续命——autoack 清旧条、生成重新开新条=BUG-ALARM-LIFECYCLE-1 弱判据假绿教训的根因。
    查无此卡=不进评估（局外账不给它铃，与 autoack「查无此卡不乱销」同保守向）。
    返回 (是否进报警评估, 现读状态串)。"""
    c = card_of(cid)
    stt = (c or {}).get("status") or "查无"
    return stt == "CLAIMED", stt


def _iso_ep(s):
    """ISO/紧凑时戳 → epoch 秒；解析失败=None（判不动就不结，保守向，同判活纪律）。"""
    if not s:
        return None
    from datetime import datetime
    txt = str(s)
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        try:
            dt = datetime.strptime(txt, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


# ---------- BUG-LANE-SILENCE-1 在制道断流探针（续写判据，锚活≠活在干） ----------
def _winify(p):
    """车队路径缺省=WSL 视角（与 dispatch.py PROJ/CODE 同源字面）；Windows 宿主裸跑（巡逻
    手点/本卡判据）换算盘符路径。换算不了/读不到=缺源，走保守放行，不改 WSL cron 主战场语义。"""
    if os.name == "nt":
        m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", p or "")
        if m:
            return m.group(1).upper() + ":" + (m.group(2) or "/")
    return p


def _fleet_proj():
    """会话转录根（与 dispatch.py DISPATCH_PROJ 测试锚同字：生产不设=缺省随 config 派生，
    两侧形态随仓；REQ-OSS-ENGINE-1 由字面量收编为派生值，生产逐字等价见证据文）。"""
    return os.environ.get("DISPATCH_PROJ") or _winify(_cfg.CC_PROJ)


def _fleet_code():
    """车队根 wf-<lane>（同 dispatch.py DISPATCH_CODE_ROOT；缺省=config.CC_CODE 派生）。"""
    return os.environ.get("DISPATCH_CODE_ROOT") or _winify(_cfg.CC_CODE)


def transcript_age_min(owner, now_ep=None):
    """该道会话转录 C--...-code-wf-<lane>/*.jsonl 最新 mtime 距今分钟（看板 laneact 同口径）。
    目录缺/无 jsonl=None——缺源≠冻结（保守不 detach，防非派发会话道误杀）。"""
    d = os.path.join(_fleet_proj(), _cfg.proj_dir_name(_fleet_code(), owner or ""))
    # 目录名编码与 dispatch._proj_dir/lane_registry 前缀三处同源（config.proj_dir_name）；
    # 生产值与旧硬拼前缀逐字相等：encode(车队码根\wf-<lane>)。斜杠/反斜杠经编码后同为 '-'。
    try:
        fs = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jsonl")]
    except OSError:
        return None
    if not fs:
        return None
    try:
        mt = max(os.path.getmtime(f) for f in fs)
    except OSError:
        return None
    return max(0.0, ((now_ep if now_ep is not None else time.time()) - mt) / 60.0)


def worktree_moving(owner, thr_min, now_ep=None):
    """该道工作树 wf-<lane> 在阈窗内有任意文件被写过=True（规格③防误杀硬闸：合法长判据零转录
    但文件在续写→绝不判死）。目录不存在=None（双源不全，detach 需两证皆冻）。
    新鲜即早退（在干道零成本）；只有冻结道才走满全树——跳过 .git/node_modules（体积与安装噪音）。"""
    if not owner:
        return None
    wd = os.path.join(_fleet_code(), "wf-%s" % owner)
    if not os.path.isdir(wd):
        return None
    deadline = (now_ep if now_ep is not None else time.time()) - thr_min * 60
    for root, dirs, files in os.walk(wd):
        dirs[:] = [x for x in dirs if x not in (".git", "node_modules")]
        for fn in files:
            try:
                if os.path.getmtime(os.path.join(root, fn)) >= deadline:
                    return True
            except OSError:
                pass
    return False


def progress_age_min(cid, now_ep=None):
    """REQ-BEAT-ANCHOR-1 对接（规格②）：state/progress.log 本卡最新行龄。在账=优先判此龄
    （名牌每圈续写，progress 断=会话活着没在干，比文件系统定性更硬）；缺账才降级双源。
    无行/ts 不可解析=None（判不动不猜，调用方降级）。"""
    try:
        from ccards import progress
        lines = progress.tail(n=0, card=cid)
    except Exception:
        return None
    if not lines:
        return None
    ep = _iso_ep(lines[-1].split("|", 1)[0])
    if ep is None:
        return None
    return max(0.0, ((now_ep if now_ep is not None else time.time()) - ep) / 60.0)


def recent_green_done(cid, thr_min, now_ep=None):
    """「无绿 done 事件=判断流」的反面闸：events.log 本卡窗内出现 ->DONE/->VERIFIED（含 reverify
    PASS 痕）=这一轮真交过账 → 不 detach。护两个竞态：环首快照后 done 才落笔；REJECT 重派瞬间。
    ts 解析不了的行=跳过（不豁免也不误杀）；无流水文件=False（走正常判据）。"""
    now_ep = now_ep if now_ep is not None else time.time()
    try:
        with open(os.path.join(STATE, "events.log"), encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return False
    for ln in reversed(lines):
        parts = ln.split("|")
        if len(parts) < 3 or parts[1] != cid:
            continue
        if not (parts[2].endswith("->DONE") or parts[2].endswith("->VERIFIED")):
            continue
        ep = _iso_ep(parts[0])
        if ep is None:
            continue
        return (now_ep - ep) / 60.0 < thr_min
    return False


def continuation_verdict(cid, owner, c, thr_min, now_ep=None):
    """在制道续写判定核心（BUG-LANE-SILENCE-1）。返回 (verdict, 说明)：
      moving     工作树在写（③无条件放行，排第一=新鲜道不花判账钱）
      skip-fresh 认领不足窗（判据窗不抢跑，新卡不背旧文件的账）
      active     主信号（progress 优先/jsonl 降级，②）龄在阈内=还在续写
      no-source  定性源读不到（progress/转录皆缺，或工作树目录缺失使「双源皆冻」不成立）
      done-event 窗内有绿 done 事件（豁免）
      detached   锚活但断流：主信号超阈 + 工作树确冻（两证皆冻才判死）"""
    now_ep = now_ep if now_ep is not None else time.time()
    moving = worktree_moving(owner, thr_min, now_ep)
    if moving is True:
        return "moving", "工作树 mtime 在续写(<%.0fmin)" % thr_min
    cl = _iso_ep((c or {}).get("claimed_at"))
    if cl is not None and now_ep - cl < thr_min * 60:
        return "skip-fresh", "认领才 %.0fmin(<阈%.0f)" % ((now_ep - cl) / 60.0, thr_min)
    age, src = progress_age_min(cid, now_ep), "progress"
    if age is None:
        age, src = transcript_age_min(owner, now_ep), "jsonl"
    if age is None:
        return "no-source", "progress/转录均不可读(工作树=%s)" % (
            "缺目录" if moving is None else "冻结")
    if age < thr_min:
        return "active", "%s 龄%.0fmin(<阈%.0f)" % (src, age, thr_min)
    if recent_green_done(cid, thr_min, now_ep):
        return "done-event", "窗内有绿 done 事件"
    if moving is None:
        return "no-source", "工作树目录缺失——「双源皆冻」不成立，保守放行"
    return "detached", "%s 龄%.0fmin(≥阈%.0f)+工作树冻结" % (src, age, thr_min)


def beatzombie_exempt(owner, now_ep=None):
    """BUG-POOL-SLOTCHURN-1 最小保护形（PM 直管代落，余项归工人；插队毒卡停毒语义）。
    病灶实锤：beat 在账 → lane_alive 全权（MECH-INT-BEAT13A-1），续写三信号被关在门外；
    而 L297 `plateau >= 2` 硬编 + brief 只给 progress 模板未教频度规 ⇒ 长活 worker 首拍后
    无续拍，3~4min 必放流（活体 slot-25：首拍 33s、移交 n=13 有真增量，仍整 3min0s 被杀；
    无牌槽 13~24 走「任期零上报 age=∞」同死——两个放流点都只问名牌/账本，不问人是否在写）。
    本函数=放流前最后一道闸：转录 mtime 或工作树 mtime 任一在宽限窗内续写 → 保活不放流，
    只点名。双源皆读不到(None)不豁免——退回 lane_alive 原判，宁漏不误保。
    env RECYCLE_TRANSCRIPT_GRACE_MIN 默认 15.0（REQ-OPENCLAW-WIN-1 误杀归因轮 2026-11-03
    20:2x 上调，PM 直管代落，证据 docs/机制-车队保险丝S2归因-20261103-openclaw误杀.md：
    旧值 5.0 下两班连环误杀——slot-51 转录冻 5.14min、slot-52 冻 5.11min，皆为合法单发长
    Bash（引擎等待轮询 360s / 4.3GB 灌卷 472s）期间零转录属常态，plateau 判死先于 15min
    心跳龄到期到达，5min 豁免窗对 Docker 类装机卡系统性不够；新值与②心跳龄阈 CARD_STALE_MIN=15
    同轨=一条口径「转录+工作树+名牌 15min 三源皆冻才认停摆」，真死道回收至多晚 10min 到拍）；
    ≤0=显式停用（逃生门 + 红检自证开关：撤闸必红 = tests/slot-churn-test.py 的 R 款）。"""
    g = env_num("RECYCLE_TRANSCRIPT_GRACE_MIN", 15.0)
    if g <= 0:
        return False, "保护形停用(RECYCLE_TRANSCRIPT_GRACE_MIN≤0)"
    now_ep = now_ep if now_ep is not None else time.time()
    ta = transcript_age_min(owner, now_ep)
    if ta is not None and ta < g:
        return True, "转录龄%.1fmin(<grace%.0fmin)" % (ta, g)
    if worktree_moving(owner, g, now_ep) is True:
        return True, "工作树mtime在续写(<grace%.0fmin)" % g
    return False, "转录/工作树双源均无%.0fmin内续写" % g


def autoack_sweep(tick_min, apply_mode):
    """警账生命周期结算（BUG-ALARM-LIFECYCLE-1 ①②④）：本看护轮里出生的警，在本轮里入土。
    返回 (卡状态迁移销警数, 腐灭销警数)；dry 只点名不落笔。
    BUG-ALARM-AUTOACK-MASK-1 追加：同题当日反复「未续报」到期不进腐灭数——alerts 侧改钉住，
    本侧打 PINNED 点名行（钉住不落 ack=open 账不归零，S6/面板照常看得见）。"""
    if not _AL:
        print("警账结算跳过：alerts 库不可用")
        return 0, 0
    n_state = n_stale = 0
    now_ep = time.time()
    stale_after = 2 * tick_min * 60
    for k, e in sorted(_AL.entries().items()):
        ack0 = e.get("ack")
        if ack0 and (e.get("last") or "") <= ack0:
            continue
        reason = None
        m = ALARM_KEY_CID.match(k)
        last_ep = _iso_ep(e.get("last"))
        if m:
            c = card_of(m.group(1))
            stt = (c or {}).get("status")
            if c is None:
                pass  # 卡查无此 id：不拿坏账迁销（误伤面不清），留给时间轴腐灭兜底
            elif stt != "CLAIMED":
                reason = "卡状态迁移 %s" % (stt or "?")
            else:
                cl_ep = _iso_ep(c.get("claimed_at"))
                if cl_ep is not None and last_ep is not None and cl_ep > last_ep:
                    reason = "卡状态迁移 再claim"
        if reason is None and last_ep is not None and now_ep - last_ep >= stale_after:
            reason = "超2tick未续报"
        if reason is None:
            continue
        # BUG-ALARM-AUTOACK-MASK-1：同题变体族当日第 2 次及以上的「未续报」到期不再静默销账——
        # alerts.autoack 改写 pinned（灯留在 open 账里，S6 按龄升级），本侧只点名不计数；
        # dry 轮走只读探针 would_pin，报「会钉住」不报「会销」（旧口径=掩蔽的预演）。
        stale_form = reason.startswith("超")
        if apply_mode:
            # 身份形保留（81 锁契约 acked_by=auto@*）：合并签名后 who 必须显式带「auto@recycle-sweep: 原因」
            hit = _AL.autoack(k, "auto@recycle-sweep: " + reason, reason=reason)
            if not hit:
                if stale_form and _AL.is_pinned(k):
                    print("PINNED %s → %s（同题当日反复到期，升级点名·不再静默销账·待人工 ack）"
                          % (k[:100], reason))
                continue  # 没落笔=不计销账数（钉住/并发已被人工处置，虚增计数=账面自欺）
        elif stale_form and _AL.would_pin(k, reason):
            print("PINNED %s → %s（(dry)会钉住点名，同题当日反复到期）" % (k[:100], reason))
            continue
        print("AUTOACK %s → %s（%s）" % (k[:100], reason, "销警" if apply_mode else "(dry)会销"))
        if reason.startswith("卡状态迁移"):
            n_state += 1
        else:
            n_stale += 1
    return n_state, n_stale


# ---------- DONE 滞留扫描（MECH-DONE-SWEEP-1；扫描主体见 done_sweep_scan） ----------
def env_num(name, default, key=None):
    # REQ-OSS-ENGINE-1：env 锚 > config.toml（键名缺省自动归位 thresholds.*）> 内置默认（=现网值）。
    if key is None:
        key = "thresholds." + name.lower()
    return float(_cfg.num_setting(key, name, default))


def done_ts_of(c):
    """完成时戳读取序（卡面第6条）：done_at(顶层，MECH-STATE-STD-1 落写) → output.done_at
    （done --out-json 合法写通道，盘账 16 张的实际可用位）→ updated_at（panel 同款兜底）。
    只读不改写面；全无=返回 None（不造假账龄）。"""
    ts = c.get("done_at") or (c.get("output") or {}).get("done_at") or c.get("updated_at")
    return str(ts) if ts else None


def done_age_h(c, now_ep=None):
    """滞留小时数；None=时戳不可解析（保守不补跑，调用方点名可见）。"""
    ep = _iso_ep(done_ts_of(c))
    if ep is None:
        return None
    return ((now_ep if now_ep is not None else time.time()) - ep) / 3600.0


def load_done_state():
    try:
        j = json.load(open(DONE_STATE, encoding="utf-8"))
        if isinstance(j, dict) and isinstance(j.get("cards"), dict):
            return j
    except (OSError, ValueError):
        pass
    return {"cards": {}}


def save_done_state(st):
    t = DONE_STATE + ".tmp"
    try:
        with open(t, "w", encoding="utf-8") as f:
            json.dump({"updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cards": st["cards"]},
                      f, ensure_ascii=False)
        os.replace(t, DONE_STATE)
    except OSError as exc:
        print("WARN: done-sweep.json 落盘失败(%r)——计数照常打，下轮读到旧账重算，不静默" % exc)


def done_state_prune(st):
    """VERIFIED/CANCELED/DRAFT/查无此卡 → 销账：收口后不背旧计数（新滞留新起算）。
    OPEN **留账**：连续 FAIL 计数跨「REJECT→重派→重做→再 DONE」链累计——这正是业主
    「连续补跑 FAIL 满 N 次」的语义本体（重做再红=连续红，不是新起一轮）。"""
    n = 0
    for cid in list(st["cards"]):
        c = card_of(cid)
        if c is None or c.get("status") in ("VERIFIED", "CANCELED", "DRAFT"):
            st["cards"].pop(cid, None)
            n += 1
    return n


def is_wsl():
    return os.name == "posix" and (bool(os.environ.get("WSL_DISTRO_NAME")) or os.path.isdir("/mnt/c"))


def cross_argv(cmd):
    """跨 OS 补跑 argv；None=另一侧不可达。env CARD_CROSS_BASH 直钉（测试双/特殊安装；
    配了却不存在=明示不可达，走 pending 分流档，不猜别的解释器）。"""
    b = os.environ.get("CARD_CROSS_BASH")
    if b is not None:
        if b and (shutil.which(b) or os.path.exists(b)):
            return [b, "-c", cmd]
        return None
    if is_wsl():
        for p in GIT_BASH_CANDIDATES:
            if os.path.exists(p):
                return [p, "-c", cmd]
        return None
    if os.name == "nt" or sys.platform.startswith("win"):
        # 2026-11-03 业主令删 WSL（ubuntu/docker-desktop 均已注销）：wsl.exe 二进制虽在，
        # 零发行版下调用只会 127+UTF-16 横幅=BUG-REDAUTO-HOST-PIN2 同型毒源，永不再当跨宿通道。
        # 跨 OS 补跑自此恒不可达（None→pending 分流档）；单宿 Windows 形态下属预期。
        return None
    return None


def run_argv(argv, to):
    """执行判据；返回 (rc, out) 或 (None, 失败说明)。124=超时（不判=不 REJECT：判据跑得慢
    是卡面问题，别拿假红驳卡）。"""
    try:
        r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=to, creationflags=NO_WIN)
        return r.returncode, (r.stdout or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %ss" % to
    except OSError as exc:
        return None, "exec failed: %r" % exc


MISMATCH_HINTS = ("command not found", "no such file or directory", "not found",
                  "is not recognized", "cannot find",
                  # 双态判据的宿主守卫自证文本（NOT-WIN-HOST/NOT-LINUX-HOST）=「跑错了宿主」
                  # 而非「判据真红」——归 pending 待该侧验收（REDAUTO-HOST-1②同语义）。
                  # 03:4x 实锤：CARD_VERIFY_BASH 被钉到 System32 WSL 启动器时，守卫 echo
                  # 被当真实 FAIL 走 REJECT=毒轮空转（三卡连坐）；此行=第二道保险。
                  "not-win-host", "not-linux-host")
CD_FAIL_RE = re.compile(r"cd[:\s].{0,120}(no such file|not exist|cannot|can't|不存在|找不到)",
                        re.IGNORECASE)


def looks_host_mismatch(rc, out):
    """「跑不了/没跑到」环境特征（vs「判据真红」），BUG-REDAUTO-HOST-1② 定版三条：
    ① rc 127/126=命令不在；② rc≠0 且**输出全空**（15:58 DONE-SWEEP 案原话「tail 空无真
    失败」——连判据断言都没到达，零归因素材）；③ 路径/命令找不到类输出或 cd 失败行。
    仅在判据红参与 pending/REJECT 分流——误 pending 可见（计数+events+点名行），
    误 REJECT=让完工卡白重做，保守向 pending。「真红但恰好静默」的卡走 pending 后
    PM/Verifier 现面处置，驳回权不丢，只是不从看护环代行使。"""
    if rc in (126, 127):
        return True
    if not (out or "").strip():
        return True
    lo = (out or "").lower()
    return any(h in lo for h in MISMATCH_HINTS) or bool(CD_FAIL_RE.search(out or ""))


def via_host():
    return "%s/%s" % (os.name, sys.platform)   # 与闸内 _run_verify 的 via 痕同式


def _host_alias(via):
    """宿主名归一（host 字段与 via 痕两种形态同居）：win|nt/win32→'win'；wsl|posix/linux→'wsl'。"""
    v = str(via or "").strip().lower()
    if v.startswith("nt") or "win" in v or v in ("gitbash", "git-bash"):
        return "win"
    if v.startswith("posix") or "linux" in v or v == "wsl":
        return "wsl"
    return None


def local_host():
    """本进程侧别名——cron 环不继承在制会话的宿主选择，这台 WSL 上「本机」永远=posix/linux；
    本卡两案的本体。只用来与卡面记录比对，不再单独定生杀。"""
    return _host_alias(via_host())


def recorded_host(c):
    """卡面记录的原验收宿主（BUG-REDAUTO-HOST-1①，卡面话术「判据落 host 字段」）：
    host 显式字段（win/wsl 或 nt/win32、posix/linux 形态均可）优先 → verify_run.via 痕
    （闸 PASS/auto@done 绿时盖的执行侧，REJECT 不磨此痕=冤案卡重做完仍带着「当年在哪家变绿」）。
    None=无记录（存量新卡）——不猜，按旧纪律走环境特征分流。"""
    h = _host_alias(c.get("host") or "")
    if h:
        return h
    return _host_alias(((c.get("verify_run") or {}).get("via")) or "")


def card_cwd(c):
    """补跑工作目录（与闸 _run_verify「继承进程 cwd」同式才不产生预检/闸二象性）：
    卡 repo 字段优先（真账多数无此字段；Windows 路径换算 /mnt/x/ 后须实存）→ 否则自
    TEAM 向上找带 .git 的仓根（真账=主仓；判据里 git rev-parse/相对路径的前提位）→
    都无=None 沿用本进程位（cron cwd=HOME 非仓，不兜这一步 16 张真账判据成片假红=误驳，
    实账 dry 2026-11-02 点名卡全用 rev-parse/相对形态，见 docs/mech-done-sweep-20261102.md）。"""
    r = (c.get("repo") or "").strip()
    if r:
        p = r.replace("\\", "/")
        if os.name == "posix":
            m = re.match(r"^([A-Za-z]):(/.*)?$", p)
            if m:
                p = "/mnt/" + m.group(1).lower() + (m.group(2) or "/")
        if os.path.isdir(p):
            return p
    d = TEAM
    while d and d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, ".git")) or os.path.isfile(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return None


def flat_tail(out, n=120):
    return " ".join((out or "").split())[-n:]


def do_verdict(cid, result, reason):
    """MECH-STD1 进程内通道（与 do_release 同理，不开第三种通道）：verdict 的套件复跑/瞬锁
    落账/竞态重载全在 cmd_verdict 自理，本环只调用并按其结论记账。rc≠0 时贴出闸口原话。"""
    from ccards import cli as _cli
    buf, rc = io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(buf):
            _cli.run(["verdict", cid, result, "--reason", reason])
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as exc:
        rc = 1
        buf.write("EXC: %r" % exc)
    out = buf.getvalue().strip()
    if rc != 0:
        print("   verdict %s rc=%d %s" % (result, rc, out or "(无输出)"))
    return rc == 0


def note_event(cid, old, new, extra):
    """补跑归因/待验收入 events（append-only 审计流水；reconcile 只取每卡最后一条
    「->新状态」，本行 new 侧恒=卡面当下状态=不点裸改假铃）。写失败点名，不静默吞。"""
    try:
        from ccards import events as _EV
        _EV.event(cid, old, new, "recycle", " ".join((extra or "").split()))
    except Exception as exc:
        print("WARN: %s events 落笔失败(%r)——stdout/摘要计数仍在，不算静默" % (cid, exc))


def sweep_lock_try():
    """快环防重跑：mkdir 原子抢锁；锁主 pid 死、或无可读 meta 超 2min（崩残）、或超龄
    ≥SWEEP_LOCK_STALE_MIN → 接管；抢不到就本轮让路（下一拍再查，宁漏一拍不叠两拍）。"""
    for _attempt in (0, 1):
        try:
            os.makedirs(STATE, exist_ok=True)
            os.mkdir(SWEEP_LOCK)
        except FileExistsError:
            age, pid = float("inf"), None
            try:
                age = (time.time() - os.path.getmtime(SWEEP_LOCK)) / 60.0
            except OSError:
                pass
            try:
                pid = json.load(open(os.path.join(SWEEP_LOCK, "meta.json"), encoding="utf-8")).get("pid")
            except (OSError, ValueError):
                pass
            if (pid is not None and not pid_alive(pid)) or (pid is None and age >= 2.0) \
               or age >= SWEEP_LOCK_STALE_MIN:
                print("SWEEP-LOCK 接管陈旧锁(主=%s 龄%.1fmin) → 重占" % (pid, age))
                shutil.rmtree(SWEEP_LOCK, ignore_errors=True)
                continue
            return False
        except OSError as exc:
            print("WARN: 防重跑锁建不了(%r)——本轮裸跑（账龄>阈值天然幂等，风险=两轮重叠）" % exc)
            return True
        try:
            with open(os.path.join(SWEEP_LOCK, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, f)
        except OSError:
            pass
        return True
    return False


def train_held_ids():
    """MERGE-TRAIN 持管卡号集合（state/merge-train.json，stage ∈ 火车持管态）。
    done-sweep 见此组 DONE 卡让路：交卷即验/队列试合归火车，双轨并跑=同卡双起子进程互踩。
    账/件读不到=可见 WARN + 空集回落（火车没在跑时 done-sweep 照旧全量兜底，绝不静默漏卡）。"""
    try:
        import mergetrain as _MT
        return {cid for cid, rec in _MT.load_state(TEAM)["cards"].items()
                if rec.get("stage") in _MT.HELD_STAGES}
    except Exception as exc:
        print("WARN: train 账不可读(%r) → done-sweep 按全量处理（不误漏）" % exc)
        return set()


def train_scan(apply_mode):
    """MERGE-TRAIN 快环挂点（REQ-MERGE-TRAIN-1）：①交卷即验/②队列试合真并/③撞车登记/
    ⑤生效锁全在 mergetrain.run_ring 内自理（自带火车锁）。返回
    (passed_now, merged, blocked, queued, stale, not_energized)；火车异常不拖快环主体——
    响亮 WARN 全零，下拍再追。dry 只点名不写账不合线。"""
    try:
        import mergetrain as _MT
        g = _MT.run_ring(apply=apply_mode)
        return (g.get("passed", 0), g.get("merged", 0), g.get("blocked", 0),
                g.get("queued", 0), g.get("stale", 0), g.get("not_energized", 0))
    except Exception as exc:
        print("WARN: MERGE-TRAIN 本拍异常(%r)——快环主体不受拖累，下拍再追" % exc)
        return (0, 0, 0, 0, 0, 0)


def done_sweep_scan(apply_mode, hours, n_cap, run_seq):
    """DONE 滞留扫描主体。返回 (swept, verified, rejected, pending_side)。
    swept=本轮实际补跑过的滞留卡数（含超时/闸拒等未定案项，定案三分项之和≤swept）；
    满 N 停跑与无判据卡**不计 swept**（没跑=没补跑），各自点名行可见。
    dry 只列名判账龄，一律不起子进程、不写盘（与回收主体同纪律，*/1 下 dry 随便点）。"""
    from ccards import graph
    held = train_held_ids()
    stuck = []
    for c in graph.all_cards().values():
        if c.get("status") != "DONE":
            continue
        cid = c.get("id") or "?"
        if cid in held:
            continue        # MERGE-TRAIN 持管（①~③流程火车自有），快环不重复补跑
        age = done_age_h(c)
        if age is None:
            print("DONE-TS %s: 完成时戳不可解析(%r) → 保守不补跑，不造假账龄" % (cid, done_ts_of(c)))
            continue
        if age > hours:
            stuck.append((cid, c, age))
    st = load_done_state()
    done_state_prune(st)
    n_s = n_v = n_r = n_p = 0
    for cid, c, age in stuck:
        ent = st["cards"].get(cid) or {}
        fails = int(ent.get("fails", 0) or 0)
        verify = (c.get("verify") or "").strip()
        sha = hashlib.sha1(verify.encode("utf-8")).hexdigest()[:12] if verify else "-"

        def mark_pending(text):
            nonlocal n_p
            if ent.get("pending_sha") != sha:
                ent["pending_sha"] = sha
                note_event(cid, "DONE", "DONE", "待该侧验收: %s" % text)
            st["cards"][cid] = ent
            n_p += 1
            print("DONE-PENDING %s: %s → 卡留 DONE 可见、不 REJECT 不假转正"
                  "（events 按 cmd_sha 去重只一笔，摘要计数每轮都打）" % (cid, text))

        def mark_reject(text):
            nonlocal n_r, fails
            if do_verdict(cid, "REJECT", "recycle DONE补跑FAIL: %s" % text):
                n_r += 1
                fails += 1
                st["cards"][cid] = dict(st["cards"].get(cid) or {}, fails=fails,
                                        last_fail=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                note_event(cid, "DONE", "OPEN", "补跑FAIL归因: %s" % text)
                print("DONE-REJECT %s: 回OPEN 等重派（业主拍板=归因并重做，非机制副作用）连续FAIL %d/%d"
                      % (cid, fails, n_cap))
            else:
                print("DONE-GATE-RED %s: verdict REJECT 被闸拒(执行窗竞态)→ 本轮不记 FAIL，下拍再判" % cid)

        if fails >= n_cap:
            # 停跑连拍梯（本环自有账，免疫两 cron 交叉 run_seq）：首拍 SKIP 点名，连 2 拍升
            # ALARM-DONE 稳定键进警账聚合；不再补跑，PM/inbox 处置（删 state/done-sweep.json
            # 该条=恢复补跑意愿的显式动作）。键式避开「ALARM <cid>:」——DONE 卡≠CLAIMED，
            # 全环 autoack 会按「卡状态迁移」把它每 10min 误停铃。
            prev = int(ent.get("skip_run", -99) or -99)
            streak = int(ent.get("skip_streak", 0) or 0) + 1 if prev == run_seq - 1 else 1
            if apply_mode:
                st["cards"][cid] = dict(ent, skip_run=run_seq, skip_streak=streak)
            if streak >= 2:
                print("ALARM-DONE %s: DONE 滞留连续补跑FAIL %d 满 %d，已停自动补跑第 %d 拍"
                      "——请 PM 经 inbox 处置（要恢复补跑=删 state/done-sweep.json 条目 %s）"
                      % (cid, fails, n_cap, streak, cid))
                if _AL:
                    _AL.record("ALARM-DONE %s: DONE 滞留连续补跑FAIL满%d 停自动补跑，PM经inbox处置"
                               % (cid, n_cap))
            else:
                print("SKIP-DONE %s: 连续补跑FAIL 满 %d（账龄%.1fh）本轮不补跑，连二拍升 ALARM-DONE"
                      % (cid, fails, age))
            continue
        if not verify:
            print("DONE-NOVERIFY %s: 滞留%.1fh 但卡面无 verify 判据 → 无法补跑，不 REJECT"
                  "（缺判据≠货坏），请 PM 补判据" % (cid, age))
            continue
        if not apply_mode:
            print("DONE-STALE %s: 账龄%.1fh>阈%.1fh → (dry)会补跑 verify=[%s] fails=%d/%d"
                  % (cid, age, hours, verify[:60], fails, n_cap))
            n_s += 1
            continue
        n_s += 1
        # 补跑+落章全程 chdir 到卡仓（闸 _run_verify 在 do_verdict 内继承本进程 cwd——
        # 不同位=预检绿闸红 DONE-GATE-RED 每拍空响；chdir 失手则原样继承，两问同位不猜）。
        prev_cwd, cw = None, card_cwd(c)
        if cw:
            try:
                prev_cwd = os.getcwd()
                os.chdir(cw)
            except OSError:
                prev_cwd = None
        try:
            to = int(env_num("CARD_VERIFY_TIMEOUT", 600, key="engine.verify_timeout_s"))
            # BUG-REDAUTO-HOST-PIN2（2026-11-03 19:x 实锤）：旧式裸 getenv("CARD_VERIFY_BASH",
            # "bash") 缺位时 shutil.which 看 PATH=Git Bash，而 subprocess/CreateProcess 先探
            # System32 后探 PATH=**WSL 启动器**——两解析器不同源，which 校验给不出保护；18:0x 后
            # WSL 代理横幅(UTF-16)混进 tail 使痕非空，②「尾痕空=环境性」被穿，selftest #90 A1
            # 冤驳现形。同类洞=mergetrain BUG-TRAIN-HOSTPIN-1①同款收口：复用 ccards
            # _resolve_verify_bash（win32 毒径拒+Git 候选现探+全缺 die 宁红不猜；POSIX 零漂移），
            # die(SystemExit) 转响亮不可用=与既有「宿主不可用」档同路（不 REJECT 不假转正）。
            try:
                from ccards.commands import _resolve_verify_bash
                bashbin = _resolve_verify_bash()
            except SystemExit as exc:
                rc_l, out_l = None, "本机判据宿主不可用: %s" % (exc.args[0] if exc.args else exc)
            else:
                rc_l, out_l = run_argv([bashbin, "-c", verify], to)
            print("DONE-SWEEP %s: 本机补跑 rc=%s via=%s cwd=%s tail=[%s]"
                  % (cid, rc_l, via_host(), cw or "(继承)", flat_tail(out_l)))
            if rc_l == 0:
                if do_verdict(cid, "PASS", "recycle DONE补跑(账龄%.1fh 本机复跑绿，ring=sweep)" % age):
                    n_v += 1
                    st["cards"].pop(cid, None)
                    note_event(cid, "DONE", "VERIFIED", "补跑PASS转正 via=%s cmd_sha=%s" % (via_host(), sha))
                    print("DONE-VERIFIED %s: 转正落章（at 时戳/verify_run 痕由闸盖，本环不代笔）" % cid)
                else:
                    print("DONE-GATE-RED %s: verdict PASS 被闸拒(复跑差异/执行窗竞态)→ 卡照旧，"
                          "不计 FAIL 不 REJECT，下拍再判" % cid)
                continue
            if rc_l == 124:
                print("DONE-TIMEOUT %s: 本机补跑超%d s 未毕 → 不判不记不驳，下拍重判（判据过长是卡面题）"
                      % (cid, to))
                continue
            detail = "本机rc=%s via=%s tail=[%s]" % (rc_l, via_host(), flat_tail(out_l))
            cross = cross_argv(verify)
            rc_x, out_x = None, ""
            if cross is not None:
                rc_x, out_x = run_argv(cross, to)
                if rc_x == 124:
                    print("DONE-TIMEOUT %s: 跨侧补跑超%d s 未毕 → 不判，下拍重判" % (cid, to))
                    continue
                if rc_x is not None:
                    detail += " 跨侧rc=%s tail=[%s]" % (rc_x, flat_tail(out_x))
            # BUG-REDAUTO-HOST-1 归因前置两问：①这一侧的红有没有归因素材（尾痕空/找不到类=
            # 环境性失败，不算红实据）；②卡面记录的变绿宿主是谁、本机是不是它。
            rec = recorded_host(c)
            loc = local_host()
            follow = rec is not None and rec != loc       # 冤案防线：复跑没发生在原验收宿主
            l_env = rc_l is not None and rc_l != 0 and looks_host_mismatch(rc_l, out_l)
            x_env = rc_x is not None and rc_x != 0 and looks_host_mismatch(rc_x, out_x)
            if rc_l is None:
                # 本机连执行都不可达：对侧跑绿→待对侧验收（本机落章=假痕）；对侧跑红且带
                # 真失败痕→判据真红可驳；对侧红而无素材→环境性未排除不驳；双侧都起不了→
                # NOHOST 点名（静默吞反例=本卡禁令，大声些）。
                if rc_x == 0:
                    mark_pending("本机不可执行(%s)对侧绿" % detail)
                elif rc_x is not None and not x_env:
                    mark_reject(detail)
                elif rc_x is not None:
                    mark_pending(detail + " 对侧rc≠0但尾痕空/找不到类=环境性失败未排除，不驳先喊")
                else:
                    print("DONE-NOHOST %s: 双侧执行器均不可达(%s)→ 不判，PM 查宿主" % (cid, detail))
            elif rc_x == 0:
                mark_pending(detail + " 对侧绿本机红=单侧OS判据（闸PASS复跑钉本机宿主，跨侧落章=假痕）")
            elif rc_x is not None:
                # 双侧都真跑了且都红：只要有一侧的红没有归因素材（静默/cd 失败类），就不能说
                # 环境已排除——15:58 案（对侧 rc=1 尾空）当年正是从这里被冤驳，双真痕才许驳。
                if l_env or x_env:
                    mark_pending(detail + " 双侧皆红但至少一侧无归因素材(尾痕空/找不到类)"
                                          "=环境性失败未排除（REDAUTO-HOST-1②），不驳先喊")
                else:
                    mark_reject(detail)          # 双侧真痕红=判据真红（含记录侧亲眼红，驳回权成立）
            elif l_env:
                mark_pending(detail + " 跨侧桥不可用且为静默/命令/路径找不到特征=疑似单侧判据，宁pending不假驳")
            elif follow:
                # 卡面记着「当年在另一家变绿」，而对侧不可达——判据没在它变绿的宿主上重跑过，
                # 本机红不构成驳回权（13:57 PARITY-5 类）：pending_side 喊话，不许当 FAIL 打回。
                mark_pending(detail + " 卡面原验收宿主=%s≠本机=%s 且对侧不可达=判据未在变绿宿主"
                                      "上重跑过，本机红不具驳回权（REDAUTO-HOST-1①）" % (rec, loc))
            else:
                mark_reject(detail)          # 无桥+真红特征+宿主无异地记录 = 就地归因驳
        finally:
            if prev_cwd is not None:
                os.chdir(prev_cwd)
    if apply_mode:
        save_done_state(st)
    return n_s, n_v, n_r, n_p



# ---------- 报警范围清账（BUG-REAPER-SCOPE-1） ----------
WATCH_ALARM_RE = re.compile(r"^ALARM (\S+): ")   # 看护环升 Alarm 的独有格式（台账类 "ALARM: x" 不沾）


def alarm_scope_sweep(apply_mode):
    """开放警账里凡看护格式、涉及卡当前状态∉{CLAIMED}（含无卡）= 僵尸警，autoack 停铃。
    只动开放项；复响天然复活（record 刷 last>ack），清僵尸永远不会灭掉活问题。
    图读不到/空账=整轮拒清：账本读错≠卡全死了，空铃一次=活警报被静默吞掉。"""
    if _AL is None:
        return 0, 0
    try:
        from ccards import graph
        cards = graph.all_cards()
    except Exception as exc:
        print("WARN ALARM-SCOPE: 图读取异常(%r) → 本轮跳过清账（防空铃）" % exc)
        return 0, 0
    if not cards:
        print("WARN ALARM-SCOPE: 账本图不可读/空 → 本轮跳过清账（防空铃）")
        return 0, 0
    zombie = []
    for e in _AL.open_entries():
        k = e.get("msg") or ""
        m = WATCH_ALARM_RE.match(k)
        if not m:
            continue
        cid = m.group(1)
        st = (cards.get(cid) or {}).get("status", "无卡")
        if st != "CLAIMED":
            zombie.append((k, cid, st))
    n_ack = 0
    for k, cid, st in zombie:
        hit = apply_mode and _AL.autoack(k, "auto@recycle-scope")
        n_ack += 1 if hit else 0
        print("ALARM-SCOPE %s(状态=%s): %s" % (cid, st,
              "已清账(ack留痕)" if hit else ("(dry)会清账" if not apply_mode else "未动(竞态已闭)")))
    return len(zombie), n_ack


def _ghost_session_pids(owner):
    """放流前取该道在跑会话 pid 快照（BUG-POOL-SLOTCHURN-1③杀旧会话防幽灵，dispatch 同款语义）。
    认人不认牌：CCLANE=<道>- 启动器标记匹配 claude.exe（与 ccards.commands._named_lane_set
    同口径），连带直子进程（长工具跑在子树里）；快照在放流**之前**取=只杀当班，
    重派同名道的新班（不同 pid）不伤及。全程 Win32_Process CIM 单通道（#76 退役锁禁字样绕开）。
    env 缝（仅测试，生产不设）：RECYCLE_SESSION_PID_CMD（%L%=道名，stdout 每行一 pid）。
    查询失败/非 Windows/无 owner=空表（宁漏不误杀，幽灵点名由 PM 兜）。"""
    owner = re.sub(r"[^A-Za-z0-9_.-]", "", owner or "")
    if not owner:
        return []
    seam = os.environ.get("RECYCLE_SESSION_PID_CMD")
    try:
        if seam:
            r = subprocess.run(seam.replace("%L%", owner), shell=True, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=25, creationflags=NO_WIN)
        elif os.name == "nt":
            ps = ("$l=Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\" | "
                  "Where-Object { $_.CommandLine -match 'CCLANE=%s-' } | ForEach-Object { $_.ProcessId }; "
                  "$c=Get-CimInstance Win32_Process | Where-Object { $l -contains $_.ParentProcessId } | "
                  "ForEach-Object { $_.ProcessId }; $l + $c" % owner)
            r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=30, creationflags=NO_WIN)
        else:
            return []
        if r.returncode != 0:
            return []
        return [int(x.strip()) for x in (r.stdout or "").splitlines() if x.strip().isdigit()]
    except Exception:
        return []


def _ghost_kill(cid, owner, pids):
    """放流成功后按快照杀旧会话：判活权威仍是账本事件流（#76/#91 语义零触碰），
    本节纯善后清理。os.kill(SIGTERM) 在 Windows=TerminateProcess；杀不动只点名。
    本进程链即在任会话（CCLANE 前缀=道名）时跳过——自杀会切断看护轮，幽灵转 PM。
    env 缝 RECYCLE_KILL_CMD（%P%=pid）仅测试。"""
    if not pids:
        print("   GHOST-KILL-SKIP %s: lane=%s 会话快照为空（缺源=宁漏不误杀）" % (cid, owner))
        return
    own = (os.environ.get("CCLANE") or "").strip()
    if owner and own.startswith(owner + "-"):
        print("   GHOST-KILL-SKIP %s: lane=%s 本进程链即在任会话（自杀=断看护轮）→ 幽灵转 PM" % (cid, owner))
        return
    seam = os.environ.get("RECYCLE_KILL_CMD")
    n_ok = n_fail = 0
    for p in pids:
        if p == os.getpid():
            continue
        try:
            if seam:
                subprocess.run(seam.replace("%P%", str(p)), shell=True, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=25, creationflags=NO_WIN)
            else:
                os.kill(p, signal.SIGTERM)
            n_ok += 1
        except Exception:
            n_fail += 1
    print("   GHOST-KILL %s: lane=%s 快照=%s 杀=%d 败=%d（放流同轮清场防一卡双工）"
          % (cid, owner, pids, n_ok, n_fail))


def do_release(cid, note):
    """MECH-STD1：进程内调 ccards.cli.run（原 shell 起 card.py release 的通道退役）。
    语义与子进程版等价：写锁/双锁清/回队头/DAG 刷新全在库闸内；SystemExit 码=原 rc。
    BUG-POOL-SLOTCHURN-1③：放流成功=同轮按 release 前快照杀该道旧会话防幽灵
    （不杀则旧桌照跑+派发器同卡再开新桌=一卡双工；dispatch 侧「已杀会话防幽灵」同款）。"""
    from ccards import cli as _cli
    owner = None
    ghost_pids = []
    try:
        owner = (card_of(cid) or {}).get("owner")
        ghost_pids = _ghost_session_pids(owner)
    except Exception as exc:
        print("   GHOST-SNAP-WARN %s: 会话快照取不到(%r)——放流照做，幽灵点名由 PM 兜" % (cid, exc))
    buf, rc = io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(buf):
            _cli.run(["release", cid, "--who", "recycle"])
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as exc:  # 库炸栈也不裸崩看护轮：报 rc=1 留痕
        rc = 1
        buf.write("EXC: %r" % exc)
    out = buf.getvalue().strip()
    print("   release rc=%d %s (%s)" % (rc, out or "(无输出)", note))
    if rc == 0:
        _ghost_kill(cid, owner, ghost_pids)
    return rc == 0


def pool_round(apply_mode):
    """弹性工位池收桌（REQ-POOL-ELASTIC-1，sweep 快环专属作业）：
      交卷转正（VERIFIED/CANCELED）→ 收桌（force 允许——交付经主干闸在案，残渣即垃圾）；
      躺平桌（桌龄超阈且卡回 OPEN/查无此卡）→ 净桌收、**脏桌不毁**留点名（PM 处置）；
      CLAIMED/DONE 桌不动（在制与待验桌都有用）。
    台账留痕两笔：①桌账 history+collected 状态（pool.json，操作账）；②对应卡的 progress
    旁证流一笔（有卡号才记——progress 对错号 die，查无此卡桌只留 ①）。dry 只报不动手
    （POOL-DRY 行），主链纪律同款。"""
    if _pool is None:
        print("WARN: pool 模块不可用——本轮工位池收桌缺报")
        return (0, 0, 0, 0)
    try:
        from ccards import graph as _G
        cards = _G.all_cards()
    except Exception as exc:
        print("WARN: 账本读不到(%r)——本轮跳过收桌（空账误灭桌比不收更糟，同警账纪律）" % exc)
        return (0, 0, 0, 0)
    try:
        n_r, n_f, n_d, lines, n_w = _pool.collect_round(cards, apply_mode, log=print)
    except Exception as exc:
        print("WARN: 收桌轮异常(%r)——本轮工位池收桌缺报，桌账不动" % exc)
        return (0, 0, 0, 0)
    if apply_mode:
        for slot, cid, why in lines:
            if not cid:
                continue
            try:
                from ccards import cli as _cli
                with contextlib.redirect_stdout(io.StringIO()):
                    _cli.run(["progress", cid, "工位 %s 收桌：%s（弹性工位池自动回收，台账留痕）"
                              % (slot, why), "--who", "pool"])
            except SystemExit:
                pass                       # 卡恰被并发迁走/错号=旁证笔落空，桌账①已留痕不追
            except Exception as exc:
                print("WARN: 收桌留痕笔 %s@%s 未落(%r)（桌账已记，不追不断轮）" % (cid, slot, exc))
    return (n_r, n_f, n_d, n_w)


def main():
    ap = argparse.ArgumentParser(prog="recycle.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float,
                   default=float(_cfg.num_setting("thresholds.beat_stale_min", "CARD_STALE_MIN", 15.0)),
                   help="静默阈值(分钟)，默认=配置/15")
    ap.add_argument("--apply", action="store_true", help="真正执行(默认 dry-run 只报不动手)")
    ap.add_argument("--dry", action="store_true", help="显式 dry(与默认一致，可覆盖别处的 --apply)")
    ap.add_argument("--sweep-only", dest="sweep_only", action="store_true",
                    help="只跑报警范围清账(BUG-REAPER-SCOPE-1 存量一次性收口)，不走回收主循环/GC")
    ap.add_argument("--tick-minutes", type=float, default=10.0,
                    help="一拍时长(分钟，对齐 cron */10)；警 last 超 2×tick 未续报=自动销警"
                         "（BUG-ALARM-LIFECYCLE-1②）")
    ap.add_argument("--ring", choices=["full", "sweep"], default="full",
                    help="看护环(MECH-DONE-SWEEP-1，业主信箱 seq=7 定版)：full=旧全环比照旧"
                         "(CLAIMED 锚看护+GC+警账结算，*/10，不扫 DONE)；sweep=独立快环专做"
                         " DONE 滞留补跑(*/1，轻：不探锚不起 powershell 不结算警账)")
    ap.add_argument("--done-hours", type=float, default=None,
                    help="DONE 滞留账龄阈值(小时)，默认 2；env CARD_DONE_SWEEP_HOURS 可配，旗标再压 env"
                         "（MECH-DONE-SWEEP-1）")
    ap.add_argument("--done-fails", type=int, default=0,
                    help="同卡连续补跑 FAIL 满 N 次停自动补跑升 ALARM-DONE，默认 2；env CARD_DONE_SWEEP_FAILS")
    a = ap.parse_args()
    if a.dry:
        a.apply = False
    done_hours = a.done_hours if a.done_hours is not None else env_num("CARD_DONE_SWEEP_HOURS", 2.0, key="pool.done_sweep_hours")
    n_cap = a.done_fails if a.done_fails > 0 else max(1, int(env_num("CARD_DONE_SWEEP_FAILS", 2, key="pool.done_sweep_fails")))
    sil_min = env_num("CARD_SILENCE_RELEASE_MIN", 45.0, key="thresholds.silence_release_min")   # BUG-LANE-SILENCE-1 断流阈；≤0=显式停用（逃生门）
    warn_file = WARN_STATE_SWEEP if a.ring == "sweep" else WARN_STATE
    if a.sweep_only:
        n_c, n_k = alarm_scope_sweep(a.apply)
        print("recycle: alarm-scope 候选=%d 清账=%d mode=%s (sweep-only)"
              % (n_c, n_k, "APPLY" if a.apply else "DRY"))
        return

    # ---- MECH-MEMGATE-1 同面报警：水位低照收不误（超时在制该放还得放），但留痕 ----
    if _MEM_STATUS:
        mem_block, mem_note = _MEM_STATUS()
        if mem_block:
            print("MEMGATE-WARN %s → 本轮回收照跑；派发侧应已 MEMGATE-SKIP 憋住新单，"
                  "仍撑在此水位=存量没缩，请 PM 复核 DISPATCH_CAP/停道" % mem_note)
            if _AL:
                _AL.record("MEMGATE-WARN 宿主空闲内存低于发单地板，回收轮仍见低水位（派发侧已憋新单，需 PM 复核并发）")

    n_stale = n_zombie = n_alive = n_warn = n_fixed = 0
    n_sil = 0    # BUG-LANE-SILENCE-1 断流点名数（dry=会 detach；apply=实际 detach）
    n_ds = n_dv = n_dr = n_dp = 0
    n_pr = n_pf = n_pd = n_pw = 0   # REQ-POOL-ELASTIC-1 收桌计数（removed/forced/dirty/wait；full 环=0 属「未扫」）
    n_tp = n_tm = n_tb = n_tq = n_ts = n_te = 0    # MERGE-TRAIN 火车计数（full 环=照旧未跑）
    verb = "回收" if a.apply else "(dry)会回收"
    run_seq, warns = load_warn_state(warn_file)
    if a.apply:
        run_seq += 1
    warned_this_run = set()

    def warn(cid, msg, alarm=None):
        """alarm=稳定文案（进聚合账做键）；msg 可含实时值（静默分钟等）只给人看。
        不分家的话，同一张卡的告警每分钟生成新键（「静默26min/36min/…」）——
        ACKED 一次只停一响，铃照旧 = MECH-OPT1 修的键增殖 BUG。"""
        nonlocal n_warn
        # BUG-REAPER-SCOPE-1 生成侧闸：落笔前现读卡状态，只 CLAIMED 进报警评估。
        # 环首快照晚到=属主已迁出（done/转正/撤销/查无），不响、不计 WARN 连轮、不进
        # warned_this_run（连轮分账随迁出自然清账）——看护铃不给局外卡响。
        in_scope, stt = in_watch_scope(cid)
        if not in_scope:
            print("SKIP-STATE %s: 生成现读状态=%s≠CLAIMED → 不进报警评估（不响/不连坐，存量警由警账结算 autoack 停铃）" % (cid, stt))
            return
        up = warn_note(cid, run_seq, warns, a.apply)
        warned_this_run.add(cid)
        n_warn += 1
        if up:
            print("ALARM %s: %s → 连续两轮 WARN 仍未收口，请 PM 经 inbox 处置(不 release)" % (cid, msg))
            if _AL:
                _AL.record("ALARM %s: %s → 连续两轮 WARN 仍未收口，请 PM 经 inbox 处置(不 release)"
                           % (cid, alarm or msg))  # MECH-AUD1：键用稳定文案，同题聚合 count++
        else:
            print("WARN %s: %s → 不 release，转 PM/看护确认" % (cid, msg))

    def silence_check(c, owner):
        """BUG-LANE-SILENCE-1 断流探针落笔处（pid 活≠工作续；只有 lane 卡进得到——session 早退
        WARN-only 不动手，ZOMBIE/NOANCHOR 路径与本探针正交）。True=本轮判了断流（dry 点名或
        apply 已执行）。detach 前现读复核（同 warn 的 SCOPE 闸：环首快照晚到属主已迁出不动手）。"""
        nonlocal n_sil
        if sil_min <= 0:
            return False
        cid = c.get("id") or ""
        v, why = continuation_verdict(cid, owner, c, sil_min)
        if v == "detached":
            print("SILENCE-DETACH %s: lane-%s 锚活但断流(%s) → %s" % (cid, owner, why, verb))
            n_sil += 1
            if not a.apply:
                return True
            in_scope, stt = in_watch_scope(cid)
            if not in_scope:
                print("SKIP-STATE %s: detach 执行现读状态=%s≠CLAIMED → 不动手" % (cid, stt))
                return True
            if _AL:
                _AL.record("ALARM %s: 在制道断流探针 silence-detach 锚活但续写冻结超阈(CARD_SILENCE_RELEASE_MIN)自动 release"
                           % cid)   # 稳定键零分钟值(OPT1)；release 后同轮 autoack 状态迁移停铃=留痕不空响
            ok = do_release(cid, "silence-detach")
            print("   SILENCE-RELEASE %s: %s" % (cid, "回 OPEN 等重派" if ok else "release 未成(留痕下拍再判)"))
            return True
        if v in ("moving", "active"):
            print("CONT %s: lane-%s %s → 续写在，不打扰" % (cid, owner, why))
        elif v == "no-source":
            print("SILENCE-SKIP %s: lane-%s %s → 定性源不全保守放行（宁漏不误杀）" % (cid, owner, why))
        else:  # skip-fresh / done-event
            print("SILENCE-SKIP %s: lane-%s %s → 不 detach" % (cid, owner, why))
        return False

    # ---- GC1: 残渣 v3 道锁（指向非 CLAIMED/属主不符/无卡）——全环专属（快环「只做 DONE 扫描」）----
    if a.ring == "full" and os.path.isdir(STATE):
        for fn in sorted(os.listdir(STATE)):
            p = os.path.join(STATE, fn)
            if not (fn.startswith("lane-") and fn.endswith(".lock") and os.path.isdir(p)):
                continue
            try:
                cid = open(os.path.join(p, "card"), encoding="utf-8").read().strip()
            except OSError:
                cid = ""
            c = card_of(cid) if cid else None
            if not (c and c.get("status") == "CLAIMED"):
                print("STALE lock %s: 指向卡 %s(状态=%s) → %s"
                      % (fn, cid or "?", (c or {}).get("status", "无卡"),
                         ("清锁" if a.apply else "(dry)会清")))
                if a.apply:
                    shutil.rmtree(p, ignore_errors=True)
                    n_fixed += 1

    # ---- 主循环：逐张 CLAIMED 卡对名牌判活（REQ-BEAT-ANCHOR-1：任期/心跳龄/圈号三问，
    #      pid/pid_ns/pid_comm/session 全部退出生死与归属判定，只作留档字段；
    #      SILENCE 探针仅在 progress 缺账两处挂点作备用路径=MECH-INT-BEAT13A-1） ----
    beat_obs = load_beat_obs()
    for c in (claimed_cards() if a.ring == "full" else []):
        cid = c.get("id") or ""
        owner = c.get("owner")
        lock_cid, meta, lockdir = load_lane_lock(owner)
        claim = load_claim(cid)
        cdir = os.path.join(CLAIMS, cid)
        if os.path.isdir(lockdir):
            kind = meta.get("kind") or claim.get("kind") or "lane"
            # 静默=心跳文件(道锁或卡锁任一侧)与 claims 目录 mtime 的较新者（过渡档锚）
            sil = min(heartbeat_min(lockdir), heartbeat_min(cdir), silence_min(cdir))
        else:
            kind = claim.get("kind") or "session"  # 无锁锚：R3 定版=会话卡，保守不回收
            sil = silence_min(cdir)
        if kind == "session":
            warn(cid, "owner=%s session 执行卡(无回收锚) 静默%smin"
                 % (owner, ("%.0f" % sil) if sil != float("inf") else "∞"),
                 alarm="owner=%s session 执行卡(无回收锚) 静默超阈" % owner)  # 键去分钟值，同题聚合(OPT1)
            continue
        # 门牌=泳道号+任期号：卡面 claimed_epoch 优先（写闸发的牌），claim/meta 留档兜底
        ep = c.get("claimed_epoch")
        if ep is None:
            ep = claim.get("epoch")
        if ep is None:
            ep = meta.get("epoch")
        if ep is None:
            # 存量无名牌（机制落地前的认领/手工旧档）：无牌可对=progress 缺账的极端形态，
            # 先走 SILENCE 备用路径探针（调用点①），未判断流再问账本静默、断流即回收；
            # 旧「锚无 pid → ZOMBIE-NOANCHOR 自愈窗」分支随名牌制落地合并至此——
            # pid 有无都不再改变判定，BUG-DISPATCH-PID-1② 的语义由「断流=死」承接。
            if silence_check(c, owner):
                continue
            if sil != float("inf") and sil >= a.minutes:
                # BUG-POOL-SLOTCHURN-1 保护形挂点②：同闸同语义（无牌旧档也一样，
                # 人在写就不放流；双源皆冻才走原 legacy 回收）
                ex, exwhy = beatzombie_exempt(owner)
                if ex:
                    print("CONT-EXEMPT %s: lane-%s 存量无名牌 断流%.0fmin(阈%.0f) 但%s → 保活不放流"
                          % (cid, owner, sil, a.minutes, exwhy))
                    n_alive += 1
                    continue
                print("ZOMBIE %s: lane-%s 存量无名牌 断流 %.0fmin(阈%.0f) → %s"
                      % (cid, owner, sil, a.minutes, verb))
                if a.apply and do_release(cid, "legacy-noepoch"):
                    n_zombie += 1
            else:
                print("WAIT %s: lane-%s 存量无名牌 静默 %.0fmin<%.0f → 再观察"
                      % (cid, owner, sil if sil != float("inf") else 0, a.minutes))
                n_alive += 1
            continue
        # MECH-INT-BEAT13A-1 三向整合（SILENCE 卡面②「BEAT 落地合并退役」条款兑现）：
        # 名牌续写在账 → lane_alive 唯一权威，探针不介入（防双判据稀释/防 ALIVE 一路豁免回潮）；
        # 仅 progress 缺账（本任期 beat 账无笔）时走 SILENCE 备用路径——调用点②。
        if not lane_beat_present(owner, int(ep)):
            if silence_check(c, owner):
                continue
        alive, why = lane_alive(owner, int(ep), a.minutes, cid, beat_obs)
        if alive:
            print("ALIVE %s: lane-%s %s → 不动" % (cid, owner, why))
            n_alive += 1
        else:
            # BUG-POOL-SLOTCHURN-1 保护形挂点①：lane_alive 判死只问名牌/账本，不问人在不在写。
            ex, exwhy = beatzombie_exempt(owner)
            if ex:
                print("CONT-EXEMPT %s: lane-%s 名牌 e%s 判死(%s) 但%s → 保活不放流"
                      % (cid, owner, ep, why, exwhy))
                n_alive += 1
                continue
            print("ZOMBIE %s: lane-%s 名牌 e%s 断流：%s → %s" % (cid, owner, ep, why, verb))
            if a.apply and do_release(cid, "beat-stale"):
                n_zombie += 1

    # ---- GC2: claims 镜像残渣（卡不在 CLAIMED 但 claims/<cid> 还在）——全环专属 ----
    if a.ring == "full" and os.path.isdir(CLAIMS):
        for cid in sorted(os.listdir(CLAIMS)):
            d = os.path.join(CLAIMS, cid)
            if not os.path.isdir(d):
                continue
            c = card_of(cid)
            if c is None or c.get("status") != "CLAIMED":
                tag = "无卡" if c is None else c.get("status", "?")
                print("STALE %s: claims 镜像残渣(卡=%s) → %s" % (cid, tag, verb))
                if a.apply:
                    shutil.rmtree(d, ignore_errors=True)
                    n_stale += 1
    # ---- 警账结算（BUG-ALARM-LIFECYCLE-1）：本轮响过的自然留下（last=now 不腐灭），
    # 卡离开 CLAIMED/被再claim 的迁销，两拍未续报的腐灭。放在回收主循环之后=同轮新警不误销。
    # 全环专属：快环不结算警账（tick 语义按 */10 定版；*/1 会把「超2tick未续报」窗错缩 10 倍误销）。
    n_ack_state = n_ack_stale = 0
    if a.ring == "full":
        n_ack_state, n_ack_stale = autoack_sweep(a.tick_minutes, a.apply)

    # ---- DONE 滞留扫描（MECH-DONE-SWEEP-1）：快环专属作业。sweep+apply 持防重跑锁——
    # 补跑判据动辄数分钟，*/1 不叠锁=tick 堆叠连环起子进程；抢不到就本轮让路。
    if a.ring == "sweep":
        if a.apply and not sweep_lock_try():
            print("SKIP-ROUND sweep: 上轮补跑未毕(%s)→ 本拍让路不双跑，下拍再查" % SWEEP_LOCK)
        else:
            try:
                n_ds, n_dv, n_dr, n_dp = done_sweep_scan(a.apply, done_hours, n_cap, run_seq)
                n_tp, n_tm, n_tb, n_tq, n_ts, n_te = train_scan(a.apply)   # 火车同锁串行（REQ-MERGE-TRAIN-1）
                n_pr, n_pf, n_pd, n_pw = pool_round(a.apply)   # 工位池收桌同锁串行（REQ-POOL-ELASTIC-1）
            finally:
                if a.apply:
                    shutil.rmtree(SWEEP_LOCK, ignore_errors=True)

    # ---- 红卡龄报（MECH-REDAUTO-1）：DONE 红卡池=等复跑转正的卡；dispatcher 每 N 圈轮转
    # 复跑判据绿即转正，这里只报龄给 PM 盯账、不自造处置。dry 只 print 零写（旧纪律不破）；
    # apply 轮超阈记稳定键告警进聚合账（同题 count++，ack 闭环走 cards_dag ack）。
    n_red = 0
    _redcards = []
    if a.ring == "full":
        try:
            from ccards import graph as _G
            _redcards = sorted((cid, c) for cid, c in _G.all_cards().items()
                               if c.get("status") == "DONE")
        except Exception as exc:
            print("WARN: 红卡池不可读(%r)——红卡龄本轮缺报" % exc)
        for cid, c in _redcards:
            n_red += 1
            age = done_age_min(c)
            print("REDCARD %s owner=%s 停DONE龄=%s（REDAUTO 轮转复跑中，绿即转正；龄报供 PM 盯账）"
                  % (cid, c.get("owner") or "-", ("%.0fmin" % age) if age is not None else "?"))
            if a.apply and age is not None and age >= REDCARD_ALARM_MIN:
                print("ALARM REDCARD-STALE %s: DONE 停留 >=%dmin 复跑仍未绿 → 进聚合账"
                      % (cid, int(REDCARD_ALARM_MIN)))
                if _AL:
                    _AL.record("REDCARD-STALE %s: DONE 红卡停留超阈(>= %dmin)且 REDAUTO 复跑未绿——PM 复核判据/催修复卡"
                               % (cid, int(REDCARD_ALARM_MIN)))
    save_warn_state(run_seq, warns, a.apply, warned_this_run, warn_file)
    if a.ring == "full":  # 圈号跨圈对照账只由全环维护（sweep 快环 beat_obs 恒空，覆写=销全环的账）
        save_beat_obs(beat_obs, a.apply)
    n_sc, n_sk = alarm_scope_sweep(a.apply)   # 每圈扫开放警账：卡已离制的看护警=僵尸，清账
    # 摘要行全字段两态都打（MECH-DONE-SWEEP-1 卡面第5条）；full 环 done_*=0 属「照旧未扫」，
    # ring= 标来源不糊账；autoack=警账结算(迁移/腐灭)，alarm_scope=REAPER 结算侧补道（查无此卡残留）。
    # pool=工位池收桌 净收+强收/脏留/候收（REQ-POOL-ELASTIC-1；full 环 0+0/0/0 属「照旧未扫」，
    #   sweep 环每拍现收，桌明细看 /api/ops pool 节）
    print("recycle: stale=%d zombie=%d alive=%d warn=%d lockfix=%d redcards=%d alarm_scope=%d/%d "
          "autoack=%d/%d mode=%s run=%d ring=%s done_swept=%d done_verified=%d done_rejected=%d done_pending_side=%d silence=%d "
          "pool=%d+%d/%d/%d train_passed=%d train_merged=%d train_blocked=%d train_queued=%d train_stale=%d train_notenerg=%d" % (
              n_stale, n_zombie, n_alive, n_warn, n_fixed, n_red, n_sc, n_sk,
              n_ack_state, n_ack_stale, "APPLY" if a.apply else "DRY", run_seq,
              a.ring, n_ds, n_dv, n_dr, n_dp, n_sil, n_pr, n_pf, n_pd, n_pw,
              n_tp, n_tm, n_tb, n_tq, n_ts, n_te))


if __name__ == "__main__":
    main()
