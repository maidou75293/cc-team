#!/usr/bin/env bash
# reject-stoploss-test.py — BUG-REJECT-STOPLOSS-1「同卡连拒STOPLOSS有牙齿接线」红绿锁。
# 规格（业主 seq=30「下一次REJECT后不再重派（STOPLOSS标记上卡面）」+ seq=35「STOPLOSS=第2拒即停」；
# 事故在册=BUG-VECMEM-STALE-1 九连拒 6 槽翻台：REJECT-THRESHOLD 只挂报警零处置，报警键 ~20min
# 被机器销账早于 fuse S6 唤 PM 阈(30min)，升级环从未闭环）：
#   ①同卡累计拒绝达 STOPLOSS 阈（默认2，env REJECT_STOPLOSS_MAX 可配）时，reject 写路径内
#     原子止血：release（拒通道含·留 HANDOVER 不清工作区）+ set HOLD hold=STOPLOSS:gate+归因；
#   ②止血不依赖报警认领链——第2拒时报警键根本未触发（>2 才落键）卡已停；报警键 ack/销账
#     与否不影响止血已在账；
#   ③撤闸必红：NOACT 开关摘止血、gut 源码摘调用行，两形差分对下「第2拒必 HOLD」主断言
#     必不成立（守卫要验过它红得起来；红=干净红非崩溃：计数照落、命令 rc=0、只是不停血）；
#   ④不动已定版语义：报警上报阈(>REJECT_CARD_MAX=2)、键主体文案（只追加止血说明）、
#     conflict_count(REQ-SOP-ATTRIB-1/mergetrain)、fuse S2 即死停毒（STOPLOSS:fuse 触发面）均保持；
#   ⑤止血痕 actor=机制方(gate)+self=gate 尾标——不得伪装成 PM 真人痕拨静默钟
#     （fuse last_pm_action_ts 现算：只有止血痕的账=PM 从未动作；真人 PM 痕照常计数）。
# 判据：python -I tests/reject-stoploss-test.py 退出码0 + 尾行 REJECT_STOPLOSS_PASS
# 纪律：全沙箱（独立 CC_TEAM_HOME 临时账家 + temp git 仓），零碰生产台账/:8790；
#   撤闸样全在拷贝件上注入（cp 包+cut 补丁源），产码文件只读。双语头同 claim-counter-test。
''''true
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY=""
_p312="${CC_PY312_EXE:-}"
[ -z "$_p312" ] && for u in /mnt/[A-Za-z]*/Users/*; do
  [ -x "$u/AppData/Local/Programs/Python/Python312/python.exe" ] && _p312="$u/AppData/Local/Programs/Python/Python312/python.exe" && break
done
for c in python3 python py "${_p312}"; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "FAIL: reject-stoploss-test 找不到 python 解释器"; exit 1; fi
exec "$PY" -I -X utf8 "$0"
'''
# -*- coding: utf-8 -*-
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

try:                                    # BUG-CCARDS-UTF8-1 同纪律：出侧 utf-8+replace
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CT not in sys.path:
    sys.path.insert(0, CT)
os.environ.pop("CC_AUTO_VERIFY", None)          # done 首验通道关闭——本锁验止血不验自动验收
os.environ["CARD_3FORM_EPOCH"] = "off"          # 三段式闸关档——沙箱立卡不带 src/story/plan

OK_, FAIL = [], []


def ck(name, cond, detail=""):
    (OK_ if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + ("  [%s]" % detail if detail else ""))


def gitc(d, *a, chk=True):
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", d] + list(a),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    if chk and r.returncode != 0:
        raise RuntimeError("git %s rc=%s %s" % (" ".join(a), r.returncode, out[-200:]))
    return r.returncode, out


def ev_lines(team):
    try:
        return open(os.path.join(team, "state", "events.log"), encoding="utf-8").read().splitlines()
    except OSError:
        return []


def card(home, cid):
    g = json.load(open(os.path.join(home, "state", "graph.json"), encoding="utf-8"))
    return (g.get("cards") or g.get("nodes") or {}).get(cid) or {}


def alerts_of(home):
    try:
        return json.load(open(os.path.join(home, "state", "alerts.json"),
                              encoding="utf-8")).get("alerts", {})
    except (OSError, ValueError):
        return {}


def alerts_keys(home):
    return list(alerts_of(home))


def q_lines(home, owner):
    try:
        return [l.strip() for l in open(os.path.join(home, "state", "lane-%s.queue" % owner),
                                        encoding="utf-8") if l.strip()]
    except OSError:
        return []


def fresh(home, **rs_env):
    """场景隔离：独立 CC_TEAM_HOME + 清 ccards 模块缓存重 import；REJECT_STOPLOSS_* 两旋钮
    每场景先清再设（os.environ 进程内共享，防上场景旋钮漏进本场景假绿/假红）。"""
    for m in list(sys.modules):
        if m == "ccards" or m.startswith("ccards."):
            del sys.modules[m]
    os.environ.pop("REJECT_STOPLOSS_MAX", None)
    os.environ.pop("REJECT_STOPLOSS_NOACT", None)
    for k, v in rs_env.items():
        os.environ[k] = str(v)
    os.environ["CC_TEAM_HOME"] = home
    return importlib.import_module("ccards.cli")


def cut(src, marker, repl=None, want=1):
    """撤闸差分注入（claim-counter T4 同式）：marker 先验在场（缺席=锁腐硬败），
    命中行整行换成替换行——摘闸=等价删调用。want=期望命中数。"""
    lines = src.splitlines(keepends=True)
    idxs = [i for i, l in enumerate(lines) if marker in l]
    if len(idxs) != want:
        raise AssertionError("撤点 marker 命中 %d 处≠%d: %r" % (len(idxs), want, marker))
    repl = repl or "pass  # 撤闸（差分注入）\n"
    for i in idxs:
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        lines[i] = indent + repl
    return "".join(lines)


def claim_denied(cli, cid, owner="w1"):
    """claim 应被闸拒（活体测试）：True=拒（SystemExit），False=竟然领上了。"""
    try:
        cli.run(["claim", cid, "--owner", owner])
        return False
    except SystemExit:
        return True


SB = tempfile.mkdtemp(prefix="cc-reject-stoploss-lock-")
# 锁的宿主自己先钉对（MECH-TRAIN-ADR-1 同式，逐字取 claim-counter 先例）
_exp = os.environ.get("CARD_VERIFY_BASH")
_w = _exp or shutil.which("bash") or "bash"
if _exp is None and os.name == "nt" and _w.lower().startswith(
        os.environ.get("SystemRoot", "C:\\Windows").lower()):
    pass
else:
    os.environ["CARD_VERIFY_BASH"] = _w

# 撤闸差分注入靶（产码=只读；此串一改，本锁与注入同处告红——防「注入脱靶假绿」）
GUT_MARKER = "sl_hold = _reject_stoploss(c, rn, sl_note)"


def make_repo(tag):
    """fixture 仓：main 种子 + lane 分支（done 交卷用 lane commit，形同 claim-counter）。"""
    d = os.path.join(SB, tag)
    os.makedirs(d, exist_ok=True)
    gitc(d, "init", "-q", "-b", "main")
    gitc(d, "config", "user.email", "stoploss-test@local")
    gitc(d, "config", "user.name", "stoploss-test")
    gitc(d, "config", "core.autocrlf", "false")
    open(os.path.join(d, "README.md"), "w", encoding="utf-8", newline="\n").write("seed\n")
    gitc(d, "add", "-A")
    gitc(d, "commit", "-qm", "seed main")
    gitc(d, "checkout", "-qb", "lane")
    return d


def lane_commit(d, files, msg="lane work"):
    for name, body in files.items():
        p = os.path.join(d, *name.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8", newline="\n").write(body)
    gitc(d, "add", "-A")
    gitc(d, "commit", "-qm", msg)
    return gitc(d, "rev-parse", "HEAD")[1]


def mk_open(cli, cid, repo, lanes="ANY"):
    cli.run(["create", cid, "--title", "止血沙箱卡 %s" % cid, "--lanes", lanes,
             "--verify", "bash -c true", "--repo", repo])


def cycle_reject(cli, cid, repo, sha, reason):
    """一轮完整事故复现：OPEN→claim→done→REJECT（事故面=verifier 连拒，非手工改账）。"""
    cli.run(["claim", cid, "--owner", "w1"])
    cli.run(["done", cid, "--owner", "w1", "--commit", sha, "--repo", repo])
    cli.run(["verdict", cid, "REJECT", "--reason", reason])


def sl_events(home, cid):
    """OPEN->HOLD 止血痕行（actor/尾标双验的原料）。"""
    out = []
    for ln in ev_lines(home):
        f = ln.split("|")
        if len(f) >= 5 and f[1] == cid and f[2] == "OPEN->HOLD":
            out.append(f)
    return out


# ============ T1 主链（绿）：第1拒照常回队重派，第2拒即停=同事务 HOLD+STOPLOSS:gate ============
t = os.path.join(SB, "t1")
repo = make_repo("t1-repo")
sha = lane_commit(repo, {"marker.txt": "t1\n"})
open(os.path.join(repo, "ho-stamp.txt"), "w", encoding="utf-8", newline="\n").write("前任脏件\n")  # 交接素材
cli = fresh(t)
mk_open(cli, "RS-T1", repo, lanes="w1")
cycle_reject(cli, "RS-T1", repo, sha, "第1拒样本")
_c = card(t, "RS-T1")
ck("T1a 第1拒不止血（reject_count=1<阈2）：OPEN+无STOPLOSS+回队头（旧语义原样）",
   _c.get("status") == "OPEN" and "STOPLOSS" not in str(_c.get("hold") or "")
   and _c.get("reject_count") == 1 and "RS-T1" in q_lines(t, "w1"),
   "卡面=%s queue=%s" % (_c.get("status"), q_lines(t, "w1")))
cycle_reject(cli, "RS-T1", repo, sha, "第2拒·判据病")
_c = card(t, "RS-T1")
ck("T1b 第2拒即停：status=HOLD 且 hold=STOPLOSS:gate 前缀+归因一句话在册",
   _c.get("status") == "HOLD" and str(_c.get("hold") or "").startswith("STOPLOSS:gate")
   and "第2拒·判据病" in str(_c.get("hold") or ""),
   "status=%s hold=%s" % (_c.get("status"), _c.get("hold")))
ck("T1c 拒落账与止血同事务：reject_count=2，release 语义已含（owner/claimed_at/commit 全清）",
   _c.get("reject_count") == 2 and _c.get("owner") is None
   and _c.get("claimed_at") is None and _c.get("commit") is None)
_evs = sl_events(t, "RS-T1")
ck("T1d events 双痕成对：DONE->OPEN(release 面)+OPEN->HOLD(止血面)，痕 actor=gate 尾标 self=gate",
   any(l.split("|")[2] == "DONE->OPEN" and l.split("|")[1] == "RS-T1" for l in ev_lines(t))
   and len(_evs) == 1 and _evs[0][3] == "gate" and _evs[0][4].rstrip().endswith("self=gate"),
   str(_evs))
_ho = (_c.get("workarea") or {}).get("handover") or {}
ck("T1e 留 HANDOVER 不清工作区：卡面 handover.files 在场且 events 有 HANDOVER-OUT",
   bool(_ho.get("files")) and any("HANDOVER-OUT" in l for l in ev_lines(t)))
ck("T1f 停复派活体闸（HOLD 态）：挂失卡再 claim 必拒且卡面不被抢回",
   claim_denied(cli, "RS-T1") and card(t, "RS-T1").get("status") == "HOLD")
cli.run(["thaw"])
ck("T1g thaw 不自解挂：hold 文案在场=人工归因专属通道，机件解冻环放行不得（防自动复位空转复发）",
   card(t, "RS-T1").get("status") == "HOLD")
ck("T1h 止血不依赖报警链（②）：第2拒已停血而 REJECT-THRESHOLD 键从未落（键仅 >2 才上报）",
   not any("REJECT-THRESHOLD" in k for k in alerts_keys(t)), str(alerts_keys(t)))
ck("T1i 队列面不回填：止血后 w1.queue 无本卡（挂失不 requeue；HOLD 不入 ready 双保险）",
   "RS-T1" not in q_lines(t, "w1"), str(q_lines(t, "w1")))

# ============ T2 撤闸重拒再停+报警兼容：第3拒 re-arm + >2 键照落（文案只追加）+ ack 独立 ============
t = os.path.join(SB, "t2")
repo = make_repo("t2-repo")
sha = lane_commit(repo, {"marker.txt": "t2\n"})
cli = fresh(t)
mk_open(cli, "RS-T2", repo)                       # lanes=ANY（弹性工位池形）
cycle_reject(cli, "RS-T2", repo, sha, "拒一")
cycle_reject(cli, "RS-T2", repo, sha, "拒二")
ck("T2a 前段复用到第2拒：HOLD+STOPLOSS:gate（止血主形与 T1 同源，不另造态）",
   card(t, "RS-T2").get("status") == "HOLD")
cli.run(["set", "RS-T2", "--status", "OPEN"])     # PM 只改态不清归因文案——挂失闸应仍然拦路
ck("T2b 挂失闸独立于状态（③业主语义「STOPLOSS标记上卡面」）：OPEN+hold含STOPLOSS 仍拒领",
   claim_denied(cli, "RS-T2") and card(t, "RS-T2").get("status") == "OPEN")
cli.run(["set", "RS-T2", "--hold", ""])           # 合规撤闸=归因解除（cmd_claim 拒语指定通道）
cycle_reject(cli, "RS-T2", repo, sha, "拒三")
_c = card(t, "RS-T2")
ck("T2c 撤闸后再拒重新止血：第3拒即停（reject_count 累计不清=既成语义不动）",
   _c.get("status") == "HOLD" and str(_c.get("hold") or "").startswith("STOPLOSS:gate")
   and _c.get("reject_count") == 3, "卡面=%s" % _c.get("status"))
_keys = alerts_keys(t)
ck("T2d >2 报警键照落且主体文案不动（④）：startswith REJECT-THRESHOLD card=RS-T2 + reject_count=3>2 在键",
   any(k.startswith("REJECT-THRESHOLD card=RS-T2") and "reject_count=3>2" in k for k in _keys),
   str(_keys))
ck("T2e 键只追加「止血已落」说明、族内仅此一键（拒1/2 不响=上报阈语义原样）",
   sum(1 for k in _keys if "REJECT-THRESHOLD" in k) == 1
   and any("止血已落HOLD" in k for k in _keys if "REJECT-THRESHOLD" in k), str(_keys))
ck("T2f 止血痕两笔成对在账（第2拒+第3拒各一，actor=gate）",
   len(sl_events(t, "RS-T2")) == 2)
from ccards import alerts as _AL
_ack_hit = _AL.ack("REJECT-THRESHOLD card=RS-T2", "pm归因样本：止血已在账此键可销")
ck("T2g ack 销报警键≠撤止血（②独立性反向钉）：键闭合而卡仍 HOLD+STOPLOSS 在册",
   bool(_ack_hit) and "STOPLOSS" in str(card(t, "RS-T2").get("hold") or "")
   and card(t, "RS-T2").get("status") == "HOLD"
   and not _AL.still_open(_AL.entries().get([k for k in _AL.entries()
                       if k.startswith("REJECT-THRESHOLD card=RS-T2")][0], {})),
   str({k: v.get("ack") for k, v in _AL.entries().items()}))

# ============ T3 阈值可配（env REJECT_STOPLOSS_MAX）+ 坏值保守 + 0 下限钳 1 ============
t = os.path.join(SB, "t3a")
repo = make_repo("t3a-repo")
sha = lane_commit(repo, {"marker.txt": "t3a\n"})
cli = fresh(t, REJECT_STOPLOSS_MAX=3)
mk_open(cli, "RS-T3", repo)
cycle_reject(cli, "RS-T3", repo, sha, "阈3·拒一")
cycle_reject(cli, "RS-T3", repo, sha, "阈3·拒二")
_c = card(t, "RS-T3")
ck("T3a① MAX=3：第2拒不止血（OPEN），默认2不被硬编码吃掉",
   _c.get("status") == "OPEN" and "STOPLOSS" not in str(_c.get("hold") or "")
   and _c.get("reject_count") == 2, "卡面=%s" % _c.get("status"))
cycle_reject(cli, "RS-T3", repo, sha, "阈3·拒三")
ck("T3a② MAX=3：第3拒即停（阈从 env 现读，非 import 期冻值）",
   card(t, "RS-T3").get("status") == "HOLD" and "STOPLOSS:gate" in str(card(t, "RS-T3").get("hold") or ""))
t = os.path.join(SB, "t3b")
repo = make_repo("t3b-repo")
sha = lane_commit(repo, {"marker.txt": "t3b\n"})
cli = fresh(t, REJECT_STOPLOSS_MAX="abc")
mk_open(cli, "RS-T3B", repo)
cycle_reject(cli, "RS-T3B", repo, sha, "坏值·拒一")
ck("T3b① 坏值回退默认2：第1拒不止血", card(t, "RS-T3B").get("status") == "OPEN")
cycle_reject(cli, "RS-T3B", repo, sha, "坏值·拒二")
ck("T3b② 坏值回退默认2：第2拒即停（写口不炸·宁保守不猜大）",
   card(t, "RS-T3B").get("status") == "HOLD")
t = os.path.join(SB, "t3c")
repo = make_repo("t3c-repo")
sha = lane_commit(repo, {"marker.txt": "t3c\n"})
cli = fresh(t, REJECT_STOPLOSS_MAX="0")
mk_open(cli, "RS-T3C", repo)
cycle_reject(cli, "RS-T3C", repo, sha, "零值·拒一")
ck("T3c MAX=0 钳下限1：第1拒即停——0 不当「停用」误读（停用档=NOACT 明示，防手滑0全员误停）",
   card(t, "RS-T3C").get("status") == "HOLD" and "STOPLOSS:gate" in str(card(t, "RS-T3C").get("hold") or ""))

# ============ T4 撤闸差分对（③必红两形）：NOACT 开关 + gut 摘调用行 ============
t = os.path.join(SB, "t4a")
repo = make_repo("t4a-repo")
sha = lane_commit(repo, {"marker.txt": "t4a\n"})
cli = fresh(t, REJECT_STOPLOSS_NOACT=1)
mk_open(cli, "RS-T4A", repo, lanes="w1")
cycle_reject(cli, "RS-T4A", repo, sha, "撤闸·拒一")
cycle_reject(cli, "RS-T4A", repo, sha, "撤闸·拒二")
_c = card(t, "RS-T4A")
ck("T4a① NOACT 摘止血=接线前形态复现：第2拒仍 OPEN 可重派（T1b 主断言在此形必红=撤闸必红）",
   _c.get("status") == "OPEN" and "STOPLOSS" not in str(_c.get("hold") or "")
   and not sl_events(t, "RS-T4A"), "卡面=%s" % _c.get("status"))
ck("T4a② 撤闸=干净红非崩溃：reject_count=2 照落账、回队照常（计数与调度语义不因开关连坐）",
   _c.get("reject_count") == 2 and "RS-T4A" in q_lines(t, "w1"))
ck("T4a③ 差分对成立：同流程同轮次，T1 无开关=HOLD / 本形开关=OPEN（止血整段经此开关，非旁路假绿）",
   card(os.path.join(SB, "t1"), "RS-T1").get("status") == "HOLD"
   and _c.get("status") == "OPEN")

# —— gut 源码注入形：摘调用行=止血整段不存在，差分对照 T1 全绿 ——
gut = os.path.join(SB, "gut")
shutil.copytree(os.path.join(CT, "ccards"), os.path.join(gut, "ccards"),
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
shutil.copy2(os.path.join(CT, "cards_dag.py"), os.path.join(gut, "cards_dag.py"))
_p = os.path.join(gut, "ccards", "commands.py")
_prod_src = open(os.path.join(CT, "ccards", "commands.py"), encoding="utf-8").read()
ck("T4b0 注入靶在产码恰一命中（脱靶=撤闸样假绿防线；与上方 GUT_MARKER 注释同源维护）",
   sum(1 for l in _prod_src.splitlines() if GUT_MARKER in l) == 1)
open(_p, "w", encoding="utf-8").write(cut(_prod_src, GUT_MARKER,
                                          "sl_hold = None  # 撤闸（差分注入）\n"))


def run_gut(args, home):
    env = dict(os.environ)
    env["CC_TEAM_HOME"] = home
    env.pop("REJECT_STOPLOSS_MAX", None)
    env.pop("REJECT_STOPLOSS_NOACT", None)   # 摘的是代码不是开关——两撤闸形各证各的
    env["PYTHONIOENCODING"] = "utf-8:replace"
    r = subprocess.run([sys.executable, "-I", os.path.join(gut, "cards_dag.py")] + args,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, env=env)
    return r.returncode, (r.stdout or b"").decode("utf-8", "replace")


t = os.path.join(SB, "t4b")
repo = make_repo("t4b-repo")
sha = lane_commit(repo, {"marker.txt": "t4b\n"})
_rcs = []
_rcs.append(run_gut(["create", "RS-T4B", "--title", "gut撤闸样", "--lanes", "w1",
                     "--verify", "bash -c true", "--repo", repo], t)[0])
for _i in (1, 2):
    _rcs.append(run_gut(["claim", "RS-T4B", "--owner", "w1"], t)[0])
    _rcs.append(run_gut(["done", "RS-T4B", "--owner", "w1", "--commit", sha, "--repo", repo], t)[0])
    _rcs.append(run_gut(["verdict", "RS-T4B", "REJECT", "--reason", "gut拒%d" % _i], t)[0])
ck("T4b① gut 全链 rc=0（摘调用不炸账——干净红定义）", all(rc == 0 for rc in _rcs), str(_rcs))
_c = card(t, "RS-T4B")
ck("T4b② 摘 _reject_stoploss 调用=第2拒 STOPLOSS 消失（T1b 之绿全凭那一行；守卫红得起来）",
   _c.get("status") == "OPEN" and "STOPLOSS" not in str(_c.get("hold") or "")
   and _c.get("reject_count") == 2 and not sl_events(t, "RS-T4B"))

# ============ T5 静默钟不拨（⑤）：fuse last_pm_action_ts 现算——止血痕不算 PM 动作 ============
fuse_src = open(os.path.join(CT, "ops", "fuse.py"), encoding="utf-8").read()
_fmod = types.ModuleType("fuse_under_test")
_fmod.__file__ = os.path.join(CT, "ops", "fuse.py")
_ops = os.path.join(CT, "ops")
if _ops not in sys.path:
    sys.path.insert(0, _ops)
exec(compile(fuse_src, "fuse.py#sbx", "exec"), _fmod.__dict__)
_lines_t1 = ev_lines(os.path.join(SB, "t1"))
_gate1 = [l for l in _lines_t1 if "|RS-T1|OPEN->HOLD|gate|" in l]
ck("T5a 止血痕单拎过 fuse 静默钟口径=不计（⑤actor=gate 不伪装 PM；痕须真实在场防空检假绿）",
   len(_gate1) == 1 and _fmod.last_pm_action_ts(_gate1) is None, str(_gate1))
# 机制面全账对照（剔掉发卡 create 的 PM 硬编痕——那是发卡通道既成语义，非本卡产物）：
_nonpm = [l for l in _lines_t1 if (l.split("|")[3].strip() if len(l.split("|")) > 3 else "") != "PM"]
ck("T5b 机制面全账（claim/done/拒/止血/交接痕）：静默钟读数=从未 PM 动作",
   _fmod.last_pm_action_ts(_nonpm) is None)
_mix = _nonpm + ["2026-11-04T23:59:59+0800|RS-T1|OPEN->HOLD|PM|归因真人痕"]
ck("T5c 对照面成立：混入一条真人 PM 痕立刻计数（过滤不是瞎剔=PM 静默钟仍能被真人拨）",
   _fmod.last_pm_action_ts(_mix) is not None)

# ============ T6 已定版语义不动（④）静态钉：报警阈/停派闸/fuse S2/conflict 面/矩阵边 ============
eng_src = open(os.path.join(CT, "ccards", "engine.py"), encoding="utf-8").read()
ck("T6a 上报阈不动：REJECT_CARD_MAX=2 且比较符 > 原样（第3拒才落键）",
   "REJECT_CARD_MAX = 2" in _prod_src and "if rn > REJECT_CARD_MAX:" in _prod_src)
ck("T6b 停派闸同源不动：engine._stoploss 仍按 hold 含 STOPLOSS 判（gate 写的前缀复用同一闸）",
   'def _stoploss(c):' in eng_src and 'return "STOPLOSS" in str(c.get("hold") or "")' in eng_src)
ck("T6c fuse S2 即死停毒面不动（不同触发面各管各的）：STOPLOSS:fuse 文案与 self_trigger=fuse 在场",
   "STOPLOSS:fuse 连续%d次即死" in fuse_src and 'self_trigger="fuse"' in fuse_src)
_mt_src = open(os.path.join(CT, "ops", "mergetrain.py"), encoding="utf-8").read()
ck("T6d conflict_count 面不动（REQ-SOP-ATTRIB-1 域外零触碰）：CONFLICT-THRESHOLD 上报行在场",
   '_AL.record("CONFLICT-THRESHOLD card=' in _mt_src)
from ccards.statemachine import legal_edge
ck("T6e 迁移矩阵零加边：OPEN->HOLD 仍只经 set 合法边；DONE->HOLD via verdict REJECT 非法（不造新通道）",
   legal_edge("OPEN", "HOLD", "set") and not legal_edge("DONE", "HOLD", "verdict REJECT")
   and not legal_edge("VERIFIED", "HOLD", "verdict REJECT"))
ck("T6f 报警键头纪律不破：止血说明走后缀不走前缀（键名不得以 业主/FUSE-S6 头=静默丢报病根）",
   'alerts.record("REJECT-THRESHOLD card=%s reject_count=%d>%d%s"' in _prod_src)

# ============ 收账 ============
if FAIL:
    print("REJECT_STOPLOSS_FAIL fails=%d %s" % (len(FAIL), ";".join(FAIL)))
    sys.exit(1)
shutil.rmtree(SB, ignore_errors=True)
print("REJECT_STOPLOSS_PASS cases=%d" % len(OK_))
sys.exit(0)
