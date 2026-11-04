#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stoploss-crosslang-test — 业主 seq=30 双守卫锁（系统保障，PM 常责直落）。

病根实锤：BUG-AGENT-KEY-FIX-1 四连拒空转（05:51/05:58/06:06/06:16），第 4 笔 verdict
原文 tail=[secret.sh line 112 SyntaxError: unmatched ')']——卡面判据 `python -I
scripts/ops/secret.sh check` 用 Python 解释器解析 bash 脚本，结构永红，工人怎么返工
都救不了。两把锁：
A) 跨语言发卡闸 _check_verify_crosslang（MECH-GATE-BASHN-1 同族补全·反向）：
   判据含 `python [-旗] <file>.sh` 时 create/set/start 三口统一拒并归因；
   修正形态（bash 跑 .sh / python 跑 .py 混排）与 `python -c '…'` 不误伤=两侧都锁。
B) STOPLOSS 停派闸（下一次 REJECT 后不再重派）：hold 含 STOPLOSS 时 engine 道派/池派
   双不供、claim 拒领；set --hold '' 清失=复原对照锁（证明 S 段之红只归因于闸）。

跑法：python -I cc-team/tests/stoploss-crosslang-test.py → 尾行 STOPLOSS-CROSSLANG-GREEN n=N
红自证（变异实录 2026-11-04 副本树跑——现网树不动——，不常驻本文件防自匹配假绿）：
  M1 摘 create/set/start 三口 _check_verify_crosslang 调用 → RED fails=10
     （X01~X06 毒形照发卡+X11/X12 set 漏拦连坐+X13/X14 start 连坐）；
  M2 摘 engine.tick 两处 _stoploss 过滤 → RED fails=2（S14/S15 挂失卡仍被供派）；
  M3 摘 cmd_claim 停派闸 → RED fails=3（S16 放行，S19/S20 因卡已被放行 claim 连坐）；
  三者恢复后 GREEN n=24 对照实录。
链锁演进注记（REQ-CARD-TYPEGATE-1，业主 seq=32/31① 2026-11-04）：X07 夹具按新「判据单行为
测试」规矩从放行翻拒绝（拼案范本形）；放行侧与 S 段夹具随父卡 REQ-VERIFY-DRYRUN-1 试跑闸补
--files 走 R2 red-first（同 gate-bashn 适配式，断言本体零改动）。上方 M 数=旧夹具年代实录，
新形态撤闸差分由 card-type-gate-test.py K12 三对承担。
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

TMP = tempfile.mkdtemp(prefix="stoploss-xl-")
os.environ["CC_TEAM_HOME"] = TMP          # 必须在 import ccards 之前钉沙箱（同 gate-bashn 手法）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs(os.path.join(TMP, "state"))   # 无 registry.json=道册缺席，_check_lanes 跳过
from ccards import cli, engine            # noqa: E402


def run(*args):
    try:
        return cli.run(list(args))
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def run_cap(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = run(*args)
    return rc, out.getvalue(), err.getvalue()


def show(cid):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = run("show", cid)
    return rc, out.getvalue()


oks = []


def ck(name, got, want):
    oks.append((name, got == want, "got=%r want=%r" % (got, want)))


# ── A) 跨语言发卡闸·拒绝侧 ────────────────────────────────────────────────
ck("X01 create 实锤形态 python -I <.sh> 必闸红",
   run("create", "SL-X1", "--title", "t",
       "--verify", "python -I scripts/ops/secret.sh check"), 1)
ck("X02 create 嵌套 bash -c \"python … .sh\" 必闸红（文本含毒即拒）",
   run("create", "SL-X2", "--title", "t",
       "--verify", 'bash -c "python -I scripts/ops/secret.sh check"'), 1)
ck("X03 create python3 <.sh> 变体必闸红",
   run("create", "SL-X3", "--title", "t", "--verify", "python3 foo.sh"), 1)
rc_e, _o, err_e = run_cap("create", "SL-X4", "--title", "t", "--verify", "python -I a.sh check")
ck("X04 拒绝信息带归因（病例卡号+bash 正解）",
   ("BUG-AGENT-KEY-FIX-1" in err_e) and ("bash <file>.sh" in err_e), True)
ck("X05 被拒卡账上零残留", show("SL-X1")[0] != 0, True)
ck("X06 create 毒形多旗连珠 python -E -s <.sh> 必闸红",
   run("create", "SL-X6", "--title", "t",
       "--verify", "python -E -s scripts/ops/secret.sh list"), 1)

# ── A) 跨语言发卡闸·放行侧（防误伤；含本病例修复建议形） ──────────────────
# REQ-CARD-TYPEGATE-1 连锁修正（业主 seq=31① 机读化，2026-11-04）：X07 夹具从「放行」翻「拒绝」——
# `python …py && bash …check` 正是被「判据单行为测试」规矩禁掉的拼案范本（一卡一行为测试，
# 状态检查不许外挂）；闸=commands._check_verify_single，锁=card-type-gate-test.py K10。
# 其余放行侧夹具同轮适配父卡 REQ-VERIFY-DRYRUN-1 试跑闸：tmp 目录里本不存在的目标补 --files
# 声明走 R2 red-first（同 gate-bashn/lane-book 适配式，静态闸断言本体零改动）。
ck("X07 拼案范本形（python 跑 .py && bash 跑 .sh）被 G4 拒（原放行——seq=31① 机读化后翻拒）",
   run("create", "SL-OK1", "--title", "t",
       "--verify", "python -I scripts/e2e/agent-chat-smoke-test.py "
                   "&& bash scripts/ops/secret.sh check"), 1)
ck("X08 bash 直跑 .sh 放行（试跑闸经 --files R2 豁免形）",
   run("create", "SL-OK2", "--title", "t", "--verify", "bash scripts/ops/secret.sh check",
       "--files", "scripts/ops/secret.sh"), 0)
ck("X09 python -c 串内含 .sh 不误伤（旗后首 token 非 .sh；拆去 && 免连坐 G4 拼案闸）",
   run("create", "SL-OK3", "--title", "t",
       "--verify", 'python -I -c "print(1)  # 含 deploy.sh 字样不误伤"'), 0)
ck("X10 bash -n *.sh 语法闸族不受影响（同族回归对照）",
   run("create", "SL-OK4", "--title", "t", "--verify", "bash -n cc-team/tests/*.sh",
       "--files", "cc-team/tests/*.sh"), 0)

# ── A) set / start 同闸口 ─────────────────────────────────────────────────
run("create", "SL-KEEP", "--title", "t", "--verify", "bash keep.sh", "--files", "keep.sh")
ck("X11 set 换毒判据 python bad.sh 必闸红",
   run("set", "SL-KEEP", "--verify", "python bad.sh"), 1)
ck("X12 被拒 set 后卡面仍是旧判据（账零污染）",
   ("keep.sh" in show("SL-KEEP")[1]) and ("bad" not in show("SL-KEEP")[1]), True)
run("create", "SL-ST", "--title", "t", "--verify", "bash ok.sh",
    "--files", "ok.sh,ok2.sh")  # 自由卡（无 lanes）；ok2.sh=X14 start 补判据的 R2 声明位
ck("X13 start 补毒判据 python y.sh 必闸红",
   run("start", "SL-ST", "--owner", "sx", "--verify", "python y.sh"), 1)
ck("X14 start 干净判据对照放行（红只归因于闸；owner=sx 不占 m1 一道一活）",
   run("start", "SL-ST", "--owner", "sx", "--verify", "bash ok2.sh"), 0)

# ── B) STOPLOSS 停派闸 ────────────────────────────────────────────────────
run("create", "SL-A", "--title", "t", "--lanes", "m1", "--verify", "bash a.sh", "--files", "a.sh")
run("create", "SL-B", "--title", "t", "--lanes", "ANY", "--verify", "bash b.sh", "--files", "b.sh")
t0 = engine.tick()
ck("S11 挂失前基线：道派 m1 供 SL-A", t0["claimable"].get("m1"), "SL-A")
ck("S12 挂失前基线：池供 SL-B", "SL-B" in (t0["pool_ready"] or []), True)
ck("S13 set --hold STOPLOSS 落账", run("set", "SL-A", "--hold", "STOPLOSS:seq30-test"), 0)
run("set", "SL-B", "--hold", "STOPLOSS:seq30-test")
t1 = engine.tick()
ck("S14 挂失后道派不供（m1 头=None 非 SL-A）", t1["claimable"].get("m1"), None)
ck("S15 挂失后池不供 SL-B", "SL-B" in (t1["pool_ready"] or []), False)
rc_c, _o, err_c = run_cap("claim", "SL-A", "--owner", "m1")
ck("S16 挂失后 claim 拒领+归因", (rc_c == 1) and ("STOPLOSS" in err_c), True)
ck("S17 挂失后 start 亦拒（既有 hold 闸回归钉）", run("start", "SL-B", "--owner", "m9"), 1)
ck("S18 清失 set --hold ''", run("set", "SL-A", "--hold", ""), 0)
t2 = engine.tick()
ck("S19 清失后道派恢复供 SL-A（复原对照=证明 S14/S16 之红只归因于闸）",
   t2["claimable"].get("m1"), "SL-A")
ck("S20 清失后 claim 成功", run("claim", "SL-A", "--owner", "m1"), 0)

bad = [t for t, ok, _ in oks if not ok]
for t, ok, hint in oks:
    print("%s %s%s" % ("PASS" if ok else "FAIL", t, ("｜" + hint) if not ok else ""))
if bad:
    print("STOPLOSS-CROSSLANG-RED fails=%d" % len(bad))
    sys.exit(1)
print("STOPLOSS-CROSSLANG-GREEN n=%d" % len(oks))
