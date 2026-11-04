#!/usr/bin/env bash
# claim-counter-test.py — REQ-CLAIM-COUNTER-1「换手计数+冲突计数+看板列」红绿锁。
# 规格（业主 seq=28/29/30）：
#   R1 认领(OPEN->CLAIMED)计 claim_count、退回(CLAIMED->OPEN，含 recycle/手杀/HANDOVER 重派)
#      计 release_count——递增必在账本写锁内同笔落账（graph.json 一次 save，禁读改分离竞窗）；
#   R2 看板卡列表增「换手次数」列（认/退拆量直读），高换手（>10 且仍 CLAIMED）行亮色标记；
#   R3 存量不回填：无字段按 0 读、渲染不报错、读侧不写回造实体；
#   R5 撞车落定（blocked 痕未清 + 复验净 + 真并线/外线转正 stage=merged 落地）conflict_count+1，
#      同笔 events 机读行 MERGE-TRAIN-CONFLICT-RESOLVED card=<cid> cluster=<簇键>；
#      同次撞车多圈重试只计一次（痕清除即闭合）；从未撞车的直接并线不计数；
#   R5b verdict REJECT 计 reject_count（同一机件），第 3 拒落 REJECT-THRESHOLD 开放键；
#   R6 超阈：同卡 conflict_count>2、同簇 events 实探累计>3 → CONFLICT-THRESHOLD 键，
#      复用 fuse-S6 升级闭环（键名禁「业主/FUSE-S6」头=否则不升级静默丢报）；
#   R7 定向选案只登记不实现——本锁不含该项。
# 判据：
#   T1 claim+1（重复领不双计）；T2 release+1（回收通道收口+手杀 cmd_release 直调+HANDOVER
#      交接只计一次）；T3 两进程各锤 50 轮认领/退回并发，claim_count==100/release_count==100
#      零丢失（原子入账实证）；T4 撤闸差分对（摘 _bump 调用行→计数必冻）；T5 存量卡默认 0
#      不回填（show/list/reindex 后字段仍缺席；坏值按 0 读）；T6 看板「换手次数」列+高换手
#      亮行静态断言 + _turnover 缺省容错现跑；T7 撞车->复验净->并线落地 +1——外线转正与
#      真并线两路线全链沙箱（真 git 仓冲突->解冲突->merge），含禁双计与「未撞车不计数」；
#   T7b reject 递增+第 3 拒阈值键+撤闸冻（同一 gut 包负样）；T8 同卡 3 解/同簇 4 解 ->
#      CONFLICT-THRESHOLD 键在册且 fuse-S6 现算可升级（业主/FUSE-S6 头对照键被排除）；
#   T9 撤闸差分对 for conflict：摘 _conflict_closure 调用=计数+事件行+阈值键同冻（转正本身
#      照常），摘 _AL.record(CONFLICT-THRESHOLD)=计数照增键不落（分层撤闸，各钉各的闸）。
# 跑法: python -I tests/claim-counter-test.py   （exit 0 + 尾行 CLAIM_COUNTER_PASS = 全绿）
# 纪律：全沙箱（独立 CC_TEAM_HOME 临时账家 + temp git 仓），零碰生产 :8790、零碰真实台账；
#   撤闸样全在拷贝件上注入（cp 包/exec 补丁源），产码文件只读。双语头同 merge-train-check
#   （''''true 藏 bash 引导段，bash/python 两形通用；段内禁再现字面 '''）。
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
if [ -z "$PY" ]; then echo "FAIL: claim-counter-test 找不到 python 解释器"; exit 1; fi
exec "$PY" -I -X utf8 "$0"
'''
# -*- coding: utf-8 -*-
import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types

try:                                    # BUG-CCARDS-UTF8-1 同纪律：出侧 utf-8+replace（子样尾含非 utf8 字节时打印不炸）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CT not in sys.path:
    sys.path.insert(0, CT)
os.environ.pop("CC_AUTO_VERIFY", None)          # done 首验通道关闭——本锁验计数不验自动验收
os.environ["MERGE_TRAIN_STEP_TIMEOUT"] = "240"
os.environ["CARD_3FORM_EPOCH"] = "off"          # 三段式闸关档——立卡不带 src/story/plan 须放行

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


def state_of(home):
    return json.load(open(os.path.join(home, "state", "merge-train.json"), encoding="utf-8"))


def alerts_of(home):
    try:
        return json.load(open(os.path.join(home, "state", "alerts.json"),
                              encoding="utf-8")).get("alerts", {})
    except (OSError, ValueError):
        return {}


def alerts_keys(home):
    return list(alerts_of(home))


def _cnt(home, cid, field):
    return card(home, cid).get(field)


def fresh(home, **mt_env):
    """场景隔离：独立 CC_TEAM_HOME + 清 ccards/recycle/mergetrain 模块缓存重 import
    （路径常量全在 import 期取值，随 env 重算——一进程跑全部场景不串账）。返回 mergetrain。"""
    for m in list(sys.modules):
        if m == "ccards" or m.startswith("ccards.") or m in ("recycle", "mergetrain"):
            del sys.modules[m]
    for k, v in mt_env.items():
        os.environ[k] = str(v)
    os.environ["CC_TEAM_HOME"] = home
    ops = os.path.join(CT, "ops")
    for p in (ops, CT):
        if p not in sys.path:
            sys.path.insert(0, p)
    return importlib.import_module("mergetrain")


def cli_of():
    from ccards import cli as _cli
    return _cli


def make_repo(tag):
    """fixture 仓：main 带 cc-team/tests/{select.sh,all.sh} 桩（改动面锁沙箱真跑）+ seed.py；
    已 checkout lane。主干可被 worktree 签出后由测试自移（撞车源）。返回 (repo, mw|None)。"""
    d = os.path.join(SB, tag)
    os.makedirs(os.path.join(d, "cc-team", "tests"), exist_ok=True)
    os.makedirs(os.path.join(d, "cc-team", "ops"), exist_ok=True)
    gitc(d, "init", "-q", "-b", "main")
    gitc(d, "config", "user.email", "counter-test@local")
    gitc(d, "config", "user.name", "counter-test")
    gitc(d, "config", "core.autocrlf", "false")
    open(os.path.join(d, "cc-team", "tests", "all.sh"), "w", newline="\n").write(
        "#!/usr/bin/env bash\necho ALL_SUITES_PASS\n")
    open(os.path.join(d, "cc-team", "tests", "select.sh"), "w", newline="\n").write(
        '#!/usr/bin/env bash\n'
        'for f in "$@"; do case "$f" in cc-team/ops/*) echo "PARTIAL: bash tests/all.sh" ;; '
        '*) echo "FULL: bash tests/all.sh" ;; esac; done\n')
    open(os.path.join(d, "cc-team", "ops", "seed.py"), "w", newline="\n").write("X = 1\n")
    open(os.path.join(d, "README.md"), "w", encoding="utf-8", newline="\n").write("line\n")
    gitc(d, "add", "-A")
    gitc(d, "commit", "-qm", "seed main")
    gitc(d, "checkout", "-qb", "lane")
    return d


def lane_commit(d, files, msg="lane card work"):
    for name, body in files.items():
        p = os.path.join(d, *name.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8", newline="\n").write(body)
    gitc(d, "add", "-A")
    gitc(d, "commit", "-qm", msg)
    return gitc(d, "rev-parse", "HEAD")[1]


def mk_open(home, cid, repo=None, lanes="w1"):
    _cli = cli_of()
    # REQ-VERIFY-DRYRUN-1 适配（2026-11-04 PM 合流轮）：发卡三口现挂试跑闸，判据在一次性
    # tmp 目录实跑——旧形 test -f marker.txt 在该目录必 rc=1 且无缺件报文（R2 无可豁免串）=
    # 卡建不出来。本测试考的是 claim/换手计数账不是判据内容，换自过形（同 dryrun 锁 T6 对照）。
    args = ["create", cid, "--title", "计数沙箱卡 %s" % cid, "--lanes", lanes,
            "--verify", "bash -c true"]
    if repo:
        args += ["--repo", repo]
    _cli.run(args)


def cut(src, marker, repl=None, want=1):
    """撤闸差分注入（bind-miss C8 同式）：marker 先验在场（缺席=锁腐硬败），
    命中行整行换成 pass/替换行——摘闸=等价删调用。want=期望命中数。"""
    lines = src.splitlines(keepends=True)
    idxs = [i for i, l in enumerate(lines) if marker in l]
    if len(idxs) != want:
        raise AssertionError("撤点 marker 命中 %d 处≠%d: %r" % (len(idxs), want, marker))
    repl = repl or "pass  # 撤闸（差分注入）\n"
    for i in idxs:
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        lines[i] = indent + repl
    return "".join(lines)


SB = tempfile.mkdtemp(prefix="cc-claim-counter-lock-")
os.environ["MERGE_TRAIN_TMPDIR"] = SB
# 锁的宿主自己先钉对（MECH-TRAIN-ADR-1 同式，逐字取 merge-train-check 先例）：显式钉沿用；
# 无显式钉且 which 落 SystemRoot（WSL 桥毒宿主）=不钉，交其 Git Bash 候选自解析。
_exp = os.environ.get("CARD_VERIFY_BASH")
_w = _exp or shutil.which("bash") or "bash"
if _exp is None and os.name == "nt" and _w.lower().startswith(
        os.environ.get("SystemRoot", "C:\\Windows").lower()):
    pass
else:
    os.environ["CARD_VERIFY_BASH"] = _w


# ============ T1 认领计数：claim=+1；领不上的重复 attempt 不双计 ============
t = os.path.join(SB, "t1")
fresh(t)
mk_open(t, "CT-T1", lanes="ANY")
cli_of().run(["claim", "CT-T1", "--owner", "w1"])
ck("T1a claim(OPEN->CLAIMED) claim_count=1", _cnt(t, "CT-T1", "claim_count") == 1,
   "卡面=%s" % card(t, "CT-T1").get("claim_count"))
try:
    cli_of().run(["claim", "CT-T1", "--owner", "w2"])
    _t1_dead = False
except SystemExit:
    _t1_dead = True
ck("T1b CLAIMED 上重复领拒且不加计（判据先于计数）",
   _t1_dead and _cnt(t, "CT-T1", "claim_count") == 1)

# ============ T2 退回计数：唯一闸收口；手杀/HANDOVER 只计一次 ============
t = os.path.join(SB, "t2")
repo2 = make_repo("t2-repo")
fresh(t)
cli = cli_of()
mk_open(t, "CT-T2", repo=repo2, lanes="ANY")
open(os.path.join(repo2, "ho-note.txt"), "w", encoding="utf-8").write("前任脏件\n")  # 交接素材
cli.run(["claim", "CT-T2", "--owner", "w1"])
cli.run(["release", "CT-T2", "--who", "recycle"])   # ①recycle/fuse/dispatch 全走本 subcommand
ck("T2a release(CLAIMED->OPEN) release_count=1", _cnt(t, "CT-T2", "release_count") == 1)
_evs = ev_lines(t)
ck("T2b HANDOVER-OUT 随交接落痕且只计一次（release_count 不双计）",
   any("HANDOVER-OUT" in l for l in _evs) and _cnt(t, "CT-T2", "release_count") == 1)
cli.run(["claim", "CT-T2", "--owner", "w2"])        # 重派（HANDOVER-IN 消费交接）
ck("T2c 重派 claim_count=2 且 HANDOVER-IN 在案",
   _cnt(t, "CT-T2", "claim_count") == 2 and any("HANDOVER-IN" in l for l in ev_lines(t)))
from ccards import commands as _CMD
_CMD.cmd_release(argparse.Namespace(id="CT-T2", who="lane-steal"))  # ②手杀=locks.py 同一直调形
ck("T2d 手杀通道（cmd_release 直调=lane-steal 收口）release_count=2",
   _cnt(t, "CT-T2", "release_count") == 2)
for _f, _mark in (("ops/recycle.py", '_cli.run(["release", cid, "--who", "recycle"]'),
                   ("ccards/locks.py", 'cmd_release(argparse.Namespace('),
                   ("ops/dispatch.py", 'run_gate("release"'),
                   ("ops/fuse.py", 'gate(cfg, "release", cid')):
    _src = open(os.path.join(CT, *_f.split("/")), encoding="utf-8").read()
    ck("T2e %s 回收全部收口 release 唯一闸（源钉）" % _f, _mark in _src)

# ============ T3 并发原子：两进程各锤 50 轮认领/退回，零丢失 ============
t = os.path.join(SB, "t3")
fresh(t)
mk_open(t, "CT-T3", lanes="ANY")
HAMMER = os.path.join(SB, "hammer.py")
open(HAMMER, "w", encoding="utf-8").write(
    "import os, sys, time, random\n"
    "CT, HOME, WHO, N = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])\n"
    "sys.path.insert(0, CT)\n"
    "os.environ['CC_TEAM_HOME'] = HOME\n"
    "from ccards import cli as _cli\n"
    "def spin(args):\n"
    "    t0 = time.time(); tries = 0\n"
    "    while True:\n"
    "        try:\n"
    "            _cli.run(args)\n"
    "            return\n"
    "        except SystemExit:\n"
    "            tries += 1\n"
    "            if time.time() - t0 > 200:\n"
    "                sys.exit(2)\n"
    "            nap = 0.003 + random.random() * 0.015\n"
    "            if tries > 30:\n"
    "                nap += 0.05 + random.random() * 0.1\n"
    "            time.sleep(nap)\n"
    "for _ in range(N):\n"
    "    spin(['claim', 'CT-T3', '--owner', WHO])\n"
    "    time.sleep(random.random() * 0.05)\n"
    "    spin(['release', 'CT-T3', '--who', WHO])\n"
    "    time.sleep(random.random() * 0.05)\n"
    "print('HAMMER_DONE', WHO, N)\n")
_henv = dict(os.environ)
_henv["PYTHONIOENCODING"] = "utf-8:replace"   # 子样 die 中文写管道不炸宿主 cp936（同出侧纪律）
_kids = [subprocess.Popen([sys.executable, "-I", HAMMER, CT, t, who, "50"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_henv)
         for who in ("hA", "hB")]
_rcs = []
for k in _kids:
    out, _ = k.communicate(timeout=300)
    _rcs.append((k.returncode, (out or b"").decode("utf-8", "replace"))[-2:])
ck("T3a 两并发子进程正常收工（rc=0）", all(rc == 0 for rc, _o in _rcs),
   str([(rc, o[-80:]) for rc, o in _rcs]))
ck("T3b 并发 50+50 认领零丢失 claim_count==100（写锁内同笔递增）",
   _cnt(t, "CT-T3", "claim_count") == 100, "实数=%s" % _cnt(t, "CT-T3", "claim_count"))
ck("T3c 并发 50+50 退回零丢失 release_count==100",
   _cnt(t, "CT-T3", "release_count") == 100, "实数=%s" % _cnt(t, "CT-T3", "release_count"))

# ============ T5 存量不回填：无字段按 0 读、读侧不造实体、坏值不炸 ============
t = os.path.join(SB, "t5")
fresh(t)
mk_open(t, "CT-T5", lanes="ANY")
_c0 = card(t, "CT-T5")
ck("T5a 立卡即无计数字段（不回填=创建面零写）",
   all(k not in _c0 for k in ("claim_count", "release_count", "conflict_count", "reject_count")))
cli = cli_of()
cli.run(["show", "CT-T5"])
cli.run(["list"])
cli.run(["reindex"])
_c1 = card(t, "CT-T5")
ck("T5b show/list/reindex 现读不报错且零写回（字段仍缺席）",
   all(k not in _c1 for k in ("claim_count", "release_count", "conflict_count", "reject_count")))
ck("T5c 读侧缺省/坏值按 0（_as_count：None/'x'/-3→0）",
   _CMD._as_count(None) == 0 and _CMD._as_count("x") == 0 and _CMD._as_count(-3) == 0)
cli.run(["claim", "CT-T5", "--owner", "w1"])        # 旧卡开新账=0+1，不是 0 也不是报错
ck("T5d 存量卡首领 claim_count=1（缺省读 0 递增非造 2）", _cnt(t, "CT-T5", "claim_count") == 1)

# ============ T6 看板：换手列+高换手亮行（静态断言）+ _turnover 现跑容错 ============
sw_src = open(os.path.join(CT, "board", "status-web.py"), encoding="utf-8").read()
ck("T6a /cards 表头含「换手次数」列（R2 业主可直读）", "<th>换手次数</th>" in sw_src)
ck("T6b 高换手行条件=合计>10 且仍 CLAIMED（两条件同式）",
   'hi = tot > 10 and str(c.get("status", "")) == "CLAIMED"' in sw_src
   and 'class="hi"' in sw_src and ".hi{background:" in sw_src)
ck("T6c 拆量可读（认/退分列括注）", "（认%d/退%d）" in sw_src)
_m = re.search(r"(def _turnover\(c\):.*?)(?=\ndef )", sw_src, re.S)
ck("T6d _turnover 取段成功（现跑靶面在场）", _m is not None)
if _m:
    _ns = {}
    exec(compile(_m.group(1), "_turnover#gut", "exec"), _ns)
    _tv = _ns["_turnover"]
    ck("T6e _turnover 存量缺字段/坏值=0（渲染 0 不报错，R2/R3 交叉）",
       _tv({}) == (0, 0, 0) and _tv({"claim_count": "x"}) == (0, 0, 0)
       and _tv(None) == (0, 0, 0))
    ck("T6f _turnover 合计=认+退（拆量直读口径）", _tv({"claim_count": 3, "release_count": 9}) == (3, 9, 12))

# ============ T7 撞车落定计数：外线转正/真并线两路线全链 + 禁双计 + 未撞车不计数 ============
# 路线甲（R5 原文「外线转正」）：撞车 blocked -> PM 手外合进主干 -> 圈扫转正 stage=merged(外线)
t = os.path.join(SB, "t7a")
repo = make_repo("t7a-repo")
mw = os.path.join(SB, "t7a-mw")
gitc(repo, "worktree", "add", "-q", mw, "main")
shaA = lane_commit(repo, {"README.md": "lane A side\n", "marker.txt": "a\n"})
open(os.path.join(mw, "README.md"), "w", encoding="utf-8", newline="\n").write("trunk side\n")
gitc(mw, "add", "-A")
gitc(mw, "commit", "-qm", "trunk edits README")           # 两侧同改=撞车源
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
cli = cli_of()
mk_open(t, "CT-T7A", repo=repo)
cli.run(["claim", "CT-T7A", "--owner", "w1"])
cli.run(["done", "CT-T7A", "--owner", "w1", "--commit", shaA, "--repo", repo])
mt.run_ring(team=t, apply=True, log=lambda s: print("   t7a-r1| " + str(s)[:150]))
_rec = state_of(t)["cards"].get("CT-T7A", {})
ck("T7甲① 前置如设计：撞车卡 blocked、痕（integrations/conflict_files）在册、计数未动",
   _rec.get("stage") == "blocked" and _rec.get("integrations") == "blocked"
   and "README.md" in str(_rec.get("conflict_files"))
   and _cnt(t, "CT-T7A", "conflict_count") is None,
   "rec=%s" % {k: _rec.get(k) for k in ("stage", "integrations", "conflict_files")})
rc, _ = gitc(mw, "merge", "--no-ff", "--no-edit", "-m", "PM ext merge lane", shaA, chk=False)
if rc != 0:                                               # README 撞：PM 人工裁决落定
    open(os.path.join(mw, "README.md"), "w", encoding="utf-8", newline="\n").write("PM resolved\n")
    gitc(mw, "add", "-A")
    rc, out = gitc(mw, "commit", "--no-edit", "-m", "PM ext merge lane (resolved)", chk=False)
    assert rc == 0, "T7甲前置失败：外合未落地 %s" % out[-200:]
mt.run_ring(team=t, apply=True, log=lambda s: print("   t7a-r2| " + str(s)[:150]))
_ck = _cnt(t, "CT-T7A", "conflict_count")
ck("T7甲② 外线转正落地=解冲突落定：conflict_count 0->1 + stage=merged(外线) + VERIFIED",
   _ck == 1 and state_of(t)["cards"].get("CT-T7A", {}).get("stage") == "merged"
   and card(t, "CT-T7A").get("status") == "VERIFIED",
   "count=%s 卡面=%s" % (_ck, card(t, "CT-T7A").get("status")))
_ev7 = [l for l in ev_lines(t) if "MERGE-TRAIN-CONFLICT-RESOLVED" in l]
ck("T7甲③ events 机读行 MERGE-TRAIN-CONFLICT-RESOLVED card= cluster=README.md（R5 原文形）",
   len(_ev7) == 1 and "card=CT-T7A" in _ev7[0] and _ev7[0].split("cluster=", 1)[1].strip() == "README.md",
   str(_ev7))
ck("T7甲④ 痕清除即闭合（integrations/blocked*/conflict_seen 退场=禁双计实体）",
   all(k not in state_of(t)["cards"].get("CT-T7A", {})
       for k in ("integrations", "blocked_at", "blocked_ts", "conflict_seen")))
mt.run_ring(team=t, apply=True, log=lambda s: print("   t7a-r3| " + str(s)[:150]))
ck("T7甲⑤ 多跑一圈不双计（同次撞车只计一次；merged 后再无落定时刻）",
   _cnt(t, "CT-T7A", "conflict_count") == 1
   and sum(1 for l in ev_lines(t) if "MERGE-TRAIN-CONFLICT-RESOLVED" in l) == 1)

# 路线乙（R5 原文「真并线」）：撞车 blocked -> 泳道解冲突重交（新 commit）-> 复验净 -> CAS/merge 并线
t = os.path.join(SB, "t7b")
repo = make_repo("t7b-repo")
mw = os.path.join(SB, "t7b-mw")
gitc(repo, "worktree", "add", "-q", mw, "main")
sha1 = lane_commit(repo, {"README.md": "lane side\n", "marker.txt": "b\n"})
open(os.path.join(mw, "README.md"), "w", encoding="utf-8", newline="\n").write("trunk edit\n")
gitc(mw, "add", "-A")
gitc(mw, "commit", "-qm", "trunk moves README")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
cli = cli_of()
mk_open(t, "CT-T7B", repo=repo)
cli.run(["claim", "CT-T7B", "--owner", "w1"])
cli.run(["done", "CT-T7B", "--owner", "w1", "--commit", sha1, "--repo", repo])
mt.run_ring(team=t, apply=True, log=lambda s: print("   t7b-r1| " + str(s)[:150]))
ck("T7乙① 前置：blocked 在案", state_of(t)["cards"].get("CT-T7B", {}).get("stage") == "blocked")
cli.run(["verdict", "CT-T7B", "REJECT", "--reason", "撞车打回解冲突重交"])   # 打回->重做真流程
sha2 = lane_commit(repo, {"README.md": "trunk edit\n"}, "resolved: README=trunk")  # 解冲突=与主干同文
cli.run(["claim", "CT-T7B", "--owner", "w1"])
cli.run(["done", "CT-T7B", "--owner", "w1", "--commit", sha2, "--repo", repo])
mt.run_ring(team=t, apply=True, log=lambda s: print("   t7b-r2| " + str(s)[:150]))
ck("T7乙② 换提交重交痕随交接携带->干跑净真并线落地 conflict_count=1（conflict_seen 通道）",
   _cnt(t, "CT-T7B", "conflict_count") == 1
   and state_of(t)["cards"].get("CT-T7B", {}).get("stage") == "merged"
   and card(t, "CT-T7B").get("status") == "VERIFIED",
   "count=%s 卡面=%s" % (_cnt(t, "CT-T7B", "conflict_count"), card(t, "CT-T7B").get("status")))
ck("T7乙③ 真并线路 events 同款机读行在册",
   sum(1 for l in ev_lines(t) if "MERGE-TRAIN-CONFLICT-RESOLVED card=CT-T7B" in l) == 1)

# 对照组（R5 原文「从未撞车的直接并线不计数」）
t = os.path.join(SB, "t7c")
repo = make_repo("t7c-repo")
sha = lane_commit(repo, {"marker.txt": "clean\n"})
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
cli = cli_of()
mk_open(t, "CT-T7C", repo=repo)
cli.run(["claim", "CT-T7C", "--owner", "w1"])
cli.run(["done", "CT-T7C", "--owner", "w1", "--commit", sha, "--repo", repo])
mt.run_ring(team=t, apply=True, log=lambda s: print("   t7c-r1| " + str(s)[:150]))
ck("T7丁 无冲突直接并线：merged+VERIFIED 但 conflict_count 不落（零虚计）",
   card(t, "CT-T7C").get("status") == "VERIFIED" and _cnt(t, "CT-T7C", "conflict_count") is None
   and not any("CONFLICT-RESOLVED" in l for l in ev_lines(t)))

# ============ T7b reject 计数 + 第 3 拒阈值键（R5b） ============
t = os.path.join(SB, "t7r")
repo = make_repo("t7r-repo")
sha = lane_commit(repo, {"marker.txt": "r\n"})
fresh(t, MERGE_TRAIN_STALE_MIN=60)
cli = cli_of()
mk_open(t, "CT-T7R", repo=repo)
for _i in range(3):
    cli.run(["claim", "CT-T7R", "--owner", "w1"])
    cli.run(["done", "CT-T7R", "--owner", "w1", "--commit", sha, "--repo", repo])
    cli.run(["verdict", "CT-T7R", "REJECT", "--reason", "打回样本 %d" % _i])
ck("T7b① REJECT×3 reject_count==3（与 R1 同一机件；PASS 通道不涉）",
   _cnt(t, "CT-T7R", "reject_count") == 3, "实数=%s" % _cnt(t, "CT-T7R", "reject_count"))
_keys = alerts_keys(t)
ck("T7b② 第 3 拒落开放键 REJECT-THRESHOLD card=... reject_count=3>2（R5b 阈值键）",
   any(k.startswith("REJECT-THRESHOLD card=CT-T7R") and "reject_count=3>2" in k for k in _keys),
   str(_keys))
ck("T7b③ 前两拒不触键（第 3 拒起，比较符 >2 严格）",
   sum(1 for k in _keys if "REJECT-THRESHOLD" in k) == 1)

# ============ T8 同卡 3 解/同簇 4 解 -> CONFLICT-THRESHOLD 键 + fuse-S6 可升级（R6） ============
t = os.path.join(SB, "t8")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
cli = cli_of()
mk_open(t, "CT-T8", lanes="ANY")
mk_open(t, "CT-T8B", lanes="ANY")
_logs = []
_logf = lambda s: _logs.append(str(s))


def _closure(cid):
    rec = {"stage": "passed", "integrations": "blocked", "blocked_at": "x",
           "blocked_ts": time.time(), "conflict_seen": True, "conflict_files": "README.md"}
    return mt._conflict_closure(rec, cid, t, _logf)


_ns = [_closure("CT-T8") for _ in range(3)]
_closure("CT-T8B")                                    # 第 4 笔同簇（跨卡）
ck("T8① 同卡 3 解计数 1/2/3（>阈恰在第 3）", _ns == [1, 2, 3], str(_ns))
_keys = alerts_keys(t)
ck("T8② 卡阈键 CONFLICT-THRESHOLD card=... conflict_count=3>2 开放在册（C_CARD=2）",
   "CONFLICT-THRESHOLD card=CT-T8 conflict_count=3>2" in _keys, str(_keys))
ck("T8③ 簇阈键 CONFLICT-THRESHOLD cluster=README.md total=4>3 开放在册（events 实探跨卡）",
   "CONFLICT-THRESHOLD cluster=README.md total=4>3" in _keys, str(_keys))
ck("T8④ 簇计数=events.log 实探 4 笔（唯一 append-only 账，辅账改写不骗阈值）",
   mt._cluster_total(t, "README.md") == 4)
ck("T8⑤ 键名非「业主/FUSE-S6」头（S6 排除规则在场=不升级静默丢报防线）",
   all(not k.startswith("业主") and not k.startswith("FUSE-S6")
       for k in _keys if "THRESHOLD" in k))
# fuse-S6 现算：把本沙箱报警账喂进 fuse 自己的 seg_s6（act/trace 桩化，零真唤醒）
fuse_src = open(os.path.join(CT, "ops", "fuse.py"), encoding="utf-8").read()
_fmod = types.ModuleType("fuse_under_test")
_fmod.__file__ = os.path.join(CT, "ops", "fuse.py")
sys.path.insert(0, os.path.join(CT, "ops"))
exec(compile(fuse_src, "fuse.py#sbx", "exec"), _fmod.__dict__)
_calls, _tlog = [], []
_fmod.trace = lambda cfg, m: _tlog.append(str(m))
_fmod.act = lambda cfg, seg, fn, *a, **k: (_calls.append(a), "OK")[1]
from ccards import alerts as _AL
_AL.record("业主 测试对照键（前缀排除组）")
_AL.record("FUSE-S6 测试对照键（前缀排除组）")
_cfg = {"home": t, "state": {}, "alert_ack1": -1, "alert_ack2": 60}   # 阈=-1s：现役键必判 WAKE
_fmod.seg_s6(_cfg)
_woken = " ".join(str(a) for a in _calls)
ck("T8⑥ fuse-S6 现算可升级：两阈值键恰全进 WAKE 通道（一级唤 PM 班，零多余零漏升）",
   len(_calls) == 2 and "CONFLICT-THRESHOLD card=CT-T8" in _woken
   and "CONFLICT-THRESHOLD cluster=README.md" in _woken, _woken[:200])
ck("T8⑦ 自触发前缀排除组照旧不升级（业主/FUSE-S6 头被 fuse 滤掉）",
   "业主 测试对照键" not in _woken and "FUSE-S6 测试对照键" not in _woken)

# ============ T4/T7b撤 撤闸差分对：claim/release/reject 计数摘调用必冻（子进程跑 gut 包） ============
gut4 = os.path.join(SB, "gut4")
shutil.copytree(os.path.join(CT, "ccards"), os.path.join(gut4, "ccards"),
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
shutil.copy2(os.path.join(CT, "cards_dag.py"), os.path.join(gut4, "cards_dag.py"))
_p4 = os.path.join(gut4, "ccards", "commands.py")
_src4 = open(_p4, encoding="utf-8").read()
for _mk, _rp in (('_bump(c, "claim_count")', None),
                 ('_bump(c, "release_count")', None),
                 ('_bump(c, "reject_count")', "rn = 0  # 撤闸（差分注入）\n"),
                 ('alerts.record("REJECT-THRESHOLD', None)):
    _src4 = cut(_src4, _mk, _rp)
open(_p4, "w", encoding="utf-8").write(_src4)


def run_gut(args, home, exe=os.path.join(gut4, "cards_dag.py")):
    env = dict(os.environ)
    env["CC_TEAM_HOME"] = home
    r = subprocess.run([sys.executable, "-I", exe] + args,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=180, env=env)
    return r.returncode, (r.stdout or b"").decode("utf-8", "replace")


t = os.path.join(SB, "t4")
repo = make_repo("t4-repo")
sha = lane_commit(repo, {"marker.txt": "g\n"})
rc, out = run_gut(["create", "CT-T4", "--title", "撤闸样", "--lanes", "ANY",
                   "--verify", "bash -c true", "--repo", repo], t)
ck("T4z gut 包立卡成功（撤闸样环境自证）", rc == 0, out[-120:])
run_gut(["claim", "CT-T4", "--owner", "w1"], t)
run_gut(["release", "CT-T4", "--who", "x"], t)
run_gut(["claim", "CT-T4", "--owner", "w1"], t)
run_gut(["done", "CT-T4", "--owner", "w1", "--commit", sha, "--repo", repo], t)
rc, out = run_gut(["verdict", "CT-T4", "REJECT", "--reason", "撤闸样"], t)
_ck4 = card(t, "CT-T4")
ck("T4a 撤 _bump(claim) 实跑=claim_count 冻在缺席（迁移照常走）",
   "claim_count" not in _ck4 and _ck4.get("status") == "OPEN", str(_ck4.get("status")))
ck("T4b 撤 _bump(release) 实跑=release_count 冻（T2 的绿全凭那行调用）",
   "release_count" not in _ck4)
ck("T7b撤 撤 _bump(reject)+阈值键=reject_count 冻且零键（第 3 拒不响）",
   "reject_count" not in _ck4
   and not any("REJECT-THRESHOLD" in k for k in alerts_keys(t)), out[-120:])

# ============ T9 撤闸差分对 for conflict（mergetrain 两处分层撤闸） ============
_mt_src = open(os.path.join(CT, "ops", "mergetrain.py"), encoding="utf-8").read()


def load_mt_gut(src_text, tag):
    mod = types.ModuleType("mergetrain_gut_" + tag)
    mod.__file__ = os.path.join(CT, "ops", "mergetrain.py")
    exec(compile(src_text, "mergetrain.py#gut_" + tag, "exec"), mod.__dict__)
    mod.RESIDENT_UNITS = []
    return mod


# 撤甲：摘两处 _conflict_closure 调用 = 计数+事件行+阈值键同冻（转正本身必须照常=差分对照）
gut_mt = cut(_mt_src, "_conflict_closure(rec, cid, team, log)  #", want=2)
t = os.path.join(SB, "t9a")
repo = make_repo("t9a-repo")
mw = os.path.join(SB, "t9a-mw")
gitc(repo, "worktree", "add", "-q", mw, "main")
shaA = lane_commit(repo, {"README.md": "lane A side\n", "marker.txt": "a\n"})
open(os.path.join(mw, "README.md"), "w", encoding="utf-8", newline="\n").write("trunk side\n")
gitc(mw, "add", "-A")
gitc(mw, "commit", "-qm", "trunk edits README")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
import recycle as _R9
_cli9 = cli_of()
mk_open(t, "CT-T9A", repo=repo)
_cli9.run(["claim", "CT-T9A", "--owner", "w1"])
_cli9.run(["done", "CT-T9A", "--owner", "w1", "--commit", shaA, "--repo", repo])
_g = load_mt_gut(gut_mt, "a")
_g.run_ring(team=t, apply=True, log=lambda s: print("   t9-r1| " + str(s)[:150]), recycle_mod=_R9)
rc, _ = gitc(mw, "merge", "--no-ff", "--no-edit", "-m", "PM ext merge lane", shaA, chk=False)
if rc != 0:
    open(os.path.join(mw, "README.md"), "w", encoding="utf-8", newline="\n").write("PM resolved\n")
    gitc(mw, "add", "-A")
    rc, out = gitc(mw, "commit", "--no-edit", "-m", "PM ext merge lane (resolved)", chk=False)
    assert rc == 0, "T9甲前置失败 %s" % out[-200:]
_g.run_ring(team=t, apply=True, log=lambda s: print("   t9-r2| " + str(s)[:150]), recycle_mod=_R9)
ck("T9甲① 撤闸样对照面成立：外线转正照常（merged(外线)+VERIFIED）——不是全链崩",
   card(t, "CT-T9A").get("status") == "VERIFIED"
   and state_of(t)["cards"].get("CT-T9A", {}).get("stage") == "merged")
ck("T9甲② 撤 _conflict_closure 调用=conflict_count 冻零+零事件行（T7 的绿全凭这两笔调用）",
   _cnt(t, "CT-T9A", "conflict_count") is None
   and not any("CONFLICT-RESOLVED" in l for l in ev_lines(t)))

# 撤乙：只摘 _AL.record(CONFLICT-THRESHOLD 两行 = 计数照增、阈值键不落（分层撤闸）
gut_mt2 = cut(_mt_src, '_AL.record("CONFLICT-THRESHOLD card=')
gut_mt2 = cut(gut_mt2, '_AL.record("CONFLICT-THRESHOLD cluster=')
t = os.path.join(SB, "t9b")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
_g2 = load_mt_gut(gut_mt2, "b")
from ccards import alerts as _AL2
_cli9b = cli_of()
mk_open(t, "CT-T9B", lanes="ANY")
_n2 = [_g2._conflict_closure({"stage": "passed", "integrations": "blocked",
                              "conflict_files": "README.md"}, "CT-T9B", t, _logf)
       for _ in range(3)]
ck("T9乙 撤阈值键调用：计数 1/2/3 照增、事件行照落、CONFLICT-THRESHOLD 零落键（各闸各钉）",
   _n2 == [1, 2, 3]
   and sum(1 for l in ev_lines(t) if "CONFLICT-RESOLVED card=CT-T9B" in l) == 3
   and not any("CONFLICT-THRESHOLD" in k for k in alerts_keys(t)))

# ============ 收账 ============
if FAIL:
    print("CLAIM_COUNTER_FAIL fails=%d %s" % (len(FAIL), ";".join(FAIL)))
    sys.exit(1)
shutil.rmtree(SB, ignore_errors=True)
print("CLAIM_COUNTER_PASS cases=%d" % len(OK_))
sys.exit(0)
