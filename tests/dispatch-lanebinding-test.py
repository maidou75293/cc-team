#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dispatch-lanebinding-test — BUG-LANE-ZOMBIECLAIM-1 红绿锁（业主 2026-11-04 04:37
点名看板成片「未绑定会话」黄字：「未绑定会话什么情况，这个不是严重bug么」——定性：严重）。

病根实锤（ctrld.log 2026-11-04 04:40~04:48）：`GATE-拒 claim REQ-UX-DEFAULTS-1@m9：
状态 CLAIMED 不可领` 连刷；REQ-UX-TERMS-1@m6 等三张 CLAIMED 卡的道锁 meta pid
(35720/17272/25344/37332) 实测全死、session='-'、lane beat 停 11-03（10~22h）：
  ① dispatch 认领无泳道活性闸——死道（无活 pid/活 beat/活绑定）照样发牌起班认领，
     重试方每圈写活锁面 ts/epoch（lane-m9.epoch 刷到 142）=死主赖道；
  ② 卡 CLAIMED 后仍重试 claim（同圈双道拿陈旧快照；圈间隔=claim→暴死→release→重claim
     循环），recycle zombie=0 判不走（锁面永远「新鲜」）；
  ③ 僵尸 CLAIMED 占 DISPATCH_CAP 在岗位 → 真工人（pool slot）领不到并发=队列饿死
     （REQ-CCTEAM-OSS-1 被 m6,m7 钉死 3 小时无人认领）。

修复三规（本锁逐条双向钉死——「守卫要验过它红得起来」，空检查比没检查更糟）：
  ①ops/dispatch.lane_dead_gate/mark_lane_dead：道被确死通道 release 后冷却窗内且无新
     名牌续写 ⇒ 不发牌不起班不 claim（改投活道或留队）；复活工人一笔 progress 早退冷却。
     → D1（死道在场⇒不认领）+ D1红向（冷却账在而心跳新⇒照样领走=不是永拦假闸）。
  ②ops/dispatch.unbind_watch + 同圈 BUSY-CARD 卡级去重：CLAIMED 超时未绑定→release 回
     OPEN+记痕 who=unbound-timeout（禁无脑重试刷锁面）；一卡一圈一道。
     → D2（同圈双道只领一次）、D3（僵尸 CLAIMED⇒回收）、D4（绑了/在写/手工卡不误杀）。
  ③ccards/locks.lane_owner_dead + lane_lock_acquire：锁面 ts 只能由活主续——活 pid/活 beat/
     活绑定三信号全灭的死主重写 ⇒ 直接 ZOMBIE-TAKEOVER 接管，不等 5min 自愈窗；
     任一信号活 ⇒ 维持「一道一活」新鲜保护。
     → G1（真写闸：死主锁被接管+旧卡回收）、G2（活主心跳在场⇒夺不动，撤新鲜感后又能接管
       ——差分对，证明拦你的是活性闸本身）、G3（空道正常认领/释放回归面）。

跑法：python -I cc-team/tests/dispatch-lanebinding-test.py
全绿汇总行 `DISPATCH-LANEBINDING-GREEN n=<笔数>`；任一红 `DISPATCH-LANEBINDING-RED`，rc=1。
不碰正账：D 组全程 tmp 沙箱（含 STREAK/REJECTS/POOL_HOME/REDAUTO 挂点钉死）；G 组子进程
CC_TEAM_HOME 指 tmp。不真起会话（spawn 全 stub）。
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
import types

for _s in (sys.stdout, sys.stderr):            # Windows 控制台默认 cp936 打碎 ⇒/① 等码点（同 alarm-deage-check 口径）
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cc-team 根
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "ops"))
os.environ["CC_TEAM_HOME"] = tempfile.mkdtemp(prefix="lanebind-importshield-")  # 垫掉 import 期默认账根
os.environ["POOL_HOME"] = os.path.join(tempfile.mkdtemp(prefix="lanebind-pool-"))
os.environ["MEMGATE_FORCE_MB"] = "32768"   # 高水位：资源闸永不拦（拦不拦由闸自己管）

from ccards import config as _cfg          # noqa: E402
from ccards import locks as _locks         # noqa: E402
import dispatch as D                       # noqa: E402  (ops/dispatch.py 本尊)

oks = []


def ck(name, cond, hint=""):
    oks.append((name, bool(cond), hint if not cond else ""))


# ---------- 活性锚：真实死 pid（起一个秒退子进程取号并 wait 收尸） ----------
def fresh_dead_pid():
    pr = subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.exit(0)"])
    pid = pr.pid
    pr.wait()
    deadline = time.time() + 5
    while _cfg.pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    return pid, not _cfg.pid_alive(pid)


# =====================================================================
# D 组：ops/dispatch.tick 真路径（engine/memgate 现件 + 闸门/会话 stub，tmp 沙箱）
# =====================================================================
TMPD = tempfile.mkdtemp(prefix="lanebind-d-")
os.makedirs(os.path.join(TMPD, "state"))
os.makedirs(os.path.join(TMPD, "proj"))
GRAPH_D = {"cards": {}}


def dcard(cid, lanes, status="OPEN"):
    GRAPH_D["cards"][cid] = {"id": cid, "title": cid, "description": "", "verify": "true",
                             "status": status, "lanes": lanes,
                             "created_at": "2026-11-04T04:00:00+0800"}


for _c, _l in (("NEXT", ["st1"]), ("C", ["m8", "m9"]), ("NEW", ["st2"]), ("S1", ["ANY"]),
               ("Z1", ["st1"]), ("Z2", ["st9"]), ("ZB", ["sta"]), ("ZS", ["stb"]),
               ("ZL", ["stc"])):
    dcard(_c, _l)
with open(os.path.join(TMPD, "state", "graph.json"), "w", encoding="utf-8") as f:
    json.dump(GRAPH_D, f)
for _lane in ("st1", "st2", "st9", "m8", "m9"):
    os.makedirs(os.path.join(TMPD, "wf-%s" % _lane), exist_ok=True)

# 沙箱钉全套账根（含 fleet/alarm 挂点——漏一个就是往正账写测试垃圾）
_cfg.TEAM, _cfg.STATE, _cfg.CLAIMS, _cfg.GRAPH = TMPD, os.path.join(TMPD, "state"), \
    os.path.join(TMPD, "claims"), os.path.join(TMPD, "state", "graph.json")
_cfg.REJECTS = os.path.join(_cfg.STATE, "rejects.log")
_cfg.EVENTS = os.path.join(_cfg.STATE, "events.log")
_cfg.PROGRESS = os.path.join(_cfg.STATE, "progress.log")
_locks.TEAM = _locks.STATE = _cfg.STATE           # locks 在 import 期抓的具名锚同钉
D.TEAM, D.CODE, D.PROJ = TMPD, TMPD, os.path.join(TMPD, "proj")
D.HB = os.path.join(TMPD, "hb.txt")
D.STREAK = os.path.join(_cfg.STATE, "idle-streak.json")   # fleet_watch 写点（模块常量=import 期值）
D.BIND_WAIT = 0.0
D.DEAD_GRACE_S = 0.0
D.UNBIND_TIMEOUT_S = 0.0                          # 超时未绑=即时（生产默认 900s）
D.LANE_COOL_S = 600.0
D.REDAUTO_EVERY = 10 ** 6                         # 看护环在本锁不点火（复跑走真判据会碰真账）

claims, spawns, logs, kills = [], [], [], []


class _FakeProc:
    pid = 424242

    def kill(self):
        kills.append("killed")


D.trace = lambda m: logs.append(m)
D.run_gate = lambda *a: (claims.append(a), (0, "EPOCH x 7"))[1]
D.compose_brief = lambda card, lane, win_wd, epoch=None: (os.path.join(TMPD, "b.txt"), "WINBRIEF")
D.spawn_runner = lambda lane, win_wd, win_brief: (spawns.append(lane), _FakeProc())[1]
D._ask_model = lambda p: ""
_census = {"v": set()}
D._census_lanes = lambda: _census["v"]

fake_eng = types.ModuleType("ccards.engine")
_eng_state = {"claimable": {}, "claimed": {}, "pool_ready": []}
fake_eng.tick = lambda: dict(_eng_state)
import ccards                                   # noqa: E402
ccards.engine = fake_eng
sys.modules["ccards.engine"] = fake_eng


def dtick():
    claims.clear(); spawns.clear(); logs.clear(); kills.clear()
    D.tick(once=True)


def write_claim(cid, owner, pid, kind="lane", session="-", age_s=0, epoch=5):
    os.makedirs(os.path.join(_cfg.CLAIMS, cid), exist_ok=True)
    ts = (datetime.datetime.now().astimezone() - datetime.timedelta(seconds=age_s)) \
        .strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(os.path.join(_cfg.CLAIMS, cid, "claim.json"), "w", encoding="utf-8") as f:
        json.dump({"owner": owner, "pid": pid, "ts": ts, "kind": kind,
                   "session_id": session, "epoch": epoch}, f)


def beat_path(lane):
    return os.path.join(_cfg.STATE, "lane-%s.beat" % lane)


def cool_path(lane):
    return os.path.join(_cfg.STATE, "lane-%s.cool" % lane)


def write_beat(lane, epoch, age_s, ring=9):
    t = time.time() - age_s
    with open(beat_path(lane), "w", encoding="utf-8") as f:
        json.dump({"lane": lane, "epoch": epoch, "ring": ring, "cid": "X",
                   "ts": datetime.datetime.fromtimestamp(t).astimezone()
                   .strftime("%Y-%m-%dT%H:%M:%S%z"), "t": t}, f)


# ---- D1 死道在场 ⇒ 不认领（规格①）：先走真 WORKER-DIED 通道产冷却账，再验闸 ----
write_claim("Z1", "st1", 424242, kind="lane", session="-", age_s=0)
_eng_state.update(claimable={}, claimed={"Z1": "st1"}, pool_ready=[])
_census["v"] = set()                              # 普查无人=确死
dtick()
ck("D1a WORKER-DIED 通道 release 落闸（前置通道在场）",
   ("release", "Z1", "--who", "spawn-death") in claims, "claims=%s" % claims[-4:])
ck("D1a 确死道冷却账已立（mark_lane_dead 由 release 成功侧自动产出）",
   os.path.isfile(cool_path("st1")), "缺 state/lane-st1.cool")

_eng_state.update(claimable={"st1": "NEXT"}, claimed={}, pool_ready=[])
dtick()
ck("D1b 死道在场⇒不认领：无 epoch 发牌/无起班/无 claim（重试方不再写活锁面）",
   not any(a[0] in ("epoch", "claim") for a in claims) and not spawns,
   "claims=%s spawns=%s" % (claims, spawns))
ck("D1b 点名留痕 DEAD-LANE", any("DEAD-LANE NEXT(st1)" in m for m in logs), "logs=%s" % logs)

# ---- D1红向 差分：冷却账在场，但道上有比确死更晚的活心跳 ⇒ 早退冷却照常领（非永拦假闸）----
write_beat("st1", epoch=6, age_s=0)               # 复活工人续写一笔（t>cool.at）
dtick()
ck("D1c 复活心跳早退冷却⇒照常领走（闸只拦无活性道，不拦修活了的道）",
   ("claim", "NEXT", "--owner", "st1", "--pid", "424242", "--pid-ns", "win",
    "--epoch", "7") in claims and spawns == ["st1"],
   "claims=%s spawns=%s" % (claims, spawns))
os.remove(beat_path("st1"))                       # 心跳撤掉（桩=工人又停了）→ 又拦回：闸随活性走
dtick()
ck("D1c红向差分 心跳没了(冷却账未过期)⇒复拦，不发牌不认领",
   not any(a[0] in ("epoch", "claim") for a in claims), "claims=%s" % claims)

# ---- D2 同圈双道⇒一圈一卡一道（规格②去重；实锤=04:4x GATE-拒 claim @m9 连刷）----
_eng_state.update(claimable={"m8": "C", "m9": "C"}, claimed={}, pool_ready=[])
dtick()
claim_c = [a for a in claims if a[0] == "claim" and a[1] == "C"]
epoch_c = [a for a in claims if a[0] == "epoch"]
ck("D2 同卡钉两道一圈只领一次（后道见 BUSY-CARD 不再重试 claim）",
   len(claim_c) == 1 and len(epoch_c) == 1,
   "claim_c=%s epoch=%s" % (claim_c, epoch_c))
ck("D2 后道留痕 BUSY-CARD（不烧 epoch、不起班、不 GATE-拒刷锁面）",
   any("BUSY-CARD C" in m for m in logs) and
   not any("GATE-拒" in m for m in logs), "logs=%s" % logs[-4:])
ck("D2 认领即时写在制名单（inflight 同圈生效=去重不是事后账）",
   ("claim", "C", "--owner", "m8", "--pid", "424242", "--pid-ns", "win", "--epoch", "7")
   in claims, "首道应=m8(排序在前) claims=%s" % claims)

# ---- D3 僵尸 CLAIMED ⇒ 回收重排（规格②：超时未绑定 release 回 OPEN+记痕）----
write_claim("Z2", "st9", 424242, kind="lane", session="-", age_s=7200, epoch=5)
write_beat("st9", epoch=5, age_s=7300)            # 心跳停=比认领还旧（本任期无续写）
_census["v"] = {"st9"}                            # 普查「在册」——尸检通道放它一马，专测 unbind 面
_eng_state.update(claimable={}, claimed={"Z2": "st9"}, pool_ready=[])
dtick()
ck("D3 CLAIMED 超时未绑定⇒release who=unbound-timeout 回收（记痕+回 OPEN 重排由写闸落）",
   ("release", "Z2", "--who", "unbound-timeout") in claims, "claims=%s" % claims)
ck("D3 回收道同圈进冷却（回收与①活性闸闭环：不再 claim→刷锁面→回收的循环）",
   os.path.isfile(cool_path("st9")), "缺 lane-st9.cool")
ck("D3 尸检通道未抢戏（普查在册=人还在，回收归 unbind 面单通道）",
   ("release", "Z2", "--who", "spawn-death") not in claims and
   not any("WORKER-DIED" in m for m in logs), "logs=%s" % logs[-6:])

# ---- D4 不误杀三面：已绑 / 手工 session / 本任期在写名牌 ⇒ unbind 不动 ----
write_claim("ZB", "sta", 424242, kind="lane", session="abc-session", age_s=7200)
write_claim("ZS", "stb", None, kind="session", session="-", age_s=7200)
write_claim("ZL", "stc", 424242, kind="lane", session="-", age_s=7200, epoch=5)
write_beat("stc", epoch=5, age_s=30)              # 慢绑定但本任期在续写=活工人
_census["v"] = {"sta", "stb", "stc"}              # 普查在册=人还在，尸检通道不抢戏（专测 unbind 三面）
_eng_state.update(claimable={}, claimed={"ZB": "sta", "ZS": "stb", "ZL": "stc"},
                  pool_ready=[])
dtick()
rel = [a for a in claims if a[0] == "release"]
ck("D4 不误杀三面：已绑/手工session/在写名牌 均不回收（在制卡不打扰）",
   not rel and not any("CLAIMED-UNBOUND" in m for m in logs), "rel=%s" % rel)

# ---- D5 活 slot ⇒ 领走（池侧 happy path，pool 桩不建真桌）----
fp = types.ModuleType("pool")
fp.SLOT_PREFIX = "slot-"
fp.cost_now = lambda: 100
fp.desks = lambda: {"seq": 9001, "desks": {}}
fp.next_slot = lambda st: "slot-9001"
fp.open_desk = lambda cand, code=None: (True, os.path.join(code or "", "wf-" + cand), "")
fp.save_desks = lambda st: None
fp.mark_desk_opened = lambda slot, cid, wd: None
fp.admission = lambda free, cost, infl, cap, pending: {
    "allow": len(pending), "blocked": [], "note": "fake-gate"}
fp.write_gate = lambda *a, **k: None
fp.learn = lambda *a: None
fp.desk_dir = lambda code, slot: os.path.join(code, "wf-" + slot)
fp.remove_desk = lambda d, code=None, force=False: ("ok", "")
D.pool = fp
_eng_state.update(claimable={}, claimed={}, pool_ready=["S1"])
dtick()
ck("D5 活 slot（空桌无死史）⇒ 领走 ANY 卡（闸不误拦池侧）",
   any(a[0] == "claim" and a[1] == "S1" and a[3] == "slot-9001" for a in claims),
   "claims=%s" % claims)

# ---- D6 空道（无冷却账）正常派发回归面 ----
_eng_state.update(claimable={"st2": "NEW"}, claimed={}, pool_ready=[])
dtick()
ck("D6 无死史的空道照常用同一把闸领走（活性闸零误伤）",
   any(a[0] == "claim" and a[1] == "NEW" and a[3] == "st2" for a in claims),
   "claims=%s" % claims)

# =====================================================================
# G 组：真写闸（cards_dag 子进程 + CC_TEAM_HOME tmp）——规格③ 死主重写=直接判 zombie 接管
# =====================================================================
TMPG = tempfile.mkdtemp(prefix="lanebind-g-")
os.makedirs(os.path.join(TMPG, "state"))
CARD_DAG = os.path.join(REPO, "cards_dag.py")


def g(*args):
    env = dict(os.environ, CC_TEAM_HOME=TMPG, CLAW_SESSION_ID="")
    p = subprocess.run([sys.executable, "-I", CARD_DAG] + list(args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env)
    return p.returncode, ((p.stdout or "") + (p.stderr or ""))


def gstatus(cid):
    rc, out = g("show", cid)
    import re as _re
    m = _re.search(r'"status"\s*:\s*"([A-Z]+)"', out)
    return m.group(1) if m else "?"


dpid_a, dead_a = fresh_dead_pid()
dpid_b, dead_b = fresh_dead_pid()
ck("G0 死 pid 锚可用（秒退进程 wait 收尸后判活=死）", dead_a and dead_b,
   "pid %s/%s alive?" % (dpid_a, dpid_b))

g("create", "LB-A", "--title", "死主占道卡", "--verify", "true", "--lanes", "ANY")
g("create", "LB-B", "--title", "接管者卡", "--verify", "true", "--lanes", "ANY")
g("create", "LB-C", "--title", "第三道卡", "--verify", "true", "--lanes", "ANY")
rc, out = g("claim", "LB-A", "--owner", "m8", "--pid", str(dpid_a))
ck("G1a 前置：LB-A claim 上死锚道 m8（kind=lane、session='-'、无心跳=实况死主形）", rc == 0, out[-160:])

rc, out = g("claim", "LB-B", "--owner", "m8", "--pid", str(dpid_b))
ck("G1b 死主重写锁面(新鲜<5min 但三信号全灭)⇒ 不等自愈窗直接 ZOMBIE-TAKEOVER 接管",
   rc == 0 and "ZOMBIE-TAKEOVER" in out, "rc=%s out=%s" % (rc, out[-200:]))
ck("G1c 旧僵尸卡 LB-A release 回 OPEN+记痕(lane-steal)，LB-B 占道",
   gstatus("LB-A") == "OPEN" and gstatus("LB-B") == "CLAIMED"
   and "lane-steal" in open(os.path.join(TMPG, "state", "events.log"),
                             encoding="utf-8").read(),
   "A=%s B=%s" % (gstatus("LB-A"), gstatus("LB-B")))

# ---- G2 活主夺不动（红向差分对：同一 fixture，心跳在场⇒拒夺；心跳停摆⇒能夺）----
import re as _re0
_m = _re0.search(r"epoch=(\d+)", str(out))       # 软解析：撤闸变异时 out=ERROR 无 epoch=，
ep_b = int(_m.group(1)) if _m else -1            # 必须落成 FAIL 断言行而非 traceback 崩suite
rc, _ = g("progress", "LB-B", "活着在写", "--lane", "m8", "--epoch", str(ep_b), "--ring", "1")
ck("G2a 前置：LB-B 属主 progress 续写名牌（真通道）", rc == 0, "rc=%s ep=%s" % (rc, ep_b))
rc, out2 = g("claim", "LB-C", "--owner", "m8", "--pid", str(dpid_a))
ck("G2b 活 beat 在场⇒「锁面 ts 只能由活主续」新鲜保护守住，绝不夺活会话的锁",
   rc != 0 and "锁新鲜" in out2 and gstatus("LB-C") == "OPEN" and gstatus("LB-B") == "CLAIMED",
   "rc=%s out=%s" % (rc, out2[-160:]))
# 心跳停摆（把 beat 的 t 拨回 1h 前=桩：工人死了不再续写；三信号 pid 死+绑定'-'全灭）
bp = os.path.join(TMPG, "state", "lane-m8.beat")
b = json.load(open(bp, encoding="utf-8")) if os.path.isfile(bp) \
    else {"lane": "m8", "epoch": max(ep_b, 0), "ring": 1}   # 撤闸变异时 G2a 未写成 beat——
b["t"] = time.time() - 3600                                  # 兜底直造旧心跳，让 G2 落成 FAIL 断言
with open(bp, "w", encoding="utf-8") as f:
    json.dump(b, f)
rc, out3 = g("claim", "LB-C", "--owner", "m8", "--pid", str(dpid_b))
ck("G2c 红向差分对：同一道心跳一停摆(三信号全灭)⇒立刻可接管（拦 G2b 的就是活性闸本身）",
   rc == 0 and "ZOMBIE-TAKEOVER" in out3 and gstatus("LB-B") == "OPEN",
   "rc=%s B=%s out=%s" % (rc, gstatus("LB-B"), out3[-160:]))

# ---- G3 happy path 回归：空道认领/释放/回队不受活性闸影响 ----
dpid_q, _ = fresh_dead_pid()
rc, _ = g("claim", "LB-D" if False else "LB-C", "--owner", "q9", "--pid", str(dpid_q)) \
    if gstatus("LB-C") == "OPEN" else (0, "")
g("create", "LB-D", "--title", "正常卡", "--verify", "true", "--lanes", "ANY")
rc, _ = g("claim", "LB-D", "--owner", "q9", "--pid", str(dpid_q))
ck("G3a 无死史空道 claim 正常（新鲜锁保护语义只防活主，不坑新活）", rc == 0 and
   os.path.isdir(os.path.join(TMPG, "state", "lane-q9.lock")), "rc=%s" % rc)
rc, _ = g("release", "LB-D", "--who", "test")
ck("G3b release 收口：道锁清+卡回 OPEN（回收通道不回归坏）",
   rc == 0 and not os.path.isdir(os.path.join(TMPG, "state", "lane-q9.lock"))
   and gstatus("LB-D") == "OPEN", "rc=%s" % rc)

# ---- G4 证据面：三规锚点在场（静态锁，防后续合并悄悄拆闸=空检查比没检查更糟的反面）----
dsrc = open(os.path.join(REPO, "ops", "dispatch.py"), encoding="utf-8").read()
lsrc = open(os.path.join(REPO, "ccards", "locks.py"), encoding="utf-8").read()
ck("G4 dispatch 三规挂点齐：lane_dead_gate/unbind_watch/BUSY-CARD/mark_lane_dead",
   all(k in dsrc for k in ("lane_dead_gate(", "unbind_watch(", "BUSY-CARD",
                           "mark_lane_dead(")), "")
ck("G4 locks 接管闸齐：lane_owner_dead + ZOMBIE-TAKEOVER + 活主 die 面",
   all(k in lsrc for k in ("def lane_owner_dead", "ZOMBIE-TAKEOVER", "锁新鲜")), "")

bad = [t for t, ok, hint in oks if not ok]
for t, ok, hint in oks:
    print("%s %s%s" % ("PASS" if ok else "FAIL", t, ("｜" + hint) if not ok else ""))
if bad:
    print("DISPATCH-LANEBINDING-RED fails=%d" % len(bad))
    sys.exit(1)
print("DISPATCH-LANEBINDING-GREEN n=%d" % len(oks))
