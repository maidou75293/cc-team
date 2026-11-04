#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify-dryrun-gate-test — 发卡即考试：试跑闸红绿锁（REQ-VERIFY-DRYRUN-1，业主信箱 seq=31③）。
病根两例（族谱 docs/系统保障清单.md §八）：REQ-OSS-QUICKSTART-1 判据被 shell 提前求值烙进
乱码碎片，rc=127 `$'\\xxx': command not found` 连拍待验收；BUG-AGENT-KEY-FIX-1 `python -I <bash
脚本>` 跨语言永红六连拒。静态闸族（bashn/crosslang）拦已知病形，拦不住「格式对但跑不通」——
本闸=机读兜底：create/set/start 带判据当场锁外实跑一次，非零默认拒（R2 red-first 豁免
「--files 声明的缺失产物路径」），rc+tail 落 events 试跑痕（TRIAL-PASS/TRIAL-REDFIRST/TRIAL-REJECT）。

跑法：bash -c "cd cc-team && python -I tests/verify-dryrun-gate-test.py" → 尾行 VERIFY_DRYRUN_GREEN
红自证（T5 内建差分对）：摘 cli.run 的 verify_trial_precheck(a) 调用=T1/T3/T4 场景在撤闸副本上
全被放行（等价于本锁 T1/T3/T4 断言红）——守卫验得红，不是空检。T8 另锁 reconcile 的 TRIAL- 跳过
分支：摘掉=被拒卡号被幽灵扫成每圈 ALARM（fuse 伪卡号同案），本断言必红。
沙箱 CC_TEAM_HOME 手法同 gate-bashn-check.py（import ccards 前钉 TMP；无 registry.json=道册缺席）。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

sys.dont_write_bytecode = True  # REQ-OSSPATH-1 配套：防 pyc 嵌宿主绝对路径→判据 grep 假红
for _s in (sys.stdout, sys.stderr):  # BUG-CCARDS-UTF8-1 同式：win GBK 控制台打中文判据名不碎码
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
PKG_SRC = os.path.realpath(os.path.join(HERE, "..", "ccards"))  # 被测包源（T5 复制母本）

TMP = tempfile.mkdtemp(prefix="vdry-")
os.environ["CC_TEAM_HOME"] = TMP          # 必须在 import ccards 之前钉沙箱
sys.path.insert(0, os.path.dirname(HERE))
os.makedirs(os.path.join(TMP, "state"))
from ccards import cli                       # noqa: E402

EVENTS = os.path.join(TMP, "state", "events.log")


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


def show_out(cid):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = run("show", cid)
    return rc, out.getvalue()


def ev_lines(cid):
    """该卡在 events.log 的试跑痕行（extra 前缀 TRIAL-；reconcile 已跳过不入状态账）。"""
    if not os.path.exists(EVENTS):
        return []
    with open(EVENTS, encoding="utf-8", errors="replace") as f:
        return [ln for ln in f.read().splitlines()
                if ("|%s|" % cid) in ln and "TRIAL-" in ln]


oks = []


def ck(name, got, want):
    oks.append((name, got == want, "got=%r want=%r" % (got, want)))


# ── T1 恒红判据（false 类，静态闸不可见）→ create 被拒 + events 有试跑痕 ──────────
rc, _o, err1 = run_cap("create", "VD-T1", "--title", "恒红判据", "--verify", "false")
ck("T1 create 挂恒红判据 false 被拒", rc, 1)
ck("T1 被拒卡账上零残留（卡不存在）", show_out("VD-T1")[0] != 0, True)
ck("T1 events 落 TRIAL-REJECT 试跑痕", any("TRIAL-REJECT" in l for l in ev_lines("VD-T1")), True)
ck("T1 拒绝归因带 rc 与尾段（发卡即考试点名卡号）",
   ("rc=1" in err1) and ("REQ-VERIFY-DRYRUN-1" in err1), True)

# ── T2 red-first 对照：--files 声明未落盘产物 + 判据指它 → 放行 + TRIAL-REDFIRST 痕 ──
RED_ARTIFACT = "cc-team/tests/vdry-redfirst-not-landed.py"
ck("T2 create --files 声明缺失产物+判据指它=red-first 放行",
   run("create", "VD-T2", "--title", "红基线卡", "--files", RED_ARTIFACT,
       "--verify", "python -I " + RED_ARTIFACT), 0)
ck("T2 卡面判据已落账", show_out("VD-T2")[1].count(RED_ARTIFACT) >= 2, True)
t2ev = ev_lines("VD-T2")
ck("T2 events 落 TRIAL-REDFIRST 痕（谁豁免=账上可查）",
   any("TRIAL-REDFIRST" in l for l in t2ev), True)
ck("T2 豁免痕点名缺失路径（声明形）与规则号",
   any("missing=" + RED_ARTIFACT in l and "rule=R2" in l for l in t2ev), True)

# ── T3 set 换毒判据被拒，且卡面判据不动（账零污染）────────────────────────────
ck("T3 前置：干净判据卡创建", run("create", "VD-T3", "--title", "t", "--verify", "true"), 0)
rc, _o, err3 = run_cap("set", "VD-T3", "--verify", "false")
ck("T3 set 换恒红判据被拒", rc, 1)
rc3s, txt3 = show_out("VD-T3")
ck("T3 被拒后卡面判据仍是 true（毒文本零入账）", ('"verify": "true"' in txt3) and ("false" not in txt3), True)
ck("T3 events 落 entry=set 的 TRIAL-REJECT 痕",
   any("TRIAL-REJECT" in l and "entry=set" in l for l in ev_lines("VD-T3")), True)

# ── T4 rc=127 且点名路径不在 files → 拒 ────────────────────────────────────────
rc, _o, err4 = run_cap("create", "VD-T4", "--title", "t",
                       "--files", "cc-team/tests/declared-somewhere-else.py",
                       "--verify", "bash cc-team/tests/undeclared-missing.sh")
ck("T4 rc=127 非 files 内路径被拒（files 声明≠免检金牌）", rc, 1)
ck("T4 被拒卡账上零残留", show_out("VD-T4")[0] != 0, True)
ck("T4 归因带 R2 未命中码", ("R2-no-match" in err4) or ("R2" in err4 and "拒绝" in err4), True)

# ── T5 撤闸差分对：摘试跑调用后，T1/T3/T4 同形场景全被放行=本锁三断言必红 ─────────
SB5 = tempfile.mkdtemp(prefix="vdry-t5-")
pkg5 = os.path.join(SB5, "ccards")
team5 = os.path.join(SB5, "team")
os.makedirs(os.path.join(team5, "state"))
shutil.copytree(PKG_SRC, pkg5)
_cli5 = os.path.join(pkg5, "cli.py")
with open(_cli5, encoding="utf-8") as f:
    _src = f.read()
ck("T5 前置：调用点存在且唯一（禁复刻闸）", _src.count("verify_trial_precheck(a)"), 1)
with open(_cli5, "w", encoding="utf-8", newline="") as f:
    f.write(_src.replace("verify_trial_precheck(a)", "(lambda _a: None)(a)"))
SCN = os.path.join(SB5, "scenario5.py")
with open(SCN, "w", encoding="utf-8", newline="") as f:
    f.write(
        "import os, sys\n"
        "sys.dont_write_bytecode = True\n"
        "os.environ['CC_TEAM_HOME'] = sys.argv[2]\n"
        "sys.path.insert(0, os.path.abspath(sys.argv[1]))\n"  # argv[1]=包根目录（ccards 的上一级）
        "from ccards import cli\n"
        "def go(*a):\n"
        "    try:\n"
        "        return cli.run(list(a))\n"
        "    except SystemExit as e:\n"
        "        return e.code if isinstance(e.code, int) else 1\n"
        "r1 = go('create', 'T5-T1', '--title', 't', '--verify', 'false')\n"
        "go('create', 'T5-T3', '--title', 't', '--verify', 'true')\n"
        "r3 = go('set', 'T5-T3', '--verify', 'false')\n"
        "r4 = go('create', 'T5-T4', '--title', 't', '--files', 'other.py',\n"
        "        '--verify', 'bash cc-team/tests/undeclared-missing.sh')\n"
        "print('T5RC %s %s %s' % (r1, r3, r4))\n"
    )
p5 = subprocess.run([sys.executable, "-I", SCN, SB5, team5],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
txt5 = (p5.stdout or b"").decode("utf-8", "replace")
ck("T5 撤闸副本 T1 形恒红判据被放行（=有闸时 T1 断言红，守卫验得红）",
   "T5RC 0 0 0" in txt5, True)

# ── T6 干净对照：bash -c "true" 试跑绿 → 放行 + TRIAL-PASS 痕 ──────────────────
ck("T6 干净判据 bash -c true 放行",
   run("create", "VD-T6", "--title", "t", "--verify", 'bash -c "true"'), 0)
ck("T6 events 落 TRIAL-PASS 痕",
   any("TRIAL-PASS" in l for l in ev_lines("VD-T6")), True)

# ── T7 超时注入：CARD_TRIAL_TIMEOUT_SEC=1 + sleep 2 → 拒且痕标 timeout ─────────
os.environ["CARD_TRIAL_TIMEOUT_SEC"] = "1"
try:
    rc, _o, err7 = run_cap("create", "VD-T7", "--title", "t", "--verify", "sleep 2")
finally:
    os.environ.pop("CARD_TRIAL_TIMEOUT_SEC", None)
ck("T7 超时判据被拒", rc, 1)
ck("T7 被拒卡账上零残留", show_out("VD-T7")[0] != 0, True)
ck("T7 痕标 rc=124 与 TIMEOUT",
   any("rc=124" in l and "TIMEOUT" in l.upper() for l in ev_lines("VD-T7")), True)

# ── 横切：三口一致——start --verify 补毒判据同样被拒（闸不厚此薄彼）──────────────
ck("横切 前置：HOLD 干净卡", run("create", "VD-ST", "--title", "t", "--status", "HOLD",
                             "--verify", "true"), 0)
rc, _o, errs = run_cap("start", "VD-ST", "--owner", "slot94", "--verify", "false")
ck("横切 start --verify 补恒红判据被拒", rc, 1)
ck("横切 events 落 entry=start 痕",
   any("TRIAL-REJECT" in l and "entry=start" in l for l in ev_lines("VD-ST")), True)

# ── T9 中文路径 red-first（PM 合流轮 2026-11-04 实锤补锁）────────────────────
# 病形：本仓大量中文产物路径（docs/快速开始-*.md 等），_TRIAL_MISS_RES 旧字符类
# [A-Za-z0-9_...] 不含 CJK，缺失报文尾段被截成「…-20261104.md」与声明全路径比对必
# no-match → R2 豁免对中文产物失效（实锤：REQ-OSS-QUICKSTART-1 换判据被闸拒 rc=2
# R2-no-match，尾段中文完好非编码问题）。红基线=旧类形本组 T9-① 断言红 rc=1。
CJK_ARTIFACT = "docs/快速开始-中文产物未落盘-T9.md"
ck("T9 create --files 中文缺失产物+判据指它=red-first 放行",
   run("create", "VD-T9", "--title", "中文路径卡", "--files", CJK_ARTIFACT,
       "--verify", 'grep -q 验收 "' + CJK_ARTIFACT + '"'), 0)
t9ev = ev_lines("VD-T9")
ck("T9 events 落 TRIAL-REDFIRST 痕且点名中文全路径",
   any("TRIAL-REDFIRST" in l and "missing=" + CJK_ARTIFACT in l for l in t9ev), True)

# ── T8 横切：reconcile 不把 TRIAL 痕记成幽灵卡 ─────────────────────────────────
# 被拒 create 的卡号根本不入图；若计入对账 last，会被幽灵扫成「events 有卡但事实图无此卡」
# 每圈 ALARM（fuse 伪卡号「FUSE」同案）。reconcile_events.py 的 TRIAL- 跳过分支一摘，
# VD-T1/T4/T7 必出现在 stdout=本断言红——守卫验得红，不是空检。
# BOARD_SELFAUDIT_PATH 钉不存在路径=看板自审计走 missing 报警分支，绝不探活体 :8790（沙箱纪律）。
REC = os.path.join(os.path.dirname(HERE), "ops", "reconcile_events.py")
env8 = dict(os.environ, CC_TEAM_HOME=TMP,
            BOARD_SELFAUDIT_PATH=os.path.join(TMP, "no-such-board-audit.py"))
p8 = subprocess.run([sys.executable, "-I", "-X", "utf8", REC], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, timeout=120, env=env8)
# 坑（本锁红自证实抓）：win 子进程 stdout 缺省随 locale GBK 落码，中文判据串按 utf-8 解
# 全碎=匹配恒不中、假绿；-X utf8 钉死（gbkenc-proj 同族）。断言若「恒绿」先查编码再查被测物。
txt8 = (p8.stdout or b"").decode("utf-8", "replace")
ghosts8 = [ln for ln in txt8.splitlines()
           if "events 有卡" in ln and any(c in ln for c in ("VD-T1", "VD-T4", "VD-T7"))]
ck("T8 被拒卡 TRIAL 痕不被 reconcile 记成幽灵卡", ghosts8, [])

bad = [t for t, ok, _ in oks if not ok]
for t, ok, hint in oks:
    print("%s %s%s" % ("PASS" if ok else "FAIL", t, ("｜" + hint) if not ok else ""))
if bad:
    print("VERIFY_DRYRUN_RED fails=%d" % len(bad))
    sys.exit(1)
print("VERIFY_DRYRUN_GREEN n=%d" % len(oks))
