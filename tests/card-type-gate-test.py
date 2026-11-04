#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""card-type-gate-test — 卡类型机制红绿锁（REQ-CARD-TYPEGATE-1，业主信箱 seq=32 定版 2026-11-04）。
定版原则「一卡一行为测试，用例可以输、不能不能赛」。四闸：
  G1 判据三要素必填（--c-op/--c-in/--c-expect，缺失拒立；expect 超长=提示级入痕不硬拒）；
  G2 type 字段正式化（枚举 PM 定版 REQ/BUG/TEST/MECH/OPS；--type 必带且与卡号前缀一致；
     存量卡零回填=读侧 card_type() 前缀推导，engine/看板消费面零改动）；
  G3 按类型开卡检查（接父卡 _run_verify_trial 三态：BUG=必须干净红，绿=假案子拒、崩溃形拒并
     归因「先修用例」、红的输出落 docs/evidence/card-open/<cid>.log+events 痕；
     REQ/TEST/MECH/OPS=可赛：rc∈{0,断言红}放行，崩溃族拒；父卡 R2 red-first 豁免配套保留）；
  G4 判据单行为测试（顶层 && 各起独立解释器/测试=硬拒；`bash -c "cd <dir> && python …"` 包裹形
     首段 cd=环境切换放行；不受生效档管=静态闸族，锁 X07 连锁修正随本规矩演进）。

跑法：bash -c "cd cc-team && python -I tests/card-type-gate-test.py" → 尾行 CARD_TYPE_GATE_GREEN n=N
修复前红基线（父卡并线后、本闸未落件时实跑）：K1 无 --type 放行、K4 BUG 挂永绿判据放行=即红
（无类型闸的世界——本锁两段红在册 note）。红自证=K12 撤闸差分对：M-G2/M-G1/M-G3 变异副本上
对应 K 形全被放行（=现树之红只归因于该闸，守卫验得红，不是空检）。
沙箱 CC_TEAM_HOME 手法同父卡 verify-dryrun-gate-test.py（import ccards 前钉 TMP；无 registry.json=
道册缺席）。CARD_TYPE_EPOCH 每场景 env 翻转（enforce=2026-11-04 / off=完全静默对照）。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

sys.dont_write_bytecode = True
for _s in (sys.stdout, sys.stderr):  # BUG-CCARDS-UTF8-1 同式：win GBK 控制台打中文断言名不碎码
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
PKG_SRC = os.path.realpath(os.path.join(HERE, "..", "ccards"))
CC_TEAM_DIR = os.path.dirname(PKG_SRC)          # 本仓 cc-team 根（包上一级）
REAL_HOME = os.environ.get("CC_TEAM_HOME")      # 正账根（沙箱覆盖前先留底，K9 只读回归用）

TMP = tempfile.mkdtemp(prefix="ctg-")
os.environ["CC_TEAM_HOME"] = TMP                # 必须在 import ccards 之前钉沙箱
sys.path.insert(0, os.path.dirname(HERE))
os.makedirs(os.path.join(TMP, "state"))
from ccards import cli, commands, engine        # noqa: E402

EVENTS = os.path.join(TMP, "state", "events.log")
ENFORCE = "2026-11-04"


def enforce(on=True):
    os.environ["CARD_TYPE_EPOCH"] = ENFORCE if on else "off"


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


def ev_lines(cid):
    if not os.path.exists(EVENTS):
        return []
    with open(EVENTS, encoding="utf-8", errors="replace") as f:
        return [ln for ln in f.read().splitlines() if ("|%s|" % cid) in ln and "TRIAL-" in ln]


def std3(extra=()):
    """三要素齐 + 通用 create 基参（G1 对照组用）。"""
    return ("--c-op", "跑卡面判据命令", "--c-in", "无外部输入（临时目录实跑）",
            "--c-expect", "尾行 CARD_TYPE_GATE_GREEN n=<总数>") + tuple(extra)


# 干净红用例形（ASCII 断言行——win 子进程 stdout 随 locale GBK 落码，中文断言串经 utf-8 解码
# 碎码=匹配恒不中的在册坑（gbkenc-prod 同族，父卡 T8 头注同款实锤）；「预期X实际Y」中文形
# 闸侧支持（_ASSERT_RES 在册），测试面取跨码点稳的 AssertionError 形。）
CLEAN_RED = 'python -I -c "raise AssertionError(\'expected GREEN got RED\')"'

oks = []


def ck(name, got, want):
    oks.append((name, got == want, "got=%r want=%r" % (got, want)))


# ── K1 无 --type 拒（enforce 档）──────────────────────────────────────────────
enforce(True)
rc, _o, err = run_cap("create", "REQ-K1", "--title", "无类型", *std3(), "--verify", 'bash -c "true"')
ck("K1 enforce 无 --type create 被拒", rc, 1)
ck("K1 归因点名缺 --type 与枚举", ("缺 --type" in err) and ("REQ/BUG/TEST/MECH/OPS" in err), True)
ck("K1 被拒卡账上零残留", show("REQ-K1")[0] != 0, True)

# ── K2 前缀与 type 不符拒（BUG-* 挂 REQ）+ 乱枚举拒 ──────────────────────────
rc, _o, err = run_cap("create", "BUG-K2", "--title", "错挂", "--type", "REQ", *std3(),
                      "--verify", 'bash -c "true"')
ck("K2 BUG- 前缀挂 --type REQ 被拒", rc, 1)
ck("K2 归因点名前缀不符", "与卡号前缀" in err, True)
rc, _o, err = run_cap("create", "REQ-K2B", "--title", "乱枚举", "--type", "FOO", *std3(),
                      "--verify", 'bash -c "true"')
ck("K2b --type 不在枚举被拒", (rc == 1) and ("不在枚举" in err), True)

# ── K3 三要素缺 expect 拒、齐了放行（并钉 type/criteria 落账）─────────────────
rc, _o, err = run_cap("create", "REQ-K3", "--title", "缺要素", "--type", "REQ",
                      "--c-op", "查询接口", "--c-in", "空参数", "--verify", 'bash -c "true"')
ck("K3 缺 --c-expect 被拒", rc, 1)
ck("K3 归因点名缺预期输出什么", "预期输出什么" in err, True)
ck("K3 被拒卡账上零残留", show("REQ-K3")[0] != 0, True)
rc, _o, _e = run_cap("create", "REQ-K3OK", "--title", "齐", "--type", "REQ", *std3(),
                     "--verify", 'bash -c "true"')
ck("K3b 三要素齐+type 对 → 放行", rc, 0)
_rc3, txt3 = show("REQ-K3OK")
ck("K3c 卡面 type/criteria 双字段落账",
   ('"type": "REQ"' in txt3) and ('"criteria"' in txt3) and ('"expect"' in txt3), True)

# ── K4 BUG 卡挂永绿判据拒（假案子）───────────────────────────────────────────
rc, _o, err = run_cap("create", "BUG-K4", "--title", "假案子", "--type", "BUG", *std3(),
                      "--verify", 'bash -c "true"')
ck("K4 BUG 卡立卡即绿被拒（假案子）", rc, 1)
ck("K4 归因含「假案子」", "假案子" in err, True)
ck("K4 events 落 G3-fake-case 痕",
   any("TRIAL-REJECT" in l and "G3-fake-case" in l for l in ev_lines("BUG-K4")), True)

# ── K5 BUG 卡挂干净红用例放行 + evidence 文件在册 + events 痕 ─────────────────
rc, _o, _e = run_cap("create", "BUG-K5", "--title", "干净红", "--type", "BUG", *std3(),
                     "--verify", CLEAN_RED)
ck("K5 BUG 卡干净红复现 → 放行", rc, 0)
_ev_rel = "docs/evidence/card-open/BUG-K5.log"
_ev_abs = os.path.join(os.path.dirname(TMP), *_ev_rel.split("/"))
ck("K5 evidence 文件在册（红的输出落 evidence）",
   os.path.isfile(_ev_abs) and ("AssertionError" in open(_ev_abs, encoding="utf-8").read()), True)
ck("K5 events 痕回写 evidence 路径",
   any("TRIAL-CLEANRED" in l and ("evidence=" + _ev_rel) in l for l in ev_lines("BUG-K5")), True)

# ── K6 BUG 卡挂 SyntaxError 崩溃红拒 + 归因文案含「先修用例」──────────────────
rc, _o, err = run_cap("create", "BUG-K6", "--title", "崩溃红", "--type", "BUG", *std3(),
                      "--verify", 'python -I -c "def ("')
ck("K6 BUG 卡崩溃形（SyntaxError）被拒", rc, 1)
ck("K6 归因含「先修用例再立卡」", ("先修用例" in err) and ("SyntaxError" in err), True)
ck("K6 被拒卡账上零残留", show("BUG-K6")[0] != 0, True)

# ── K7 REQ 卡挂断言红放行（未实现=可赛但输，不作必红要求）────────────────────
rc, _o, _e = run_cap("create", "REQ-K7", "--title", "可赛但输", "--type", "REQ", *std3(),
                     "--verify", CLEAN_RED)
ck("K7 REQ 卡干净红放行（红可、跑不动不可）", rc, 0)
ck("K7 events 落 TRIAL-LOSS 痕（不放行证据面）",
   any("TRIAL-LOSS" in l for l in ev_lines("REQ-K7")), True)
ck("K7 REQ 断言红不落 BUG evidence（证据面归 BUG 专属）",
   not os.path.exists(os.path.join(os.path.dirname(TMP), "docs", "evidence", "card-open", "REQ-K7.log")), True)

# ── K8 REQ 卡挂 rc=127 崩溃族拒 ──────────────────────────────────────────────
rc, _o, err = run_cap("create", "REQ-K8", "--title", "起不来", "--type", "REQ", *std3(),
                      "--verify", "definitely-not-a-command-k8")
ck("K8 REQ 卡 rc=127 崩溃族被拒", rc, 1)
ck("K8 归因含崩溃形", "崩溃形" in err, True)
ck("K8 被拒卡账上零残留", show("REQ-K8")[0] != 0, True)

# ── K9 存量卡读侧推导不炸（零回填；tick/只读面回归）+ 正账现读 ────────────────
ck("K9a 读侧推导七形（字段优先/前缀缺省/乱值回退）",
   tuple(commands.card_type(c) for c in (
       {"id": "REQ-A"}, {"id": "BUG-A"}, {"id": "MECH-A"}, {"id": "OPS-A"}, {"id": "TEST-A"},
       {"id": "SYS-A"}, {"id": "NODASH"}, {"id": "BUG-B", "type": "GARBAGE"},
       {"id": "REQ-C", "type": "MECH"})),
   ("REQ", "BUG", "MECH", "OPS", "TEST", "REQ", "REQ", "BUG", "MECH"))
try:
    snap = engine.tick()  # 现账（含带/不带 type 的卡）tick 只读回归=不炸
    ck("K9b 沙箱整账 tick 只读不炸", isinstance(snap, dict) and "claimable" in snap, True)
except Exception as e:  # noqa: BLE001
    ck("K9b 沙箱整账 tick 只读不炸", repr(e), "no-exception")
# 正账存量（235 卡族）现读：子进程指 REAL_HOME 只读扫描；graph 不在位（别机/裸工作树）=如实 SKIP。
K9_REAL = (
    "import sys, os\n"
    "sys.dont_write_bytecode=True\n"
    "sys.path.insert(0, os.environ['_CTG_PKG'])\n"
    "from ccards import commands, engine\n"
    "cards = dict((k, v) for k, v in commands.all_cards().items() if isinstance(v, dict))\n"
    "types = sorted(set(commands.card_type(c) for c in cards.values()))\n"
    "snap = engine.tick()\n"
    "print('K9REAL n=%d enumok=%s tickok=%s' % (len(cards), all(t in commands.CARD_TYPE_ENUM for t in types), isinstance(snap, dict)))\n"
)
real_graph = os.path.join(REAL_HOME or "", "state", "graph.json")
if REAL_HOME and os.path.isfile(real_graph):
    p9 = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", K9_REAL],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180,
                        env=dict(os.environ, CC_TEAM_HOME=REAL_HOME, CARD_TYPE_EPOCH="off",
                                 _CTG_PKG=CC_TEAM_DIR))
    t9 = (p9.stdout or b"").decode("utf-8", "replace")
    ck("K9c 正账存量卡读侧推导+tick 只读不炸",
       (p9.returncode == 0) and ("enumok=True" in t9) and ("tickok=True" in t9), True)
else:
    ck("K9c 正账不在位（别机/裸工作树）如实 SKIP", "SKIP", "SKIP")

# ── K10 顶层 && 拼案拒（G4 静态闸不受生效档管；X07 范本形）───────────────────
enforce(False)  # G4 恒开证明：warn/off 档照拒
rc, _o, err = run_cap("create", "REQ-K10", "--title", "拼案",
                      "--verify", "python -I scripts/e2e/agent-chat-smoke-test.py "
                                  "&& bash scripts/ops/secret.sh check")
ck("K10 顶层 && 各起独立解释器被拒（X07 范本形）", rc, 1)
ck("K10 归因含「一卡一行为」", "一卡一行为" in err, True)
enforce(True)

# ── K11 cd 包裹形不误伤 + 引用区 && 不误伤 ───────────────────────────────────
rc, _o, _e = run_cap("create", "REQ-K11", "--title", "包裹形", "--type", "REQ", *std3(),
                     "--files", "sub,tests/x-k11-not-landed.py",
                     "--verify", 'bash -c "cd sub && python -I tests/x-k11-not-landed.py"')
ck("K11a bash -c cd 包裹形放行（首段=环境切换）", rc, 0)
rc, _o, _e = run_cap("create", "REQ-K11B", "--title", "裸cd形", "--type", "REQ", *std3(),
                     "--files", "sub,tests/y-k11-not-landed.py",
                     "--verify", "cd sub && python -I tests/y-k11-not-landed.py")
ck("K11b 裸 cd && python 放行", rc, 0)
rc, _o, _e = run_cap("create", "REQ-K11C", "--title", "引用区内与号", "--type", "REQ", *std3(),
                     "--verify", "python -I -c \"print('a&&b' in 'x&&y')\"")
ck("K11c python -c 串内 && 不算顶层拼案（引用区感知）", rc, 0)

# ── K12 撤闸差分对：G2/G1/G3 各调用点摘除 → 对应 K 形在变异副本全放行 ─────────
MUT_SCN = os.path.join(TMP, "mut_scenario.py")
with open(MUT_SCN, "w", encoding="utf-8") as f:
    f.write(
        "import os, sys\n"
        "sys.dont_write_bytecode = True\n"
        "os.environ['CC_TEAM_HOME'] = sys.argv[2]\n"
        "os.environ['CARD_TYPE_EPOCH'] = %r\n" % ENFORCE +
        "sys.path.insert(0, os.path.abspath(sys.argv[1]))\n"
        "from ccards import cli\n"
        "def go(*a):\n"
        "    try:\n"
        "        return cli.run(list(a))\n"
        "    except SystemExit as e:\n"
        "        return e.code if isinstance(e.code, int) else 1\n"
        "S = sys.argv[3]\n"
        "std3 = ['--c-op', '跑判据', '--c-in', '无', '--c-expect', '尾行 GREEN']\n"
        "if S == 'G2':\n"  # K1/K2 形：无 --type / 前缀不符 —— 摘 G2 后按 REQ 缺省放行
        "    r1 = go('create', 'BUG-K12A', '--title', 't', *std3, '--verify', 'bash -c \"true\"')\n"
        "    r2 = go('create', 'BUG-K12B', '--title', 't', '--type', 'REQ', *std3,\n"
        "            '--verify', 'bash -c \"true\"')\n"
        "elif S == 'G1':\n"  # K3 形：缺 expect —— 摘 G1 后放行
        "    r1 = go('create', 'REQ-K12C', '--title', 't', '--type', 'REQ',\n"
        "            '--c-op', 'a', '--c-in', 'b', '--verify', 'bash -c \"true\"')\n"
        "    r2 = r1\n"
        "else:\n"  # K4 形：BUG 挂永绿 —— 摘 G3 后放行
        "    r1 = go('create', 'BUG-K12D', '--title', 't', '--type', 'BUG', *std3,\n"
        "            '--verify', 'bash -c \"true\"')\n"
        "    r2 = r1\n"
        "print('M12RC %s %s' % (r1, r2))\n"
    )


def mutate(patches, scen):
    """复制 ccards 包→逐条打 patch（锚点唯一断言）→起子进程跑场景。"""
    sb = tempfile.mkdtemp(prefix="ctg-mut-")
    team = os.path.join(sb, "team")
    os.makedirs(os.path.join(team, "state"))
    shutil.copytree(PKG_SRC, os.path.join(sb, "ccards"))
    for rel, old, new in patches:
        tgt = os.path.join(sb, "ccards", rel)
        with open(tgt, encoding="utf-8") as f:
            src = f.read()
        n = src.count(old)
        assert n == 1, "变异锚点失配 %s: 实 %d 期望 1" % (old, n)
        with open(tgt, "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(old, new))
    p = subprocess.run([sys.executable, "-I", "-X", "utf8", MUT_SCN, sb, team, scen],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240,
                       env=dict(os.environ, CARD_TYPE_EPOCH=ENFORCE))
    return (p.stdout or b"").decode("utf-8", "replace")


# G2 两处调用点（_type_open_check 与 cmd_create）同摘——单摘一处会被另一处兜住（纵深，非漏闸）
t = mutate([("commands.py", "t = _check_card_type_gate(a)  # G2-CALLSITE-TYPEGATE",
             "t = 'REQ'  # G2-CALLSITE-TYPEGATE MUT"),
            ("commands.py", 'c["type"] = _check_card_type_gate(a)',
             'c["type"] = "REQ"  # G2-CALLSITE-TYPEGATE MUT')], "G2")
ck("K12-G2 摘类型闸两处调用点：K1/K2 形全放行（=现树 K1/K2 之红只归因于 G2）",
   "M12RC 0 0" in t, True)
_src0 = open(os.path.join(PKG_SRC, "commands.py"), encoding="utf-8").read()
ck("K12-G2 前置：G2 调用点在册两处且带标记（禁孤儿闸）",
   sum(1 for ln in _src0.splitlines()
       if "_check_card_type_gate(a)" in ln and "G2-CALLSITE-TYPEGATE" in ln), 2)

# G1 调用点摘除：K3 形放行
t = mutate([("commands.py", "_check_criteria3_gate(a, c)  # G1-CALLSITE-TYPEGATE",
             "(a, c)  # G1-CALLSITE-TYPEGATE MUT")], "G1")
ck("K12-G1 摘三要素调用点：K3 形放行（=现树 K3 之红只归因于 G1）", "M12RC 0" in t, True)
# G3 调用点摘除：K4 形（BUG 永绿）放行——return 留位=父闸也不跑（enforce 分支直达 G3）
t = mutate([("commands.py", "_open(a, v)  # G3-CALLSITE-TYPEGATE",
             "pass  # G3-CALLSITE-TYPEGATE MUT")], "G3")
ck("K12-G3 摘按类型开卡调用点：K4 形放行（=现树 K4 之红只归因于 G3）", "M12RC 0" in t, True)

# ── 收尾：档位阶梯横切（warn=缺 --type 只回显并落前缀推导值；off=完全静默）────────
os.environ["CARD_TYPE_EPOCH"] = "2099-01-01"  # 界前=warn 档
rc, _o, err = run_cap("create", "SYS-K99", "--title", "哨兵形", "--verify", 'bash -c "true"')
ck("横切 warn 档缺 --type 放行+回显+前缀推导落账=REQ",
   (rc == 0) and ("未带 --type" in err) and ('"type": "REQ"' in show("SYS-K99")[1]), True)
os.environ["CARD_TYPE_EPOCH"] = "off"
rc, _o, err = run_cap("create", "SYS-K98", "--title", "逃生门", "--verify", 'bash -c "true"')
ck("横切 off 档完全静默放行（无 WARN 回显）",
   (rc == 0) and ("未带 --type" not in err), True)
enforce(True)

bad = [x for x, ok, _ in oks if not ok]
for x, ok, hint in oks:
    print("%s %s%s" % ("PASS" if ok else "FAIL", x, ("｜" + hint) if not ok else ""))
if bad:
    print("CARD_TYPE_GATE_RED fails=%d" % len(bad))
    sys.exit(1)
print("CARD_TYPE_GATE_GREEN n=%d" % len(oks))
