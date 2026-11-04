#!/usr/bin/env bash
# sop-halfbind-test.py — BUG-BIND-CONFIRMRETRY-1「bind 半成自愈」常驻红绿锁。
# 病灶（2026-11-04 夜 fuse S6 升级单实锤）：cmd_bind 写 claim.json 成迹后 graph save 崩
#   （WinError 5 rename 撞窗）=「签而不收」：锚在 claim、卡面无 confirm 痕；旧 backfill_binds
#   跳检只认 session_id 缺失即 continue=对半成 bind 永不重试签名，SOP-MISS 铃每拍续记
#   （TEST-E2E-PREPUSH-1 alerts count 37）直到 PM 人工补签。
# 修复形（ops/dispatch.py backfill_binds）：session_id 在场时加一道卡面 confirm 缺位核对
#   （整拍懒拿一次 cards 快照）：kind=lane 且卡面 CLAIMED 且无 confirm → 幂等重 bind_gate
#   （cmd_bind 既有幂等：同 (lane,epoch) 已签不重复落 CONFIRM 行、三件缺一只 WARN 不落痕），
#   trace 落 HALF-BIND-RETRY 痕；补成后 sweep_confirm_miss 见签收在场自然退账=铃停。
#   禁动 ccards/config.py atomic_write_json 核心（rename 撞窗根因留观测，本锁只钉重试面）。
# 案目：
#   T1 主链：半成卡（claim 已绑+卡面无 confirm）一拍 backfill → HALF-BIND-RETRY 痕+重 bind
#      成迹+confirm 三件齐+CONFIRM 事件行恰一行+锚不动；sweep 再跑=铃停退账；再拍=静默幂等；
#   T2 已签绿形零打扰：confirm 在场=不重 bind（零闸调用零痕，events 不刷流水）；
#   T3 边界三形不重签：kind=session 手工形 / 卡面 DONE / session_id 未绑（未绑走探测+连拍账，
#      不碰半成核对，防「没锚硬签」）；
#   T4 撤闸差分对：跳检恢复只认 session_id 旧形（confirm 核对行摘成 if False）=半成不自愈、
#      卡面永无 confirm、sop-miss 账不退（铃不停）；同卡用现件形对照=当场补成（撤闸必红）；
#   T5 源钉：HALF-BIND-RETRY 痕唯一出处、cards 快照整拍一份（懒加载单点）。
# 跑法: python -I tests/sop-halfbind-test.py   （exit 0 + 尾行 SOP_HALFBIND_PASS = 全绿）
# 纪律：全沙箱（import 前钉 CC_TEAM_HOME 临时账家，claims/state/events/alerts 全落沙箱；
#   DISPATCH_PROJ/DISPATCH_CODE_ROOT 钉空根=discover_session 零真转录），零碰正账、零真会话、
#   零 powershell；run_gate 桩=进程内真 CLI bind（_cli.run 走沙箱 cmd_bind 全链，比 stub 更
#   实：钉的是「重 bind 真能把 confirm 补落」这一行为面，不是调用形）。双语头同 sop-confirm-test
#   （''''true 藏 bash 引导段，段内禁再现字面 '''）。
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
if [ -z "$PY" ]; then echo "FAIL: sop-halfbind-test 找不到 python 解释器"; exit 1; fi
exec "$PY" -I -X utf8 "$0"
'''
# -*- coding: utf-8 -*-
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

CT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cc-team 根
SB = tempfile.mkdtemp(prefix="cc-sop-halfbind-")
HOME = os.path.join(SB, "home")                                     # 唯一沙箱账家（全案共用，卡号隔离）
PROJ = os.path.join(SB, "noproj")                                   # 空转录根：discover_session 永探不到
CODE = os.path.join(SB, "nocod")                                    # 空车队根（探测目录名随道编码，仍空）
os.makedirs(os.path.join(HOME, "state"), exist_ok=True)
os.makedirs(PROJ, exist_ok=True)
os.makedirs(CODE, exist_ok=True)
# import 前钉死账家与阈值（dispatch/cmd_bind 的 import 期常量全随此家）：
os.environ["CC_TEAM_HOME"] = HOME
os.environ["POOL_HOME"] = os.path.join(SB, "poolhome")
os.environ["CARD_3FORM_EPOCH"] = "off"          # 三段式闸关档（本锁考半成重试不考立卡文案）
os.environ["SOP_CONFIRM_TIMEOUT_MIN"] = "1"     # 主链案用 1min 阈，免等 10min
os.environ["DISPATCH_PROJ"] = PROJ
os.environ["DISPATCH_CODE_ROOT"] = CODE
os.environ.pop("CC_AUTO_VERIFY", None)          # done 首验通道关闭（本锁不碰 done）
os.environ.pop("CLAW_SESSION_ID", None)         # claim 会话锚=「-」形（半成态靠改 claim.json 现造）
# 锁的宿主自己先钉对 bash（claim-counter 同式：WSL 桥毒宿主=不钉交自解析）
_exp = os.environ.get("CARD_VERIFY_BASH")
_w = _exp or shutil.which("bash") or "bash"
if not (_exp is None and os.name == "nt" and _w.lower().startswith(
        os.environ.get("SystemRoot", "C:\\Windows").lower())):
    os.environ["CARD_VERIFY_BASH"] = _w
sys.path.insert(0, CT)
sys.path.insert(0, os.path.join(CT, "ops"))

from ccards import cli as _cli                   # noqa: E402
from ccards import config as _cfg                # noqa: E402

oks = []


def ck(name, cond, hint=""):
    oks.append((name, bool(cond), "" if cond else str(hint)))
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  [%s]" % str(hint)[:220]))


def nowz():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def gpath():
    return os.path.join(HOME, "state", "graph.json")


def graph():
    return json.load(open(gpath(), encoding="utf-8"))


def gcard(cid):
    g = graph()
    return (g.get("cards") or {}).get(cid) or {}


def patch_card(cid, drop=(), setkv=None):
    """直改沙箱 graph 卡面（回填龄/抠件用）；LF 钉死同 MECH-LEDGER-CRLF-1。"""
    g = graph()
    c = g["cards"][cid]
    for k in drop:
        c.pop(k, None)
    for k, v in (setkv or {}).items():
        c[k] = v
    with open(gpath(), "w", encoding="utf-8", newline="") as f:
        json.dump(g, f, ensure_ascii=False)


def claim_json(cid):
    return json.load(open(os.path.join(HOME, "claims", cid, "claim.json"), encoding="utf-8"))


def put_claim_json(cid, d):
    p = os.path.join(HOME, "claims", cid, "claim.json")
    with open(p, "w", encoding="utf-8", newline="") as f:
        json.dump(d, f)


def ev_lines():
    try:
        return open(os.path.join(HOME, "state", "events.log"), encoding="utf-8").read().splitlines()
    except OSError:
        return []


def n_ev(cid, needle):
    return len([l for l in ev_lines() if ("|%s|" % cid) in l and needle in l])


def alerts_of():
    try:
        return json.load(open(os.path.join(HOME, "state", "alerts.json"),
                              encoding="utf-8")).get("alerts", {})
    except (OSError, ValueError):
        return {}


def sop_acct(m):
    try:
        return json.load(open(m.SOP_MISS_STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mk_claimed(cid, lane, verify="bash -c true"):
    rc = _cli.run(["create", cid, "--title", "半成沙箱卡 %s" % cid, "--lanes", lane,
                   "--verify", verify])
    assert rc == 0, cid
    # --pid 活锚=dispatcher 真形（claim.json kind=lane，半成核对判定域内）
    rc = _cli.run(["claim", cid, "--owner", lane, "--pid", str(os.getpid())])
    assert rc == 0, cid


def mk_half(cid, lane, kind="lane", age_min=3, session="sess-half"):
    """构造半成 bind：真 claim 后**直改 claim.json** 写 session_id——精确等价
    「cmd_bind 写 claim.json 成迹、graph save 崩窗」：锚在 claim、卡面无 confirm、
    events 无 CONFIRM 行、无 bind session_id 行。超龄回填让 SOP-MISS 铃先响成前提。"""
    mk_claimed(cid, lane)
    if age_min:
        patch_card(cid, setkv={"claimed_at": (datetime.now(timezone.utc).astimezone()
                                              - timedelta(minutes=age_min)).isoformat(timespec="seconds")})
    d = claim_json(cid)
    d["session_id"] = session
    d["kind"] = kind
    put_claim_json(cid, d)


GATES = []


def _cli_gate(*a):
    """run_gate 桩=进程内真 CLI（沙箱 cmd_bind 全链真跑）；SystemExit=die 透传退出码。"""
    GATES.append(a)
    buf = io.StringIO()
    rc = 1
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = _cli.run(list(a))
    except SystemExit as e:
        rc = int(e.code) if isinstance(e.code, int) else 1
    out = [l for l in buf.getvalue().splitlines() if l.strip()]
    return rc, (out[-1] if out else "")


def load_dispatch(src_text=None, tag="dphalf"):
    """exec dispatch.py 源码进独立模块（trace 桩化收音；run_gate=真 CLI bind；
    ccards 全链=沙箱家现件）。src_text=补丁源（撤闸差分用）；常量随当前 env 重算。"""
    src = src_text if src_text is not None else DP_SRC
    mod = types.ModuleType(tag)
    mod.__file__ = os.path.join(CT, "ops", "dispatch.py")
    mod.__dict__["__name__"] = "dp_under_test"
    code = compile(src, mod.__file__ + "#sbx", "exec")
    d = mod.__dict__
    exec(code, d)
    mod.trace = lambda msg: _modlog.setdefault(id(mod), []).append(str(msg))
    mod.run_gate = _cli_gate
    return mod, _modlog.setdefault(id(mod), [])


_modlog = {}


def cut(src, marker, repl, want=1):
    lines = src.splitlines(keepends=True)
    idxs = [i for i, l in enumerate(lines) if marker in l]
    if len(idxs) != want:
        raise AssertionError("撤点 marker 命中 %d 处≠%d: %r" % (len(idxs), want, marker))
    for i in idxs:
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        lines[i] = indent + repl
    return "".join(lines)


DP_SRC = open(os.path.join(CT, "ops", "dispatch.py"), encoding="utf-8").read()
m, lg = load_dispatch()

# ============ T1 主链：半成卡一拍自愈（痕+三件+CONFIRM 行+铃停） ============
mk_half("HB-T1", "l1")
m.sweep_confirm_miss({"HB-T1": "l1"})
ck("T1a 前提自证：半成态先响铃（alerts 有键+sop-miss.json 入账）",
   "SOP-MISS card=HB-T1" in alerts_of() and "HB-T1" in sop_acct(m), str(sop_acct(m)))
n_g0 = len(GATES)
m.backfill_binds({"HB-T1": "l1"})
ck("T1b trace 落 HALF-BIND-RETRY 痕恰一行",
   len([l for l in lg if "HALF-BIND-RETRY HB-T1" in l]) == 1, str(lg)[-200:])
ck("T1c 幂等重 bind 真落闸一次（session=claim 现锚 sess-half）",
   len(GATES) == n_g0 + 1 and GATES[-1] == ("bind", "HB-T1", "--session", "sess-half"),
   str(GATES[-1:]))
_c1 = gcard("HB-T1")
_conf = _c1.get("confirm") or {}
ck("T1d 卡面 confirm 三件齐（lane/epoch/workarea 对账+at 落笔）",
   _conf.get("lane") == "l1" and str(_conf.get("epoch")) == str(_c1.get("claimed_epoch"))
   and _conf.get("workarea") == (_c1.get("workarea") or {}).get("at") and _conf.get("at"),
   json.dumps(_conf, ensure_ascii=False))
ck("T1e events 机读行 CONFIRM card=<cid> lane=<道> epoch=<N> workarea=<锚> 恰一行",
   n_ev("HB-T1", "CONFIRM card=HB-T1") == 1
   and re.search(r"\|CONFIRM card=HB-T1 lane=l1 epoch=\d+ workarea=\S+",
                 " ".join(ev_lines())), str(ev_lines())[-200:])
ck("T1f 主链不回退：claim 锚不动（覆写幂等，bind session_id 行=重试一笔恰 1）",
   claim_json("HB-T1").get("session_id") == "sess-half" and n_ev("HB-T1", "bind session_id") == 1)
m.sweep_confirm_miss({"HB-T1": "l1"})
ck("T1g 铃停=迟到签收自然退账（sop-miss.json 出账）", "HB-T1" not in sop_acct(m), str(sop_acct(m)))
n_g1 = len(GATES)
m.backfill_binds({"HB-T1": "l1"})
ck("T1h 补成后再拍=静默（不再落闸/不再出痕/CONFIRM 与 bind 行不涨）",
   len(GATES) == n_g1 and len([l for l in lg if "HALF-BIND-RETRY HB-T1" in l]) == 1
   and n_ev("HB-T1", "CONFIRM card=HB-T1") == 1 and n_ev("HB-T1", "bind session_id") == 1)

# ============ T2 已签绿形零打扰：confirm 在场=不重 bind（不刷流水） ============
mk_claimed("HB-T2", "l2")
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    _cli.run(["bind", "HB-T2", "--session", "sess-t2"])
b2 = (n_ev("HB-T2", "bind session_id"), n_ev("HB-T2", "CONFIRM card=HB-T2"))
n_g2 = len(GATES)
m.backfill_binds({"HB-T2": "l2"})
ck("T2 已签卡零闸调用零痕，events 不涨一行（cmd_bind 既有幂面保持）",
   gcard("HB-T2").get("confirm") and len(GATES) == n_g2
   and not [l for l in lg if "HALF-BIND-RETRY HB-T2" in l]
   and (n_ev("HB-T2", "bind session_id"), n_ev("HB-T2", "CONFIRM card=HB-T2")) == b2,
   str(GATES[n_g2:])[:120])

# ============ T3 边界三形不重签（session 形/DONE 形/未绑形） ============
mk_half("HB-T3A", "l3", kind="session")          # PM 直做形=无 dispatcher 签收通道（sweep 同口径不判）
mk_half("HB-T3B", "l4")
patch_card("HB-T3B", setkv={"status": "DONE"})   # 非 CLAIMED=不重签（bind 会 die，不凑上去）
mk_claimed("HB-T3C", "l5")                        # 未绑形：session_id 仍 "-"，空转录根必探不到
n_g3 = len(GATES)
m.backfill_binds({"HB-T3A": "l3", "HB-T3B": "l4", "HB-T3C": "l5"})
ck("T3a kind=session 形不重 bind（卡面无 confirm 也不碰）",
   len([l for l in lg if "HALF-BIND-RETRY HB-T3A" in l]) == 0 and not gcard("HB-T3A").get("confirm"))
ck("T3b 卡面 DONE 形不重 bind（防对非在制卡凑 die 调用）",
   len([l for l in lg if "HALF-BIND-RETRY HB-T3B" in l]) == 0)
ck("T3c 未绑形走探测不走半成核对（连拍账+1、零痕、闸调用只许 DONE/session 零笔）",
   json.load(open(m.BIND_STREAK, encoding="utf-8")).get("HB-T3C") == 1
   and len([l for l in lg if "HALF-BIND-RETRY HB-T3C" in l]) == 0 and len(GATES) == n_g3,
   str(GATES[n_g3:])[:120])

# ============ T4 撤闸差分对：跳检恢复只认 session_id 旧形=半成不自愈、铃不停 ============
_s4 = cut(DP_SRC, 'and not c.get("confirm")', "if False:  # 撤闸（差分注入）＝跳检回旧形\n")
m4, lg4 = load_dispatch(src_text=_s4, tag="dpcut")
mk_half("HB-T4", "l6")
n_g4 = len(GATES)
m4.backfill_binds({"HB-T4": "l6"})
ck("T4a 撤闸实跑=零痕零闸调用、卡面 confirm 永缺（旧形「签而不收」复发=本锁红得起来）",
   len([l for l in lg4 if "HALF-BIND-RETRY HB-T4" in l]) == 0 and len(GATES) == n_g4
   and not gcard("HB-T4").get("confirm"))
m4.sweep_confirm_miss({"HB-T4": "l6"})
ck("T4b 撤闸形铃不停（sop-miss.json 持续在册）", "HB-T4" in sop_acct(m4), str(sop_acct(m4)))
m.backfill_binds({"HB-T4": "l6"})                # 现件形对照：同卡当场补成=红绿两端都钉死
ck("T4c 同卡现件形对照=一拍补落 confirm+HALF-BIND-RETRY 痕（差分对另一半）",
   gcard("HB-T4").get("confirm") and len([l for l in lg if "HALF-BIND-RETRY HB-T4" in l]) == 1)
m.sweep_confirm_miss({"HB-T4": "l6"})
ck("T4d 现件形补成后铃停退账", "HB-T4" not in sop_acct(m), str(sop_acct(m)))

# ============ T5 源钉：痕唯一出处+快照整拍一份 ============
ck("T5a HALF-BIND-RETRY 痕唯一出处（trace 一处，别处不出=夜审可 grep）",
   DP_SRC.count('trace("HALF-BIND-RETRY') == 1)
ck("T5b cards 快照整拍懒拿一份（勿逐卡重复 IO；未绑卡不碰 graph 读）",
   DP_SRC.count("if cards is None:") == 1)

# ============ 汇总 ============
bad = [o for o in oks if not o[1]]
print("SOP_HALFBIND_%s cases=%d" % ("PASS" if not bad else "FAIL", len(oks)))
if bad:
    print("RED:", "；".join(n for n, _, _ in bad))
    sys.exit(1)
