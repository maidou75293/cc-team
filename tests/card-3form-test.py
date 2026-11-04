#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""card-3form-test — REQ-CARD-3FORM-1 卡面三段式红绿锁（GitHub issue form 形态；业主 seq=22 B 项）。

锁四件（卡面验收逐条对读）：
  ① 三段齐全新卡入账（enforce 档 create --src/--story/--plan → 卡面字段可现读）；
  ② 逐段缺失各拒一次，die 文案点名缺哪段，且**撤对应校验必红**——本文件内 M1 变异
     （进程内摘 _three_form_gate，恢复后续测）实锤拒绝归因于闸本身，非环境噪音；
  ③ 存量豁免面不误伤：CARD_3FORM_EPOCH 指界前=未到期（future/未设→warn、off→静默）缺段放行；
     闸只挂 create，界前老卡 enforce 档 set 不返工；
  ④ 术语表联动（seq=4 机制）：②需求栏命中 README STATE-TERMS 别名列黑话→回显标准措辞，
     不阻断；命中清单与 statemachine.TERMS 同源对读（机器表≠README 别名即红，防两张皮）。

生效阶梯（读 config.setting：env CARD_3FORM_EPOCH > TEAM/config.toml gates.card3form_epoch > 缺省）：
  未设/空串=warn（回显不拒，现网未烘日期时不误伤 idle_pulse/各测试房）；off=完全静默；
  YYYY-MM-DD=今日>=该日拒立。本锁用 env 档逐调用翻转（沙箱 TEAM 无 config.toml，文件层必空）。

跑法: python -I cc-team/tests/card-3form-test.py → stdout 逐条 PASS/FAIL，
末行 CARD_3FORM_PASS n=N 即绿；任何 FAIL → CARD_3FORM_RED fails=N + exit 1。
红自证（本文件常驻 M1 之外，另手工两刀，改完恢复）：
  M2 摘 _check 拒立分支（enforce die 改 return）→ F02~F06 全红；
  M3 摘术语回显（_term_hint 首行 return []）→ T01/T03/T04 红。"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

TMP = tempfile.mkdtemp(prefix="card3form-")
os.environ["CC_TEAM_HOME"] = TMP          # 必须在 import ccards 之前钉沙箱（同 gate-bashn 手法）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs(os.path.join(TMP, "state"))   # 无 registry.json=道册缺席，_check_lanes 跳过（沙箱通道）
os.environ["CARD_3FORM_EPOCH"] = ""       # 起手清 env，逐测显式翻转，防外围环境串档
# 链锁适配（REQ-CARD-TYPEGATE-1，业主 seq=32 定版 2026-11-04）：新 cardtype/三要素闸缺省 warn 档，
# 每次 create 会在 stderr 回显「WARN: … 未带 --type …」等提醒；本锁 E03 断言 off 档**全局**零回显
# （"WARN" not-in 不分辨来源闸），故钉本房 cardtype 档=off 隔离——与上行「起手清 env 防外围串档」
# 同一精神。G4 单行为静态闸与此无关（good_create 判据 `true` 单段形不触发）；父卡试跑闸路径零改动。
os.environ["CARD_TYPE_EPOCH"] = "off"
from ccards import cli, commands          # noqa: E402
from ccards import statemachine as sm     # noqa: E402
from ccards.config import REJECTS         # noqa: E402


def epoch(v):
    os.environ["CARD_3FORM_EPOCH"] = v


def run_cap(*args):
    """跑一条命令收 rc+stdout+stderr（die 归因在 stderr，TERM-WARN 回显在 stdout）。"""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.run(list(args))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def show(cid):
    return run_cap("show", cid)


oks = []


def ck(name, got, want):
    oks.append((name, got == want, "got=%r want=%r" % (got, want)))


def good_create(cid, **kw):
    """三段齐全的现形 create（kw 允许覆盖任一段以演缺失）。"""
    args = ["create", cid, "--title", "t", "--verify", "true", "--lanes", "m1",
            "--src", "业主信箱 seq=22（原话：GitHub issue form 三必填）",
            "--story", "作为 PM，我要新卡立卡即带三段，以便账目自解释；验收=缺段即拒",
            "--plan", "cmd_create 挂闸+rejectlog；红绿锁本文件"]
    for k, v in kw.items():
        if v is None:
            args = args[:args.index("--" + k)] + args[args.index("--" + k) + 2:]
        else:
            i = args.index("--" + k)
            args[i + 1] = v
    return run_cap(*args)


# ── ① enforce 档：三段齐全入账 ────────────────────────────────────────────
epoch("2020-01-01")  # 生效日已过=强制档（本卡 VERIFIED 落 main 后 PM 烘 config.toml 的等价态）
rc, out, err = good_create("F3-OK1")
ck("F01enforce 三段齐全 create 放行 rc=0", rc, 0)
rc_s, txt_s, _e = show("F3-OK1")
ck("F01b卡面 src/story/plan 三字段可现读",
   ('"src"' in txt_s) and ('"story"' in txt_s) and ('"plan"' in txt_s) and ("seq=22" in txt_s), True)

# ── ② enforce 档：逐段缺失各拒一次（点名缺哪段） ──────────────────────────
rc, _o, err = good_create("F3-NO-SRC", src=None)
ck("F02缺①src 必拒 rc=1", rc, 1)
ck("F02b拒绝文案点名①原始来源", "①原始来源" in err, True)
rc, _o, err = good_create("F3-NO-STORY", story=None)
ck("F03缺②story 必拒 rc=1 且点名②整理后需求",
   rc == 1 and "②整理后需求" in err, True)
rc, _o, err = good_create("F3-NO-PLAN", plan=None)
ck("F04缺③plan 必拒 rc=1 且点名③执行计划",
   rc == 1 and "③执行计划" in err, True)
rc, _o, err = good_create("F3-NO-ALL", src=None, story=None, plan=None)
ck("F05三段全缺 一次点名全部三段",
   rc == 1 and ("①原始来源" in err) and ("②整理后需求" in err) and ("③执行计划" in err), True)
rc, _o, err = good_create("F3-BLANK", story="   ")
ck("F06纯空白段视同缺失（strip 后判）", rc == 1 and "②整理后需求" in err, True)
ck("F07被拒卡账上零残留", show("F3-NO-SRC")[0] != 0, True)
ck("F08拒绝归因含生效配置（可追档）", "2020-01-01" in err or "CARD_3FORM_EPOCH" in err, True)

# rejectlog 归口（MECH-REJT-1 现成通道，看板「被拒记录」数据源）
try:
    with open(REJECTS, encoding="utf-8") as f:
        rej = f.read()
except OSError:
    rej = ""
ck("F09缺段拒单逐笔落 rejectlog（ts|create|事由 含卡号）",
   rej.count("|create|") >= 5 and "F3-NO-SRC" in rej and "三段式缺段" in rej, True)

# ── ②b M1 变异：撤对应校验必红（进程内摘闸，恢复后对照续绿） ───────────────
saved = commands._three_form_gate
commands._three_form_gate = lambda a, c: None
rc, _o, _e = good_create("F3-MUT")
ck("M1摘闸后缺段放行=红位存在（拒绝确归因于闸）", rc, 0)
commands._three_form_gate = saved
rc, _o, _e = good_create("F3-MUT2", src=None, story=None, plan=None)
ck("M1b恢复后同形态复拒（无残留变异体）", rc, 1)

# ── ③ 存量豁免面：界前不误伤 ──────────────────────────────────────────────
epoch("2030-12-31")  # 生效日未到=warn 档（判据生效日起强制的「界前」侧）
rc, _o, err = good_create("F3-FUT", src=None, story=None, plan=None)
ck("E01界前日期 缺段放行 rc=0", rc, 0)
ck("E01b界前只 WARN 回显不阻断", ("WARN" in err) and ("三段式" in err), True)
epoch("")  # 未设=缺省 warn 档（现网未烘日期时的世界——idle_pulse/各测试房现形调用不误伤）
rc, _o, err = good_create("F3-DEF", src=None, story=None, plan=None)
ck("E02env 未设 warn 档放行+回显", rc == 0 and "WARN" in err, True)
epoch("off")  # 完全静默档
rc, _o, err = good_create("F3-OFF", src=None, story=None, plan=None)
ck("E03off 档零回显零阻断", rc == 0 and ("WARN" not in err) and ("三段式" not in err), True)
epoch("2020-01-01")  # 回 enforce：界前老卡（warn 档立的、无三段字段）set 通道不返工
rc, _o, _e = run_cap("set", "F3-FUT", "--title", "老卡改名不拦")
ck("E04闸只挂 create：enforce 档对界前老卡 set 放行", rc, 0)

# ── ④ 术语表联动（seq=4 机制，黑话命中回显不阻断） ────────────────────────
rc, out, _e = good_create("F3-TERM", story="作为 worker，我在做这张卡并交卡")
ck("T01enforce 档命中黑话仍入账 rc=0（联动不阻断）", rc, 0)
ck("T01b回显标准措辞：在做→在制(CLAIMED)",
   ("TERM-WARN" in out) and ("在制" in out) and ("CLAIMED" in out), True)
ck("T01c回显标准措辞：交卡→待验(DONE)",
   ("待验" in out) and ("DONE" in out), True)
rc, out, _e = good_create("F3-CLEAN", story="作为 PM，我要卡面账目自解释")
ck("T02无黑话故事 不产生误报回显", ("TERM-WARN" not in out), True)
epoch("")  # warn 档同样联动（提醒面独立于拒立档）
rc, out, _e = good_create("F3-TERMW", story="在做的卡", src="s", plan="p")
ck("T03warn 档黑话回显照常（非静默档都联动）", rc == 0 and ("TERM-WARN" in out), True)
epoch("off")
rc, out, _e = good_create("F3-TERMOFF", story="在做的卡", src="s", plan="p")
ck("T04off 完全静默档不联动（off=零回显语义自洽）", rc == 0 and ("TERM-WARN" not in out), True)
# 命中清单=现有数据源对读：机器表每行标准词必须等于 statemachine.TERMS（seq=4 唯一事实源）
epoch("")
tab = commands._terms_alias_table()
ck("T05命中表非空且读自 README STATE-TERMS（现有数据源，非自造清单）",
   len(tab) >= 7, True)
ck("T06同源钉死：回显标准词=seq=4 术语表 TERMS（两张皮即红）",
   all(sm.TERMS.get(st) == t for _al, t, st in tab), True)
ck("T07别名在册：在做→在制 / 交卡→待验 / 待派→待领",
   (("在做", "在制", "CLAIMED") in tab) and (("交卡", "待验", "DONE") in tab)
   and (("待派", "待领", "OPEN") in tab), True)

# ── 归位收尾：非法配置值不炸闸（按 warn 处理） ────────────────────────────
epoch("not-a-date")
rc, _o, err = good_create("F3-BADCFG", src=None, story=None, plan=None)
ck("N01生效配置乱值 降级 warn 放行且如实报", rc == 0 and ("warn" in err), True)

bad = [t for t, okk, _ in oks if not okk]
for t, okk, hint in oks:
    print("%s %s%s" % ("PASS" if okk else "FAIL", t, ("｜" + hint) if not okk else ""))
if bad:
    print("CARD_3FORM_RED fails=%d" % len(bad))
    sys.exit(1)
print("CARD_3FORM_PASS n=%d" % len(oks))
