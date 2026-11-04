# ccards.locks — 三把锁 + 队列（运行时对象本体）。
#   写锁 .writer.lock：全部写命令串行化；持有者死/超5min 自清。
#   卡锁 claims/<cid>/：mkdir 原子=一卡一主；claim.json=锁内容(消费者兼容档)。
#   道锁 state/lane-<owner>.lock/：mkdir 原子=一道一活；card/meta.json 双文件。
#   队列 state/lane-<owner>.queue：派生视图，reindex 可重建。
import argparse, json, os, re, shutil, sys, time
from .config import (CLAIMS, SELFHEAL_MIN, STATE, WLOCK, claim_dir, die,
                     now, pid_alive)


# ---------- 台账写锁（全部写命令串行化） ----------
def team_lock_acquire(cmd):
    os.makedirs(STATE, exist_ok=True)
    deadline = time.time() + 45.0
    while True:
        try:
            os.mkdir(WLOCK)
            with open(os.path.join(WLOCK, "meta.json"), "w", encoding="utf-8", newline="") as f:
                json.dump(dict(cmd=cmd, pid=os.getpid(), ts=now()), f)
            return
        except FileExistsError:
            try:
                hm = json.load(open(os.path.join(WLOCK, "meta.json"), encoding="utf-8"))
            except (OSError, ValueError):
                hm = {}
            try:
                age = (time.time() - os.path.getmtime(WLOCK)) / 60.0
            except OSError:
                age = 99.0
            # MECH-RTY1 根因修（产品级，非测试件）：meta 读不出=空 {} ≠ 死者——持有者可能
            # **刚 mkdir 还没来得及写 meta**（mkdir 与写 meta 之间天然有窗口，9P 慢盘更宽）。
            # 旧判据 pid_alive(None)=False 把初始化中的活锁当孤儿抢走 → 并发双写者 → 真丢更新
            # （selftest #47/#63 六轮一遇的瞬态红即其现场；真实账同窗同丢）。
            # 新判据：「meta 解析成功 且 pid 已死」才夺；读不出就等待，超自清窗(5min)兜底夺。
            if (hm and not pid_alive(hm.get("pid"))) or age > 5.0:
                print("WARN: 回收陈旧台账写锁(持有 cmd=%s pid=%s 龄%.1fmin)"
                      % (hm.get("cmd", "?"), hm.get("pid"), age), file=sys.stderr)
                _stale_lock_strip(hm)
                continue
            if time.time() > deadline:
                die("台账写锁被 %s(pid=%s) 占用超 45s——持有者若已死会自清(≤5min)；勿并发改账"
                    % (hm.get("cmd", "?"), hm.get("pid")))
            time.sleep(0.1)

def team_lock_release():
    # REQ-CLAIM-COUNTER-1 T3 实锤修复（并发锤锁现场解剖=Temp/t3repro 残锁）：Windows 下
    # 等待方每 0.1s `json.load(open(meta.json))`，与持有者 rmtree 同窗时删除命中瞬时共享
    # 冲突（ERROR_ACCESS_DENIED）——旧形 ignore_errors=True 把它静默吞掉 = **幻影锁泄漏**：
    # 目录+meta 留在盘上，持有者进程还活着（pid_alive=True）但已不再持锁，后续写者全部
    # 等到 45s 超时，5min 自清也判「持有者未死」永不回收——双进程并发下必卡死（graph.json
    # 原子性的地基就此烂掉）。修：三遍扫+小步退避（等待方句柄开读即关，毫秒级，重试必成）；
    # 三遍仍删不动（极端：文件被杀软扣住）=把 meta 的 pid 写 0 交陈旧回收通道——
    # pid_alive(0)=False 恒成立，下一轮等待者必夺，幻影锁不再永生。
    for _try in range(3):
        try:
            shutil.rmtree(WLOCK)
            return
        except OSError:
            time.sleep(0.05)
    try:
        os.makedirs(WLOCK, exist_ok=True)
        with open(os.path.join(WLOCK, "meta.json"), "w", encoding="utf-8", newline="") as f:
            json.dump(dict(cmd="released?", pid=0, ts=now()), f)
    except OSError as exc:
        print("WARN: 台账写锁释放三连败且毒化落空(%r)——等 5min 自清窗，勿并发改账" % exc,
              file=sys.stderr)


def _stale_lock_strip(hm):
    """原子摘除已判陈旧/毒化的写锁目录（acquire 自愈支专用）。
    REQ-CLAIM-COUNTER-1 T3 双进程锤锁实锤（第二病灶）：旧形「读 meta 判死 → rmtree
    (ignore_errors)」是 TOCTOU——两个等待方先后都判陈旧，A 摘除后 mkdir 拿到**新活锁**，
    B 后到的 rmtree 把 A 的新锁连锅端掉再自己 mkdir = **同一把锁双持**：两个写进程并发
    读改写 graph.json，claim/release 计数增量整批丢失（现场 87 笔实操仅 50 笔在册）。
    新形：os.rename 摘牌——rename 是原子操作，同一陈旧锁多方竞争只有一方摘得走，
    摘空后 mkdir 的归属完全由 mkdir 原子性裁决，与旧形不同的是**摘的动作不再可能
    删掉一个 rename 之后才出现的活锁**；摘到的目录先复核牌面 meta 与判定时刻一致
    （一致=同一块陈旧牌，删；不一致=判定与摘牌之间被第三方换新锁，原样放回绝不误杀；
    放回也失败=留存残渣并 WARN，宁可留垃圾不可杀活锁）。hm={}（meta 读不出，只可能
    走 age>5min 支进来）= 残渣直接删。"""
    dead = WLOCK + ".stale-%d" % os.getpid()
    shutil.rmtree(dead, ignore_errors=True)  # 清本进程既往残渣（pid 命名空间，碰不到别人的锁）
    try:
        os.rename(WLOCK, dead)
    except OSError:
        return  # 被别方先摘/目录已消失/内部句柄被占：本轮放弃，下一轮重读重判
    if not hm:
        shutil.rmtree(dead, ignore_errors=True)
        return
    try:
        vm = json.load(open(os.path.join(dead, "meta.json"), encoding="utf-8"))
    except (OSError, ValueError):
        vm = None
    if vm == hm:
        shutil.rmtree(dead, ignore_errors=True)  # 牌面与判定一致：同一块陈旧牌，摘实
        return
    try:
        os.rename(dead, WLOCK)  # 易主了：放回，让真持有者收口
    except OSError:
        print("WARN: 陈旧写锁摘牌后发现易主且放回失败——残渣留存 %s（宁留垃圾不杀活锁）" % dead,
              file=sys.stderr)


# ---------- 卡锁（一卡一主） ----------
def lock_claim(cid, owner, pid, session, kind, selfheal=False, pid_ns=None, pid_comm=None,
               epoch=None):
    """卡锁：mkdir claims/<cid> 原子防「一卡二主」；claim.json=锁内容(消费者兼容档)。
    REQ-BEAT-ANCHOR-1 名牌制：自愈只认「静默超龄」（pid 降为留档字段，不再参与判定——
    pid 会被别的进程复用，拿它当「别抢这把锁」的依据=僵尸装活占道，正是旧 NS-1 事故根）。
    pid_ns/pid_comm=BUG-ANCHOR-NS-1 时代的进程名核验素材——REQ-BEAT-ANCHOR-1 起为
    **留档字段**（继续记录供排障），生死与归属判定一律不再引用（名牌制：判活只看账本
    事件流——心跳龄/圈号/任期三条，见 ops/recycle.py lane_alive）。"""
    d = claim_dir(cid)
    os.makedirs(CLAIMS, exist_ok=True)
    try:
        os.mkdir(d)  # mkdir 原子性 = 抢锁（写盘在改卡面之前，败者不会碰卡 JSON）
    except FileExistsError:
        if not selfheal:
            die("已有认领记录，拒绝重复领取")
        cj = os.path.join(d, "claim.json")
        pidv = None
        try:
            j = json.load(open(cj, encoding="utf-8"))
            pidv = j.get("pid")
            age_min = (time.time() - os.path.getmtime(cj)) / 60.0
        except (OSError, ValueError):
            # claim.json 缺失/截断：这可能是赢家 mkdir 后正要写的【活锁写入窗口】，
            # 绝不能当 0 龄死锁抢——用目录 mtime 判龄：刚建=新鲜不抢，崩溃残渣超阈才抢。
            try:
                age_min = (time.time() - os.path.getmtime(d)) / 60.0
            except OSError:
                age_min = 0.0
        # 名牌制(REQ-BEAT-ANCHOR-1)：只认「静默超龄」——旧「pid 活=永不抢」把 pid 复用
        # 的僵尸变成永久占主(2650385 案)；判活权威已迁到账本事件流，这里不再问 pid。
        if age_min < SELFHEAL_MIN:
            die("已有认领记录，拒绝重复领取")
        print("WARN: 回收崩溃孤儿锁(静默超%d分钟): %s" % (SELFHEAL_MIN, cid), file=sys.stderr)
        shutil.rmtree(d, ignore_errors=True)
        try:
            os.mkdir(d)
        except FileExistsError:
            die("已有认领记录，拒绝重复领取")  # 抢窗口时被第三个进程抢先，不硬来
    cj = os.path.join(d, "claim.json")
    body = dict(owner=owner, pid=pid, ts=now(), kind=kind, session_id=session)
    if pid_ns:
        body["pid_ns"] = pid_ns
    if pid_comm:
        body["pid_comm"] = pid_comm
    if epoch is not None:
        body["epoch"] = int(epoch)   # REQ-BEAT-ANCHOR-1 名牌制：任期号入账；pid/pid_ns/pid_comm 降为留档字段
    with open(cj, "w", encoding="utf-8", newline="") as f:
        json.dump(body, f)
    return d


# ---------- 道锁 / 队列（泳道正向对象；派生自卡，可 reindex 重建） ----------
def _lane_slug(owner):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", owner or "-")

def _lane_file(kind, owner):
    return os.path.join(STATE, "lane-%s.%s" % (_lane_slug(owner), kind))

def _read_lines(p):
    try:
        with open(p, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except OSError:
        return []

def _write_lines(p, lines):
    os.makedirs(STATE, exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8", newline="") as f:  # MECH-LEDGER-CRLF-1 钉LF
        f.write("".join(l + "\n" for l in lines))
    os.replace(p + ".tmp", p)  # 与卡面同款原子写

def lane_lock_dir(owner):
    return os.path.join(STATE, "lane-%s.lock" % _lane_slug(owner))

def lane_lock_read(owner):
    """返回 (卡号|None, meta)。v3 目录锁唯一形态（v1 文件锁兼容已随 MECH-PURE1 清零）。"""
    d = lane_lock_dir(owner)
    try:
        cid = open(os.path.join(d, "card"), encoding="utf-8").read().strip()
    except OSError:
        cid = ""
    try:
        meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
        if not isinstance(meta, dict):
            meta = {}
    except (OSError, ValueError):
        meta = {}
    return (cid or None), meta

def _lock_silence_min(d):
    mt = 0.0
    for dp, _dd, fs in os.walk(d):
        for fn in fs:
            try:
                mt = max(mt, os.path.getmtime(os.path.join(dp, fn)))
            except OSError:
                pass
    if mt == 0.0:
        try:
            mt = os.path.getmtime(d)
        except OSError:
            return 0.0
    return (time.time() - mt) / 60.0


def lane_owner_dead(owner, meta=None):
    """死主判定（BUG-LANE-ZOMBIECLAIM-1 规格③，活性三信号唯一口径：
    活 pid / 活 beat / 活绑定——全灭才算死，宁漏不误判）。
    实锤病灶：回收循环里「锁面静默<自愈窗」的**新鲜**是重试方刷 ts/重claim 刷出来的，
    不是活主心跳（2026-11-04 m8/m9：meta pid 全死、beat 停 10~22h、session='-'，
    recycle zombie=0 判不走，看板成片「未绑定会话」黄字+池工人拿不到道）。
    本函数不问 mtime——只问三条活性凭据：
      ①活 pid：meta.pid 在场且 pid_alive 真（pid 缺失=手工/PM 无锚档，证据不足不判死，保守活）；
      ②活 beat：state/lane-<道>.beat 的 t 龄 < SELFHEAL_MIN（工人频度规 ≤5min，超窗=没在写）；
      ③活绑定：道锁 meta.session_id 已绑（bind 通道在场=会话真起来过）。
    返回 True=确死（三信号全灭且有 pid 锚）；任何一信号活或读不动=保守 False。
    红绿锁 tests/dispatch-lanebinding-test.py（G 组：死主锁被接管、活主锁夺不动）。"""
    try:
        if meta is None:
            _cid, meta = lane_lock_read(owner)
    except OSError:
        return False
    meta = meta or {}
    pid = meta.get("pid")
    if pid is not None and pid_alive(pid):
        return False                             # 信号①活：进程锚在场且不确死
    try:
        b_t = float((lane_beat_read(owner) or {}).get("t") or 0.0)
    except (TypeError, ValueError):
        b_t = 0.0
    if b_t > 0 and (time.time() - b_t) < SELFHEAL_MIN * 60:
        return False                             # 信号②活：本道心跳龄在频度规窗内
    if str(meta.get("session_id") or "-") not in ("-", ""):
        return False                             # 信号③活：会话已绑定
    return pid is not None                       # 无 pid 锚=证据不足，保守不判死

def lane_lock_acquire(cid, owner, pid, session, kind, cards, pid_ns=None, pid_comm=None,
                      epoch=None):
    """原子占道。锁面静默<自愈窗 **且旧主活着/证据不足** →die；过期残渣 或 死主重写
    （BUG-LANE-ZOMBIECLAIM-1 规格③：活 pid/活 beat/活绑定三信号全灭）→先正规 release
    收口旧卡再抢=直接判 zombie 接管，不等自愈窗。
    REQ-BEAT-ANCHOR-1 名牌制：占道与否**不再问 pid 活否**（pid 复用=僵尸装活的老根），
    一泳道一会话由「道锁在场 + 事件流判活」保证；epoch=本次任期号，随 meta 入道锁留档。
    BUG-LANE-ZOMBIECLAIM-1 病根实锤（2026-11-04 04:4x 看板成片「未绑定会话」）：回收循环
    每分钟重 claim 刷锁面 ts ⇒ _lock_silence_min 恒<5min=「新鲜」当免死凭据=死主赖道、
    recycle zombie=0。新规：锁面 ts 只能由活主续；死主重写=直接判 zombie 接管。
    红绿锁 tests/dispatch-lanebinding-test.py（G 组双向：死主锁被接管/活主锁夺不动）。"""
    d = lane_lock_dir(owner)
    os.makedirs(STATE, exist_ok=True)
    try:
        os.mkdir(d)
    except FileExistsError:
        ocid, om = lane_lock_read(owner)
        ocard = cards.get(ocid, {}) if ocid else {}
        sil = _lock_silence_min(d)
        dead_owner = sil < SELFHEAL_MIN and lane_owner_dead(owner, om)
        if sil < SELFHEAL_MIN and not dead_owner:
            die("lane %s 锁新鲜(静默<%dmin，活性三信号在场/证据不足)——一道一活；"
                "等自愈窗或 PM release %s 收口" % (owner, SELFHEAL_MIN, ocid or ""))
        if dead_owner:
            print("WARN: ZOMBIE-TAKEOVER 接管死主道锁(lane=%s 旧卡=%s)：锁面静默仅%.1fmin"
                  "但活 pid/活 beat/活绑定三信号全灭=死主重写 ts，不等自愈窗"
                  % (owner, ocid or "?", sil), file=sys.stderr)
        else:
            print("WARN: 回收僵尸会话道锁(lane=%s 旧卡=%s，静默≥%dmin 无续写)"
                  % (owner, ocid or "?", SELFHEAL_MIN), file=sys.stderr)
        if ocid and ocard.get("status") == "CLAIMED":
            from .commands import cmd_release  # 延迟导入防环
            cmd_release(argparse.Namespace(id=ocid, who="lane-steal"))  # 正规流程收口：双锁清+回队头
        shutil.rmtree(d, ignore_errors=True)
        try:
            os.mkdir(d)
        except FileExistsError:
            die("道锁在清理窗口被第三方抢先，不硬抢: " + owner)
    with open(os.path.join(d, "card"), "w", encoding="utf-8", newline="") as f:  # CRLF钉LF
        f.write(cid + "\n")
    meta = dict(owner=owner, pid=pid, session_id=session, kind=kind, ts=now())
    if pid_ns:
        meta["pid_ns"] = pid_ns
    if pid_comm:
        meta["pid_comm"] = pid_comm
    if epoch is not None:
        meta["epoch"] = int(epoch)   # 名牌：本次占道的任期号（过渡期心跳兜底也以此对班）
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8", newline="") as f:  # CRLF钉LF
        json.dump(meta, f)
    return d


# ---------- 名牌账（REQ-BEAT-ANCHOR-1 判活两层：任期号 + 进度事件流） ----------
def lane_epoch_read(owner):
    """道任期台账 state/lane-<道>.epoch → dict(epoch, cid, ts)。无账={}（首次派发前无历史）。"""
    from .config import read_json
    return read_json(_lane_file("epoch", owner), {}) or {}

def lane_epoch_advance(owner, cid):
    """派发入账：任期 +1 并落账 → 返回新任期号。一泳道一会话由道锁保证，这里只发牌。"""
    from .config import atomic_write_json
    try:
        prev = int(lane_epoch_read(owner).get("epoch") or 0)
    except (TypeError, ValueError):
        prev = 0
    ep = prev + 1
    atomic_write_json(_lane_file("epoch", owner), dict(epoch=ep, cid=cid or "", ts=now()))
    return ep

def lane_beat_read(owner):
    """事件源①（账本进度流）state/lane-<道>.beat → dict(lane, epoch, ring, cid, ts, t)。
    recycle 判活经此读——**事件源可插拔**：未来 worker 换主机，只换本函数/写侧通道，
    判活三问逻辑（recycle.lane_alive）一行不动。"""
    from .config import read_json
    return read_json(_lane_file("beat", owner), {}) or {}

def lane_beat_write(owner, rec):
    from .config import atomic_write_json
    atomic_write_json(_lane_file("beat", owner), rec)
    return rec

def lane_lock_release(owner, cid):
    if not owner:
        return
    d = lane_lock_dir(owner)
    if not os.path.isdir(d):
        return
    try:
        cur = open(os.path.join(d, "card"), encoding="utf-8").read().strip()
    except OSError:
        cur = ""
    if cur and cid and cur != cid:
        return  # 泳道占的是别的卡，别顺手解错锁
    shutil.rmtree(d, ignore_errors=True)

def lane_q_push(owner, cid, front=False):
    if not owner:
        return
    lines = _read_lines(_lane_file("queue", owner))
    if cid in lines:
        return
    _write_lines(_lane_file("queue", owner), [cid] + lines if front else lines + [cid])

def lane_q_pop(owner, cid):
    p = _lane_file("queue", owner)
    lines = _read_lines(p)
    out = [l for l in lines if l != cid]
    if len(out) != len(lines):
        _write_lines(p, out)

def lane_q_drop_everywhere(cid):
    if not os.path.isdir(STATE):
        return
    for fn in sorted(os.listdir(STATE)):
        if fn.endswith(".queue"):
            p = os.path.join(STATE, fn)
            lines = _read_lines(p)
            out = [l for l in lines if l != cid]
            if len(out) != len(lines):
                _write_lines(p, out)

def lane_queue_lines(owner):
    return _read_lines(_lane_file("queue", owner))
