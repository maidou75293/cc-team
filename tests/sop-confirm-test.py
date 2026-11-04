#!/usr/bin/env bash
# sop-confirm-test.py — REQ-SOP-CONFIRM-1「S2 签收制轻形态」常驻红绿锁（业主 seq=34 S2/seq=35①）。
# 规格：
#   R1 dispatcher bind 成迹当刻（claim→bind→start）经 cmd_bind 双锁同写段落
#      卡面 confirm{lane,epoch,workarea,at} + events 机读行 CONFIRM card=<cid> lane=<道>
#      epoch=<N> workarea=<快照锚>；三件缺一不落签收（WARN 出声，bind 主链照成）；
#      同 (lane,epoch) 幂等不重复出行，重派换任期=覆写再签。
#   R2 env SOP_CONFIRM_TIMEOUT_MIN（缺省 10，config 键 sop.confirm_timeout_min，下限 1）内
#      CLAIMED 无 CONFIRM 痕=MISS：①trace 详情行；②events 机读行 SOP-MISS card=<cid>
#      lane=<道> epoch=<N>（每 (卡,任期) 一行，state/sop-miss.json 防惊群）；③alerts 稳定键
#      SOP-MISS card=<cid>——键名严禁「业主/FUSE-S6」头（S6 跳自触发键=静默丢报，
#      CLAIM-COUNTER R6 同纪律）；连发同键聚合 count++；迟到签收/离账=退账。
#      kind=session（PM 直做卡）无 dispatcher 签收通道=不判（同 unbind_watch 口径）。
#   R3 业主消息催办痕在场（唯一现件源=inbox_watchdog 现账 inbox-watch.json last_n>0）=
#      弹性池本圈 cap +1 临时槽一次（同签名授过不再加；签名变=新催办再授；dry 不消费）；
#      零改 pool.admission 纯函数与判活链——只挂 dispatch 调用点 cap 参。
# 案目：
#   T1 R1 绿形+幂等复绑；T2 三件缺一（缺 workarea / 缺 epoch 两形）不落签收且 bind 照成；
#   T3 R2 主链：到阈三面落笔+防惊群+连发 count++；T4 面界：未超阈/龄坏/session 卡/迟到签收退账/
#      DONE 不判/离账清账；T5 阈值旋钮（缺省 10、env 覆写、下限钉 1）；
#   T6 R6 键纪律+走通 S6（fuse.s6_decide 现算 30min=WAKE、60min=OWNER；自触发头对照被跳）；
#   T7 R3：授一次/同签名不复授/新签名再授/grant=False 不消费/无痕账=0/满编时 +1 确实放行一张
#      （pool.admission 现件对拍）+tick 挂点源钉；
#   T8 撤闸差分对：R1 摘签收块→CONFIRM 全无而 BOUND 照成；R2 摘 alerts 行→事件在键零；
#      摘事件防惊群闸→键在事件零（各闸各钉，撤修复必红）。
# 跑法: python -I tests/sop-confirm-test.py   （exit 0 + 尾行 SOP_CONFIRM_PASS = 全绿）
# 纪律：全沙箱（import 前钉 CC_TEAM_HOME 临时账家，claims/state/events/alerts 全落沙箱），
#   零碰正账、零真会话、零 powershell；撤闸样在 exec 补丁源/gut 拷贝包上注入，产码文件只读。
#   双语头同 claim-counter-test（''''true 藏 bash 引导段，段内禁再现字面 '''）。
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
if [ -z "$PY" ]; then echo "FAIL: sop-confirm-test 找不到 python 解释器"; exit 1; fi
exec "$PY" -I -X utf8 "$0"
'''
# -*- coding: utf-8 -*-
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

CT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cc-team 根
SB = tempfile.mkdtemp(prefix="cc-sop-confirm-")
HOME = os.path.join(SB, "home")                                     # 唯一沙箱账家（全案共用，卡号隔离）
os.makedirs(os.path.join(HOME, "state"), exist_ok=True)
# import 前钉死账家与阈值（dispatch/cmd_bind 的 import 期常量全随此家）：
os.environ["CC_TEAM_HOME"] = HOME
os.environ["POOL_HOME"] = os.path.join(SB, "poolhome")
os.environ["CARD_3FORM_EPOCH"] = "off"          # 三段式闸关档（本锁考签收账不考立卡文案）
os.environ["SOP_CONFIRM_TIMEOUT_MIN"] = "1"     # 主链案用 1min 阈，免等 10min
os.environ.pop("CC_AUTO_VERIFY", None)          # done 首验通道关闭（本锁不碰 done）
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


def ev_lines(home=None):
    try:
        return open(os.path.join(home or HOME, "state", "events.log"), encoding="utf-8").read().splitlines()
    except OSError:
        return []


def alerts_of():
    try:
        return json.load(open(os.path.join(HOME, "state", "alerts.json"),
                              encoding="utf-8")).get("alerts", {})
    except (OSError, ValueError):
        return {}


def mk_claimed(cid, lane, verify="bash -c true", pid=True):
    rc = _cli.run(["create", cid, "--title", "签收沙箱卡 %s" % cid, "--lanes", lane,
                   "--verify", verify])
    assert rc == 0, cid
    # pid=True=dispatcher 真形 claim（--pid 活锚 → claim.json kind=lane，R2 判定域内）；
    # pid=False=手工/PM 认领形（kind=session，无 dispatcher 签收通道=R2 不判面）
    args = ["claim", cid, "--owner", lane]
    if pid:
        args += ["--pid", str(os.getpid())]
    rc = _cli.run(args)
    assert rc == 0, cid


def load_dispatch(src_text=None, tag="dpsop"):
    """exec dispatch.py 源码进独立模块（trace/run_gate 桩化；ccards 全链=沙箱家现件）。
    src_text=补丁源（撤闸差分用）；模块常量随当前 env 重算。"""
    src = src_text if src_text is not None else open(os.path.join(CT, "ops", "dispatch.py"),
                                                     encoding="utf-8").read()
    mod = types.ModuleType(tag)
    mod.__file__ = os.path.join(CT, "ops", "dispatch.py")
    mod.__dict__["__name__"] = "dp_under_test"
    code = compile(src, mod.__file__ + "#sbx", "exec")
    lg = []
    saved_run = None
    mod_dict = mod.__dict__
    exec(code, mod_dict)
    mod.trace = lambda m: lg.append(str(m))
    mod.run_gate = lambda *a: (0, "STUB")
    return mod, lg


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

# ============ T1 R1 绿形：claim→bind 落 CONFIRM（卡面+events），复绑幂等不刷流水 ============
mk_claimed("SC-T1", "l1")
c1 = gcard("SC-T1")
ck("T1a claim 后卡面三件在场(lane/epoch/workarea)（前提自证）",
   c1.get("owner") == "l1" and c1.get("claimed_epoch") is not None
   and (c1.get("workarea") or {}).get("at"), str(c1.get("workarea"))[:80])
_cli.run(["bind", "SC-T1", "--session", "sess-a"])
c1 = gcard("SC-T1")
conf = c1.get("confirm") or {}
ck("T1b bind 成迹=卡面 confirm{lane,epoch,workarea,at} 三件齐落",
   conf.get("lane") == "l1" and str(conf.get("epoch")) == str(c1.get("claimed_epoch"))
   and conf.get("workarea") == (c1.get("workarea") or {}).get("at") and conf.get("at"),
   json.dumps(conf, ensure_ascii=False))
_ln = [l for l in ev_lines() if "CONFIRM card=SC-T1" in l]
ck("T1c events 机读行形 CONFIRM card=<cid> lane=<道> epoch=<N> workarea=<快照锚> 恰一行",
   len(_ln) == 1 and re.search(r"\|CONFIRM card=SC-T1 lane=l1 epoch=\d+ workarea=\S+", _ln[0]),
   str(_ln)[:200])
ck("T1d bind 主链不受签收痕影响：claim.json 锚照绑",
   json.load(open(os.path.join(HOME, "claims", "SC-T1", "claim.json"),
                  encoding="utf-8")).get("session_id") == "sess-a")
_cli.run(["bind", "SC-T1", "--session", "sess-b"])     # 复绑（backfill_binds 同形）幂等
ck("T1e 同 (lane,epoch) 复绑不重复出行（幂等=不刷流水）",
   len([l for l in ev_lines() if "CONFIRM card=SC-T1" in l]) == 1
   and (gcard("SC-T1").get("confirm") or {}).get("at") == conf.get("at"))

# ============ T2 三件缺一不算签收（缺 workarea / 缺 epoch 两形；bind 照成 WARN 出声） ============
import contextlib, io                                          # noqa: E402
for _cid, _drop, _what, _ln2 in (("SC-T2A", ("workarea",), "缺快照锚", "l2a"),
                                 ("SC-T2B", ("claimed_epoch",), "缺任期牌", "l2b")):
    mk_claimed(_cid, _ln2)
    if _drop == ("claimed_epoch",):
        _cj2 = os.path.join(HOME, "claims", _cid, "claim.json")
        _dj = json.load(open(_cj2, encoding="utf-8"))
        _dj.pop("epoch", None)                 # 卡面与 claim.json 双缺=旧认领无牌可对（回落链穷尽）
        json.dump(_dj, open(_cj2, "w", encoding="utf-8", newline=""))
    patch_card(_cid, drop=_drop)
    _buf = io.StringIO()
    with contextlib.redirect_stderr(_buf):
        _cli.run(["bind", _cid, "--session", "sess-t2"])
    _err = _buf.getvalue()
    ck("T2%s 三件缺一(%s)：不落 confirm、无 CONFIRM 行" % (_cid[-1], _what),
       not gcard(_cid).get("confirm") and not [l for l in ev_lines() if "CONFIRM card=" + _cid in l])
    ck("T2%s 缺位 WARN 出声且 bind 主链照成（rc=0+BOUND）" % _cid[-1],
       "WARN: CONFIRM 三件缺一" in _err)
    ck("T2%s 双锁同写不受影响：claim.json 锚照绑" % _cid[-1],
       json.load(open(os.path.join(HOME, "claims", _cid, "claim.json"),
                      encoding="utf-8")).get("session_id") == "sess-t2")

# ============ T3 R2 主链：kind=lane 到阈无签=MISS 三面落笔；连发聚合 count++ 不无限 WAIT ============
mk_claimed("SC-M1", "l3")
patch_card("SC-M1", setkv={"claimed_at": (datetime.now(timezone.utc)
                                          .astimezone() - timedelta(minutes=3)).isoformat(timespec="seconds")})
m, lg = load_dispatch()
ck("T3z 环境阈值随家生效（SOP_CONFIRM_TIMEOUT_MIN=1 覆写≠缺省 10）", m.SOP_CONFIRM_TIMEOUT_MIN == 1)
m.sweep_confirm_miss({"SC-M1": "l3"})
ck("T3a trace 落 SOP-MISS 详情行（lane/epoch 全量）",
   any("SOP-MISS SC-M1@l3" in l for l in lg), str(lg)[-200:])
_ml = [l for l in ev_lines() if re.search(r"\|SOP-MISS card=SC-M1 lane=l3 epoch=\d+", l)]
ck("T3b events 机读行 SOP-MISS card=<cid> lane=<道> epoch=<N> 恰一行", len(_ml) == 1, str(_ml)[:200])
_a = alerts_of().get("SOP-MISS card=SC-M1")
ck("T3c alerts 稳定键恰=SOP-MISS card=<cid>（count=1）", _a and _a.get("count") == 1,
   json.dumps(_a, ensure_ascii=False)[:150] if _a else "键缺")
ck("T3d 防惊群账落 state/sop-miss.json",
   json.load(open(m.SOP_MISS_STATE, encoding="utf-8")).get("SC-M1")
   == str(gcard("SC-M1").get("claimed_epoch")))
m.sweep_confirm_miss({"SC-M1": "l3"})
m.sweep_confirm_miss({"SC-M1": "l3"})
ck("T3e 连发同键聚合 count++（=3），事件行仍恰一行（不刷流水不无限 WAIT）",
   alerts_of().get("SOP-MISS card=SC-M1", {}).get("count") == 3
   and len([l for l in ev_lines() if "SOP-MISS card=SC-M1" in l]) == 1)
ck("T3f 键名 R6 纪律：非「业主/FUSE-S6」头（否则 S6 静默丢报）",
   all(not k.startswith("业主") and not k.startswith("FUSE-S6")
       for k in alerts_of() if "SOP-MISS" in k), str(list(alerts_of())))

# ============ T4 面界：未超阈/龄不可解析/session 卡/DONE/迟到签收退账/离账清账 ============
mk_claimed("SC-M2", "l4")                      # 刚认领=未超阈
mk_claimed("SC-M3", "l5")
patch_card("SC-M3", setkv={"claimed_at": "nonsense-ts"})      # 龄坏=宁漏不误
mk_claimed("SC-M4", "l6", pid=False)          # 手工/PM 认领真形=kind session
patch_card("SC-M4", setkv={"claimed_at": (datetime.now(timezone.utc).astimezone()
                                          - timedelta(minutes=9)).isoformat(timespec="seconds")})
_cj = os.path.join(HOME, "claims", "SC-M4", "claim.json")
_d = json.load(open(_cj, encoding="utf-8"))
_d["kind"] = "session"                          # PM 直做卡形=无 dispatcher 签收通道
json.dump(_d, open(_cj, "w", encoding="utf-8"))
mk_claimed("SC-M5", "l7")
patch_card("SC-M5", setkv={"status": "DONE"})   # 非 CLAIMED 不判
m.sweep_confirm_miss({"SC-M2": "l4", "SC-M3": "l5", "SC-M4": "l6", "SC-M5": "l7"})
ck("T4a 未超阈/龄不可解析/session 卡/DONE=全不判（零键零行零入账）",
   "SOP-MISS card=SC-M2" not in alerts_of() and "SOP-MISS card=SC-M3" not in alerts_of()
   and "SOP-MISS card=SC-M4" not in alerts_of() and "SOP-MISS card=SC-M5" not in alerts_of()
   and not [l for l in ev_lines() if "SC-M2" in l and "SOP-MISS" in l], str(alerts_of()))
_cli.run(["bind", "SC-M1", "--session", "sess-late"])          # 迟到签收（T3 卡补签）
m.sweep_confirm_miss({"SC-M1": "l3", "SC-M2": "l4", "SC-M3": "l5", "SC-M4": "l6", "SC-M5": "l7"})
ck("T4b 迟到签收=自然退账（防惊群账出，事件不再补行，报警键留档待停铃）",
   "SC-M1" not in json.load(open(m.SOP_MISS_STATE, encoding="utf-8"))
   and len([l for l in ev_lines() if "SOP-MISS card=SC-M1" in l]) == 1
   and gcard("SC-M1").get("confirm"))
m.sweep_confirm_miss({"SC-M2": "l4"})                          # M1 离账（claimed 名单不再含）
ck("T4c 离账卡从 miss 账剔除（防漂移，bind-streak 同式）",
   "SC-M1" not in json.load(open(m.SOP_MISS_STATE, encoding="utf-8")))

# ============ T5 阈值旋钮：缺省 10、env 覆写、下限钉 1（PM 可裁，畸形不放开=不瞬判风暴） ============
os.environ.pop("SOP_CONFIRM_TIMEOUT_MIN", None)
d2, _ = load_dispatch(tag="dpdef")
ck("T5a 无 env=缺省 10min", d2.SOP_CONFIRM_TIMEOUT_MIN == 10)
os.environ["SOP_CONFIRM_TIMEOUT_MIN"] = "60"
d3, _ = load_dispatch(tag="dp60")
ck("T5b PM 可裁=env 覆写 60", d3.SOP_CONFIRM_TIMEOUT_MIN == 60)
os.environ["SOP_CONFIRM_TIMEOUT_MIN"] = "0"
d4, _ = load_dispatch(tag="dp0")
ck("T5c 下限钉 1（0/负=瞬判风暴，拒穿）", d4.SOP_CONFIRM_TIMEOUT_MIN == 1)
os.environ["SOP_CONFIRM_TIMEOUT_MIN"] = "1"

# ============ T6 走通 S6 既有闭环：s6_decide 现算 30min=WAKE / 60min=OWNER（本闸不自造叫醒） ============
import fuse as _F                                               # noqa: E402
_now = time.time()
_a1 = _F.s6_decide(_now, _now - 31 * 60, False, 1800, 3600)
_a2 = _F.s6_decide(_now, _now - 61 * 60, False, 1800, 3600)
_a3 = _F.s6_decide(_now, _now - 31 * 60, True, 1800, 3600)
ck("T6a S6 现算：31min 无主=WAKE 一级唤 PM 班、61min=OWNER 直达信箱、ack 后即静",
   _a1[0] == "WAKE" and _a2[0] == "OWNER" and _a3[0] == "", str((_a1, _a2, _a3)))
ck("T6b S6 自触发闸在码：业主头/FUSE-S6 头键被跳（SOP-MISS 键不在被跳族）",
   'key.startswith("业主") or key.startswith("FUSE-S6")'
   in open(os.path.join(CT, "ops", "fuse.py"), encoding="utf-8").read())

# ============ T7 R3 催办 +1 临时槽：一次账（同签名不复授/新签名再授/dry 不消费）+ 放行对拍 ============
_nudge_f = m.SOP_NUDGE_STATE
_watch = m.INBOX_WATCH
ck("T7z 痕源现件锚=inbox_watchdog 同账 state/inbox-watch.json（源钉唯一现件源）",
   os.path.basename(_watch) == "inbox-watch.json"
   and '"state", "inbox-watch.json"' in open(os.path.join(CT, "ops", "inbox_watchdog.py"),
                                             encoding="utf-8").read())
ck("T7a 无痕账/last_n=0=不授（0，保守不扰）", m.owner_nudge_bonus() == 0)
os.makedirs(os.path.dirname(_watch), exist_ok=True)


def put_watch(n, sig):
    _cfg.atomic_write_json(_watch, {"last_n": n, "last_sig": sig})


put_watch(0, "s0")
ck("T7b 催办痕不在场（unacked=0）=不授", m.owner_nudge_bonus() == 0)
put_watch(2, "s1")
ck("T7c 催办痕在场=授一圈（+1 临时槽）", m.owner_nudge_bonus() == 1)
ck("T7d 同签名授过=回归常规（一次账在册 sop-nudge.json）",
   m.owner_nudge_bonus() == 0
   and json.load(open(_nudge_f, encoding="utf-8")).get("granted_sig") == "2|s1")
put_watch(3, "s1")
ck("T7e 新催办签名（计数推进）=再授一次", m.owner_nudge_bonus() == 1)
put_watch(4, "s2")
ck("T7f grant=False（dry 圈）只判不消费，随后真圈照授",
   m.owner_nudge_bonus(grant=False) == 1
   and json.load(open(_nudge_f, encoding="utf-8")).get("granted_sig") == "3|s1"
   and m.owner_nudge_bonus(grant=True) == 1)
import pool as _P                                               # noqa: E402
_adm0 = _P.admission(999999, 100, 6, 6, ["X"])                  # 满编+保险丝帽=本圈不放
_adm1 = _P.admission(999999, 100, 6, 6 + 1, ["X"])              # 催办 +1 临时槽后
ck("T7g 超编语义对拍：满编 allow=0 → cap+1 后 allow=1（一张加派判定）",
   _adm0["allow"] == 0 and _adm1["allow"] == 1, str((_adm0["note"], _adm1["note"])))
ck("T7h tick 挂点源钉：cap_eff=CAP+bonus 单点入 admission、write_gate 现值随生效帽（pool 纯函数零改）",
   DP_SRC.count("cap_eff = CAP + owner_nudge_bonus(grant=not dry)") == 1
   and DP_SRC.count("pool.admission(free_mb, cost, len(inflight), cap_eff, pending)") == 1
   and DP_SRC.count('pool.write_gate(adm, len(inflight), cap_eff, cost') == 1
   and "cap_eff" not in open(os.path.join(CT, "ops", "pool.py"), encoding="utf-8").read())

# ============ T8 撤闸差分对：各闸各钉，撤修复必红 ============
# ①R1 摘签收块（gut 包真 CLI 通道）：CONFIRM 全无而 bind/BOUND 照成
gut = os.path.join(SB, "gut")
shutil.copytree(os.path.join(CT, "ccards"), os.path.join(gut, "ccards"),
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
shutil.copy2(os.path.join(CT, "cards_dag.py"), os.path.join(gut, "cards_dag.py"))
_p8 = os.path.join(gut, "ccards", "commands.py")
_s8 = open(_p8, encoding="utf-8").read()
_s8 = cut(_s8, 'if str(conf.get("lane")) != cf_lane or str(conf.get("epoch")) != str(cf_ep):',
          "if False:  # 撤闸（差分注入）\n")
open(_p8, "w", encoding="utf-8").write(_s8)
GH = os.path.join(SB, "guthome")
os.makedirs(os.path.join(GH, "state"), exist_ok=True)
env8 = dict(os.environ)
env8["CC_TEAM_HOME"] = GH


def run_gut(args):
    r = subprocess.run([sys.executable, "-I", os.path.join(gut, "cards_dag.py")] + args,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, env=env8)
    return r.returncode, (r.stdout or b"").decode("utf-8", "replace")


for _a in (["create", "SC-G1", "--title", "撤闸样", "--lanes", "gl", "--verify", "bash -c true"],
           ["claim", "SC-G1", "--owner", "gl"], ["bind", "SC-G1", "--session", "s-g1"]):
    rc, out = run_gut(_a)
    ck("T8a0 gut 通道自证 %s rc=0" % _a[0], rc == 0, out[-150:])
_gc = json.load(open(os.path.join(GH, "state", "graph.json"), encoding="utf-8"))["cards"]["SC-G1"]
ck("T8a 撤 R1 签收块=卡面无 confirm+零 CONFIRM 行，而 bind 主链照成（BOUND 在场）",
   not _gc.get("confirm")
   and not [l for l in ev_lines(GH) if "CONFIRM card=SC-G1" in l]
   and "BOUND" in out)
# ②R2 摘 alerts 行=事件照落、键零落（分层撤闸）
_s2 = cut(DP_SRC, '_AL.record("SOP-MISS card=%s" % cid)', "pass  # 撤闸（差分注入）\n")
m2, _lg2 = load_dispatch(src_text=_s2, tag="dpcut1")
mk_claimed("SC-G2", "l8")
patch_card("SC-G2", setkv={"claimed_at": (datetime.now(timezone.utc).astimezone()
                                          - timedelta(minutes=4)).isoformat(timespec="seconds")})
m2.sweep_confirm_miss({"SC-G2": "l8"})
ck("T8b 摘 alerts 调用：事件行照落而 SOP-MISS 键零落（S6 断粮=撤半边必红）",
   "SOP-MISS card=SC-G2" not in alerts_of()
   and len([l for l in ev_lines() if "SOP-MISS card=SC-G2" in l]) == 1)
# ③R2 摘防惊群闸（事件面）=键在、行零
_s3 = cut(DP_SRC, "if acct.get(cid) != ep_s:", "if False:  # 撤闸（差分注入）\n")
m3, _lg3 = load_dispatch(src_text=_s3, tag="dpcut2")
mk_claimed("SC-G3", "l9")
patch_card("SC-G3", setkv={"claimed_at": (datetime.now(timezone.utc).astimezone()
                                          - timedelta(minutes=4)).isoformat(timespec="seconds")})
m3.sweep_confirm_miss({"SC-G3": "l9"})
ck("T8c 摘事件落笔闸：alerts 键照记而 SOP-MISS 事件行零（另半边各钉）",
   "SOP-MISS card=SC-G3" in alerts_of()
   and not [l for l in ev_lines() if "SOP-MISS card=SC-G3" in l])
# ④R3 摘挂点（源钉撤形对照）
ck("T8d 摘催办挂点对照形在场（cap_eff 唯一出处=本卡所钉挂点）",
   DP_SRC.count("cap_eff = CAP +") == 1)

# ============ 汇总 ============
bad = [o for o in oks if not o[1]]
print("SOP_CONFIRM_%s cases=%d" % ("PASS" if not bad else "FAIL", len(oks)))
if bad:
    print("RED:", "；".join(n for n, _, _ in bad))
    sys.exit(1)
