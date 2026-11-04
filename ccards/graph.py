# ccards.graph — 账本事实图：graph.json 的唯一访问器（MECH-GRF1，业主定版「别再要 store，
# 拿 graph 实例去操作」；store.py 并入后退役删除——ENG2 之后两者同吃一本文件，双模块握手
# 只是渐进迁移的残留，不是设计）。
#
# 类 Graph = 一次「现读」：构造即从盘上读入当前账本；**短命、不缓存、不跨调用持有**——
#   跨进程一致性靠「每个操作现读 + 写回只在 13 道写闸的写锁内」，不靠对象状态。
#   常驻可变对象=把 D3 丢更新事故搬进分布式：两个进程各持一份快照慢慢改，谁后保存谁赢。
#   所以对外形态仍是「模块函数 + 每调用一开实例」；想面向对象用就 `g = Graph()` 起步，
#   但 g 只是一张快照的读写面，save/refresh 内部仍各自现读最新账本再动笔。
# 判定归闸不动摇：本模块零状态机判断（能不能领/勾/验收全在 commands 闸门），这里只管
#   seal（事实自封，写口外改动必检）· refresh（账变→重算视图+重封）· check（对账只读，
#   永不重封=判据不自愈）· compute/digest（纯函数，视图=账的快照）。
# 旧 dag.json/dependency.json 与 tasks/ 镜像均已消亡（MECH-PURE1），本模块无迁移/回退通道。
import hashlib, json, os, sys
from .config import FINISHED, GRAPH, atomic_write_json, die, now, read_json


def seal(nodes):
    return hashlib.sha1(json.dumps(nodes, sort_keys=True, ensure_ascii=False)
                        .encode("utf-8")).hexdigest()[:16]


def digest(cards):
    # MECH-AUD1：扩入 verify/files 的稳定 hash——此前裸改判据/域不触 digest，新鲜度对账
    # 只防「状态裸改」，防不了「闸门自身被偷换」（verify 从真命令改成 echo ok 是静默的）。
    rows = sorted([cid, c.get("status"), "|".join(c.get("deps", [])),
                   c.get("owner") or "-", "|".join(c.get("lanes") or []),
                   hashlib.sha1(str(c.get("verify", "")).encode("utf-8")).hexdigest()[:8],
                   hashlib.sha1(json.dumps(c.get("files", []), ensure_ascii=False).encode("utf-8")).hexdigest()[:8]]
                  for cid, c in cards.items())
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def deps_ok(c, cards):
    return all(cards.get(d, {}).get("status") in FINISHED for d in c.get("deps", []))


def deps_blockers(c, cards):
    """不可领的 deps 原因；CANCELED 依赖点名(否则永久卡死无人知)。"""
    miss = []
    for d in c.get("deps", []):
        st = cards.get(d, {}).get("status")
        if st in FINISHED:
            continue
        if st == "CANCELED":
            miss.append("%s(依赖已CANCELED，永不满足——作废本卡或改挂 deps)" % d)
        else:
            miss.append(d)
    return miss


class Graph:
    """graph.json 一次性访问器（工厂=类本身：Graph() 即「现读开一本」；无参构造不缓存文档复用）。"""

    def __init__(self):
        self.doc = read_json(GRAPH)

    # ---------- 读侧 ----------
    def cards(self):
        """事实图全部卡；坏节点（非 dict/无 status）点名跳过（#18 语义：调度不陪葬，但必须出声）。"""
        g = self.doc
        if not isinstance(g, dict) or not isinstance(g.get("cards"), dict):
            return {}
        out, bad = {}, []
        for cid, n in g["cards"].items():
            if isinstance(n, dict) and n.get("status"):
                out[cid] = n
            else:
                bad.append(cid)
        if bad:
            print("ALARM: 事实图坏节点已跳过(修 graph.json 该节点或删卡重立): %s" % ",".join(sorted(bad)),
                  file=sys.stderr)
        return out

    def node(self, cid):
        """单卡现读；不 die（hook/巡检用）。"""
        g = self.doc
        if isinstance(g, dict) and isinstance(g.get("cards"), dict):
            n = g["cards"].get(cid)
            if isinstance(n, dict):
                return n
        return None

    def view(self):
        """已开账的文档（含 cards 段）或 None——旧 read_state 语义。"""
        j = self.doc
        return j if isinstance(j, dict) and isinstance(j.get("cards"), dict) else None

    # ---------- 写侧（只许在闸内被调：cli 的 13 道 writer 锁串行化） ----------
    def put(self, c):
        """单卡落账：现读→改这一点→重封→原子写。未开账=从零开（唯一世界，无影子可种）。"""
        doc = self.doc
        if not isinstance(doc, dict) or not isinstance(doc.get("cards"), dict):
            doc = {"cards": {}}
        c["updated_at"] = now()
        doc["cards"][c["id"]] = c
        doc["seal"] = seal(doc["cards"])
        atomic_write_json(GRAPH, doc)

    def recompute(self, cards=None, force=False):
        """账变则重算视图字段合入事实文档并重封 seal；digest 相同=空操作（不虚增 generation）。"""
        cards = self.cards() if cards is None else cards
        dg = digest(cards)
        g = self.view()
        if g and not force and g.get("digest") == dg and g.get("seal") == seal(g.get("cards") or {}):
            return g
        gen = ((g or {}).get("generation", 0) or 0) + 1
        doc = g if g else {"cards": cards}
        doc.update(compute(cards, gen))
        doc["seal"] = seal(doc["cards"])
        atomic_write_json(GRAPH, doc)
        try:  # MECH-VIS1：面板=派生视图随账重生成（与 graph 同笔保鲜）；渲染失败只 WARN 永不拦写账
            from . import panel
            panel.render(doc)
        except Exception as exc:
            print("WARN: panel 视图重生成失败(不拦写账): %r" % exc, file=sys.stderr)
        return doc


def compute(cards, generation):
    """全量快照：卡点/边/拓扑序 + 调度语义（环、就绪、CANCELED 卡死、关键路径、PM 等待）。"""
    indeg, kids = {}, {}
    for cid, c in cards.items():
        indeg[cid] = 0
        for d in c.get("deps", []):
            if d not in cards:
                continue  # 悬空依赖不造假环；显式 dag 命令另有 FATAL 点名（自动刷新容忍）
            kids.setdefault(d, []).append(cid)
            indeg[cid] += 1
    q = sorted(x for x in indeg if indeg[x] == 0)
    order, seen = [], set(q)
    head = 0
    while head < len(q):
        u = q[head]; head += 1; order.append(u)
        for v in sorted(kids.get(u, [])):
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v); seen.add(v)
    cyc = sorted(x for x in indeg if x not in seen)

    dp = {}
    best, bestpath = 0, []
    def longest(u, stack):
        if u in stack:
            return 0, []
        c = cards[u]
        if c["status"] in FINISHED or c["status"] == "CANCELED":
            return 0, []
        if u in dp:
            return dp[u]
        b, bp = 0, []
        for v in kids.get(u, []):
            l, p = longest(v, stack | {u})
            if l > b:
                b, bp = l, p
        dp[u] = (b + 1, [u] + bp)
        return dp[u]
    for u in cards:
        if cards[u]["status"] not in FINISHED and cards[u]["status"] != "CANCELED":
            l, p = longest(u, set())
            if l > best:
                best, bestpath = l, p

    ready = sorted(cid for cid, c in cards.items()
                   if c["status"] == "OPEN" and deps_ok(c, cards))
    pm_wait_alarm = sorted(cid for cid in ready
                           if not cards[cid].get("lanes") and not cards[cid].get("owner"))
    deadwait = sorted(cid for cid, c in cards.items()
                      if c["status"] not in FINISHED + ("CANCELED", "DRAFT")
                      and any(cards.get(d, {}).get("status") == "CANCELED" for d in c.get("deps", [])))
    counts = {}
    nodes = {}
    for cid, c in cards.items():
        st = c.get("status", "?")
        counts[st] = counts.get(st, 0) + 1
        nodes[cid] = {"status": st, "deps": c.get("deps", []),
                      "owner": c.get("owner"), "lanes": c.get("lanes") or []}
    return {"generated": now(), "generation": generation, "digest": digest(cards),
            "counts": counts, "nodes": nodes, "topo": order,
            "cycle": cyc, "ready": ready, "canceled_blocked": deadwait,
            "critical_path": bestpath, "key_len": best, "pm_wait_alarm": pm_wait_alarm}


# ---------- 模块门面：调用方（commands/engine/ops）按函数用，签名与旧 store+graph 逐字一致 ----------

def all_cards():
    return Graph().cards()


def exists(cid):
    return Graph().node(cid) is not None


def peek(cid):
    return Graph().node(cid)


def load(cid):
    c = peek(cid)
    if c is None:
        die("卡不存在: " + cid)
    return c


def save(c):
    Graph().put(c)


def read_state():
    return Graph().view()


def refresh(cards=None, force=False):
    return Graph().recompute(cards, force)


def check(cards=None):
    """对账判据（只读，永不重封）：(ok, msg)。缺文件=未开账；seal 不符=事实被写口外改动；
    digest 不符=视图未随账重算（崩残/裸改后未显式重刷）。"""
    cards = all_cards() if cards is None else cards
    g = read_state()
    if g is None:
        return False, "state/graph.json 未开账或无 cards 段（任意写命令/显式 dag 自动开账）"
    if g.get("seal") != seal(g["cards"]):
        return False, "graph seal 不符：facts 在上次记账后被写口外改动（核对后显式 dag 重封）"
    dg = digest(cards)
    if g.get("digest") != dg:
        return False, "graph 视图过期(存=%s… 实=%s…，账变未刷=崩溃残渣)" % (
            str(g.get("digest"))[:8], dg[:8])
    return True, "gen=%s" % g.get("generation")


def cmd_dag(a):
    if not a.check:
        # 原 v2.5 语义保留：显式跑图时悬空依赖 FATAL 点名（自动刷新档容忍不拦）
        cards_now = all_cards()
        for cid, c in cards_now.items():
            for d in c.get("deps", []):
                if d not in cards_now:
                    print("FATAL: 悬空依赖 %s -> %s(不存在)" % (cid, d))
                    sys.exit(2)
    if a.check:
        okc, msg = check()
        if not okc:
            die("DAG 过期: " + msg)
        print("dag --check: 新鲜(%s)" % msg)
        return
    if a.refresh:
        refresh(force=True)
    snap = read_state()
    changed = False
    if snap is None:
        snap = refresh(); changed = True
    else:
        cards = all_cards()
        if snap.get("digest") != digest(cards):
            snap = refresh(cards=cards); changed = True
    if a.json:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
        return
    from . import alerts  # MECH-AUD1：ALARM 不只打给人看，同时进聚合账（可 ack、可数开放项）
    cyc = snap.get("cycle", [])
    if cyc:
        print("环(死锁,必须拆卡): " + ",".join(sorted(cyc)))  # 原 v2.5 人话行保留（测试 grep「环」）
        alerts.record("环(死锁,必须拆卡): " + ",".join(sorted(cyc)))
    print("cycle=%s ready=%d 关键路径=%d卡: %s" % (
        "无" if not cyc else cyc, len(snap.get("ready", [])),
        snap.get("key_len", 0), " -> ".join(snap.get("critical_path", []))))
    if changed:
        print("(账有变化，已重刷物化图 gen=%s)" % snap.get("generation"))
    else:
        print("(物化图新鲜 gen=%s updated=%s)" % (snap.get("generation"), snap.get("generated")))
    print("状态分布: " + " ".join("%s=%d" % kv for kv in sorted(snap.get("counts", {}).items())))
    for cid in snap.get("canceled_blocked", []):
        print("ALARM: 被 CANCELED 依赖卡死(永不就绪，需改挂 deps 或一并作废): " + cid)
        alerts.record("ALARM: 被 CANCELED 依赖卡死(永不就绪，需改挂 deps 或一并作废): " + cid)
    for cid in snap.get("pm_wait_alarm", []):
        print("ALARM: 就绪但无泳道映射(PM直做卡? 用 start 开工) : " + cid)
        alerts.record("ALARM: 就绪但无泳道映射(PM直做卡? 用 start 开工) : " + cid)
    # MECH-STATE-STD-1 缺字段闸：每态必填字段的判定读 statemachine.REQUIRED_FIELDS 同一张表
    # （DONE 无 done_at=异常）。档位 CARD_FIELD_GATE=warn(缺省)/enforce/off——存量 16 张 done_at
    # 欠账由 MECH-DONE-SWEEP-1 补账，补齐后 PM 切 enforce 才进告警账（防上线即满盘红误报）。
    from . import statemachine as _sm
    _mode = _sm.gate_mode()
    if _mode != "off":
        for cid, st, miss in _sm.field_gaps(cards_now):
            _msg = "缺必填字段 %s(%s): %s" % (cid, _sm.TERMS.get(st, st), ",".join(miss))
            if _mode == "enforce":
                print("ALARM: " + _msg)
                alerts.record("ALARM: " + _msg)
            else:
                print("WARN: " + _msg + "（warn 档不进告警账；存量补账后 PM 切 CARD_FIELD_GATE=enforce）")
