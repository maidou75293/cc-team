# ccards.engine — 决策查询单面（MECH-ENG1）。
# 定版边界（业主 2026-10-31：hook 触发+自带证据，引擎判定+落账）：节点只「提出」状态变化
# （claim/done/verdict 走 cli 全闸），本面**只读**——把整账聚成一份快照：谁可领/谁在制/
# 整体可否收口/等什么处置。dispatcher/看板/外部执行层（如 LangGraph 子流程）只消费这一份。
# 零新判断、零写入：所有字段都从 graph(唯一事实访问器)/alerts/retire 现算，保证没有第二套真相。
from .config import nowz
from .graph import all_cards, compute, deps_blockers, digest, read_state


def _lane_order(cards):
    """lane -> 候领卡号表，**每圈从账本现算**（MECH-QDEL 派生队列废除，业主口径
    「拿 graph 实例去操作就够了」2026-10-31 定版的回归执行）。
    旧 model=道即邮箱(lane-<道>.queue 文件)；账本统一后它降级成"镜像"却仍被本面当真相读
    ——第二套真相实锤两案：00:4x m2 建道镜像无条目致调度失明；09:1x set --lanes 后镜像滞后
    致 propose 空转圈(GATE拒+杀会话循环)。镜像文件保留为装饰(no-op)，谁都不许据它判定。
    FIFO 语义=按发卡时刻(created_at)，同刻按卡号，与旧"入队序"等价且永不过期。
    弹性工位池（REQ-POOL-ELASTIC-1，2026-11-02 业主定版）：纯 lanes=["ANY"] 的卡**不进命名道
    队列**（它不属于任何一条道，归全局池 pool_ready）；判定口径与 ops/pool.is_pool_card 同源，
    改语义两处同改。混写 ANY+具体道=显式互斥优先，仍站具名道。"""
    out = {}
    for cid, c in cards.items():
        if not isinstance(c, dict):
            continue
        lanes = [str(x).strip() for x in (c.get("lanes") or [])]
        if lanes and "ANY" in lanes and len(lanes) == 1:
            continue
        for ln in lanes:
            if ln == "ANY":
                continue
            out.setdefault(ln, []).append(cid)
    for ln in out:
        out[ln].sort(key=lambda cid: (str(cards[cid].get("created_at") or ""), cid))
    return out


def _stoploss(c):
    """STOPLOSS 停派闸（业主 seq=30，2026-11-04 BUG-AGENT-KEY-FIX-1 四连拒实锤）：
    卡面 hold 含 STOPLOSS=停复派。旧口径 fuse S2 靠 release+HOLD 态停派（HOLD 本就不进
    ready）；本闸补「下一次 REJECT 后不再重派」形态——卡回 OPEN 也不供道派/池派，
    PM 归因后 set --hold "" 解除即恢复入队序。视图过滤=从账面现算，零新判断零写入。"""
    return "STOPLOSS" in str(c.get("hold") or "")


def tick():
    """整图一个 tick 的决策快照（只读，什么都不写；视图过期=就地算，不代刷不撒谎）。"""
    cards = all_cards()
    snap = read_state()
    if snap is None or snap.get("digest") != digest(cards):
        snap = compute(cards, (snap or {}).get("generation", 0))
    claimable = {}
    for lane, q in _lane_order(cards).items():
        head = None
        for cid in q:
            c = cards.get(cid)
            if c and c.get("status") == "OPEN" and c.get("verify") \
                    and not _stoploss(c) and not deps_blockers(c, cards):
                head = cid
                break
        claimable[lane] = head
    # 全局 FIFO（弹性工位池）：纯 ANY 卡按发卡时刻排开、资格同闸（OPEN+有 verify+依赖清+无 STOPLOSS 挂账）。
    # 同卡号跨道去重=账本卡号唯一+此处一卡一位；已 CLAIMED 的池卡不入名单（在制不双桌）。
    pool_ready = []
    for c in sorted((c for c in cards.values()
                     if isinstance(c, dict) and c.get("status") == "OPEN"
                     and c.get("verify") and not _stoploss(c) and not deps_blockers(c, cards)),
                    key=lambda c: (str(c.get("created_at") or ""), str(c.get("id", "")))):
        ln = [str(x).strip() for x in (c.get("lanes") or [])]
        if ln and "ANY" in ln and len(ln) == 1:
            pool_ready.append(c.get("id"))
    from . import alerts, retire
    try:
        open_alarms = sorted(e.get("msg", "") for e in alerts.open_entries())
    except OSError:
        open_alarms = []
    try:
        # BUG-ALARM-AUTOACK-MASK-1：open=0 不等于「没问题」——当日机器自动销账笔数并列进摘要，
        # 叫醒面/面板同一口数据源看得见「一夜自动销账 N 次」（反复到期的已钉住，留在 open_alarms 里）。
        autoack_today = alerts.autoack_today_total()
    except OSError:
        autoack_today = 0
    try:
        retire_ready = [it["name"] for it in retire.assess(retire._root()) if it["ready"]]
    except OSError:
        retire_ready = []
    remaining = sorted(cid for cid, c in cards.items()
                       if c.get("status") not in ("VERIFIED", "CANCELED"))
    return {
        "tick": nowz(), "generation": snap.get("generation"), "digest": snap.get("digest"),
        "counts": snap.get("counts", {}), "ready": snap.get("ready", []),
        "claimable": claimable, "pool_ready": pool_ready,
        "claimed": {cid: c.get("owner") for cid, c in sorted(cards.items())
                    if c.get("status") == "CLAIMED"},
        "cycle": snap.get("cycle", []), "canceled_blocked": snap.get("canceled_blocked", []),
        "critical_path": snap.get("critical_path", []),
        "run_complete": not remaining, "remaining": remaining,
        "alarms_open": open_alarms, "alarms_autoack_today": autoack_today, "retire_ready": retire_ready,
    }
