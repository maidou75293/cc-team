#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# recon-tailrace-test.py — BUG-RECON-TAILRACE-1 红绿锁（mergetrain 流水尾竞态自洽）。
# 病因（现账实锤 state/events.log 2026-11-03 08:38:52/53，卡 MECH-TRAIN-ADR-1）：
#   verifier 通道（cmd_verdict PASS）已把卡面转正并记 DONE->VERIFIED，1 秒后火车外合分支
#   拿**环初 DONE 快照**硬补 DONE->DONE 流水尾 → reconcile_events 判据一「每卡流水尾=卡面
#   现态」恒红（每环+1，guard DEGRADED 不许 --reset 假绿）。同族硬笔共 7 处：
#   _ext_merge_closure / stage1 passed+pending / stage2 blocked×2+refailed+merged。
# 修复（ops/mergetrain.py）：流水尾补笔唯一入口 git_ev_now——落笔前现读卡面（old 值以写闸
#   现场为准，同 cmd_verdict 进锁重载语义），old=new=现场态；已 VERIFIED 记自环保审计痕，
#   卡不存在/坏账响亮跳过（盲补无卡事件=触发幽灵判据第二种红，宁缺痕不毒尾）。
# 锁面（全沙箱：独立 CC_TEAM_HOME 测试账，正账零触碰；跑法 python -I
#   cc-team/tests/recon-tailrace-test.py；exit 0=全绿）：
#   A  静态段：火车源码零「快照硬笔 DONE->DONE」残留 + reconcile_events 判据一在位且
#      未现 mergetrain 豁免字样（本卡禁「擅自放宽校验」，方案②豁免条款只交 PM 裁不落地）。
#   S1 外合双通道绿：环初 DONE 快照进 _ext_merge_closure，do_verdict 现场转正
#      （face+events 两笔按 cmd_verdict 真实顺序模拟）→ 尾须 VERIFIED->VERIFIED 自环；
#      reconcile 对账零尾报警。
#   S2 队列①双通道绿：stage1_verify 拿 DONE 快照，卡面已 VERIFIED → passed 痕记自环。
#   S3 正常圈不失明：卡面仍 DONE → 尾 DONE->DONE，痕在、账自洽。
#   S4 无卡响亮跳过：git_ev_now 对不存在卡零落笔（防制造幽灵 ALARM）。
#   S5 任意现态自洽：卡面 OPEN（REJECT 重做竞态）→ 尾 OPEN->OPEN。
#   R1 撤针必红（守卫要验它红得起来）：git_ev_now 打回旧硬笔形 → 同世界重演 →
#      reconcile 必报「卡面=VERIFIED 但事件流水尾部=…->DONE(裸改/漏记/崩溃残渣)」恰一条；
#      复针后补一笔现读自环 = 现账修复通道演示（尾回自洽、报警消）。
import io, os, re, shutil, subprocess, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # cc-team 根
try:      # 宿主 cp936 控制台防炸（同 state_std/done-anchor 双侧形态）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, str(detail)[:300])


def ck(name, cond, detail=""):
    ok(name) if cond else bad(name, detail)


_home = tempfile.mkdtemp(prefix="cc-recon-tailrace-home-")
os.environ["CC_TEAM_HOME"] = _home                     # 测试账家：必须在 import ccards 前钉
os.environ.pop("CC_AUTO_VERIFY", None)                 # 沙箱禁自动验收（同 merge-train 纪律）
_sb_sa = os.path.join(_home, "selfaudit-stub.py")      # 判据四活体探针与本锁无关，stub 静音
with open(_sb_sa, "w", encoding="utf-8") as _f:
    _f.write("import sys\nsys.exit(0)\n")
os.environ["BOARD_SELFAUDIT_PATH"] = _sb_sa
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, "ops"))
from ccards import cli as _cli                          # noqa: E402
from ccards import graph as _graph                      # noqa: E402
from ccards import events as _ev                        # noqa: E402
import mergetrain as mt                                 # noqa: E402
import reconcile_events as RE                           # noqa: E402


def run(*args):
    """进程内走真写闸通道：返回 (rc, 合并输出)。"""
    buf = io.StringIO()
    _o, _e = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        rc = _cli.run(list(args))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    except Exception as e:
        rc = 99
        buf.write("EXC %r" % e)
    finally:
        sys.stdout, sys.stderr = _o, _e
    return rc, buf.getvalue()


def capture(fn, *a, **k):
    """收 stdout 跑 fn（读 git_ev_now 的 WARN 点名）。"""
    buf = io.StringIO()
    _o = sys.stdout
    sys.stdout = buf
    try:
        fn(*a, **k)
    finally:
        sys.stdout = _o
    return buf.getvalue()


EVENTS = os.path.join(_home, "state", "events.log")


def tail_lines(cid=""):
    if not os.path.exists(EVENTS):
        return []
    with open(EVENTS, encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f if ln.strip() and (not cid or ("|" + cid + "|") in ln)]


def ev(cid, old, new, who, extra=""):
    _ev.event(cid, old, new, who, extra)


def reset(cids_status):
    """清测试账重开世界：graph 重建+重封重算，每卡记出生笔（判据一有尾可对）。"""
    st = os.path.join(_home, "state")
    os.makedirs(st, exist_ok=True)
    for f in ("events.log", "graph.json", "alerts.json"):
        p = os.path.join(st, f)
        if os.path.exists(p):
            os.remove(p)
    for cid, status in cids_status:
        _graph.save({"id": cid, "title": "tail-race fixture", "status": status,
                     "deps": [], "owner": "m2", "lanes": ["m2"],
                     "verify": 'python -c "print(1)"'})
        rc, out = run("dag")
        assert rc == 0, out
        ev(cid, "OPEN", status, "seed", "fixture 出生")


def recon():
    """跑生产同一判据（reconcile_events.main，只读）：返回 (rc, 尾报警行[], 全输出)。"""
    buf = io.StringIO()
    _argv, _o, _e = sys.argv, sys.stdout, sys.stderr
    sys.argv = ["reconcile_events.py", "--quiet"]
    sys.stdout = sys.stderr = buf
    rc = 0
    try:
        RE.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (1 if e.code else 0)
    except Exception as exc:
        rc = 99
        buf.write("EXC %r" % exc)
    finally:
        sys.argv, sys.stdout, sys.stderr = _argv, _o, _e
    out = buf.getvalue()
    tails = [l for l in out.splitlines() if "但事件流水尾部" in l]
    return rc, tails, out


class FakeRing:
    """①环假件：交卷复验恒绿（run_argv 面），不做真验收——本锁盯的是补笔现场态。"""
    def run_argv(self, cmd, to):
        return 0, "fake verify green"

    def via_host(self):
        return "sandbox"

    def flat_tail(self, s, n=120):
        return (s or "")[-n:]

    def card_cwd(self, c):
        return None

    def looks_host_mismatch(self, rc, out):
        return False

    def do_verdict(self, cid, result, reason):
        return False


class FakeExtVerifier:
    """verifier 通道现场模拟（生产 08:38:52 那笔的等价物）：卡面翻 VERIFIED +
    events 记 DONE->VERIFIED|verifier|PASS，顺序=cmd_verdict 真实落账形。"""
    def __init__(self):
        self.calls = []

    def do_verdict(self, cid, result, reason):
        self.calls.append((cid, result))
        c = _graph.load(cid)
        c["status"] = "VERIFIED"
        _graph.save(c)
        run("dag")
        ev(cid, "DONE", "VERIFIED", "verifier", "PASS")
        return True


def fake_git_anc(is_anc_rc):
    def _g(repo, *args):
        if args and args[0] == "merge-base":
            return is_anc_rc, ""
        return 0, "e" * 40
    return _g


# ─────────────────────────── A 静态段 ───────────────────────────
def a_static():
    src = open(os.path.join(SRC, "ops", "mergetrain.py"), encoding="utf-8").read()
    ck("A1 火车零「快照硬笔 DONE->DONE」残留（7 处全改现读入口）",
       not re.search(r'git_ev\(team, cid, "DONE"', src)
       and len(re.findall(r'git_ev_now\(team, cid', src)) >= 7, "")
    ck("A2 git_ev_now 现读卡面入口在位（_face_status 走 graph 唯一访问器）",
       "def git_ev_now" in src and "def _face_status" in src
       and "_G.all_cards().get(cid)" in src, "")
    rec_src = open(os.path.join(SRC, "ops", "reconcile_events.py"), encoding="utf-8").read()
    ck("A3 判据一在位未放宽：对账器源码零 mergetrain 豁免字样（方案②只交 PM 裁，未落地）",
       'if last[cid] != st:' in rec_src and "裸改/漏记/崩溃残渣" in rec_src
       and "mergetrain" not in rec_src.lower(), "")


# ─────────────────────── S1 外合双通道绿 ───────────────────────
def s1():
    reset([("TAIL-S1", "DONE")])
    c_snap = dict(_graph.load("TAIL-S1"))                 # 环初快照（此刻还 DONE）
    rec = {"stage": "passed", "commit": "0754e3a7" * 5, "repo": "R"}
    root = os.path.join(_home, "sb-s1")
    os.makedirs(os.path.join(root, "wt"), exist_ok=True)
    tip0, ext0, cln0 = mt.tip_of, mt.sandbox_external, mt.sandbox_cleanup
    mt.tip_of = lambda repo, ref, git=None: "f" * 40
    mt.sandbox_external = lambda repo, tip, cid, git: (root, "")
    mt.sandbox_cleanup = lambda repo, path, git: None
    try:
        fv = FakeExtVerifier()
        res = mt._ext_merge_closure("TAIL-S1", rec, c_snap, "R", "main", _home,
                                    True, lambda s: None, fv, lambda repo, *a: (0, ""))
        tail = tail_lines("TAIL-S1")[-1]
        ck("S1 外合转正后补笔=VERIFIED->VERIFIED 自环（毒尾根除，审计痕保留）",
           res == "ok" and "|VERIFIED->VERIFIED|mergetrain|" in tail and "外线合入" in tail, tail)
        ck("S1b 卡面/辅账不串道：face=VERIFIED、stage=merged(外线)",
           _graph.load("TAIL-S1")["status"] == "VERIFIED"
           and rec.get("stage") == "merged" and rec.get("merge_sha") == "(外线)", rec)
        rc, tails, out = recon()
        ck("S1c reconcile 判据一零尾报警（下一环回绿条件成立）",
           not tails and rc == 0, tails or out[-300:])
    finally:
        mt.tip_of, mt.sandbox_external, mt.sandbox_cleanup = tip0, ext0, cln0


# ─────────────────────── S2 队列①双通道绿 ───────────────────────
def s2():
    reset([("TAIL-S2", "VERIFIED")])                      # verifier 通道已抢先转正
    ev("TAIL-S2", "DONE", "VERIFIED", "verifier", "PASS")
    cards = {"TAIL-S2": {"status": "DONE",                         # 环初快照仍旧=DONE
                         "commit": "0754e3a7" * 5, "repo": "R",
                         "verify": 'python -c "print(1)"', "done_at": "2026-11-03T08:00:00+0800"}}
    state = {"cards": {}, "restarts": {}}
    g = mt.stage1_verify(cards, state, _home, True, lambda s: None, FakeRing(),
                         git=fake_git_anc(1))
    tail = tail_lines("TAIL-S2")[-1]
    ck("S2 交卷即验绿补笔现读卡面：VERIFIED->VERIFIED（陈旧快照不再毒尾）",
       g["passed"] == 1 and "|VERIFIED->VERIFIED|mergetrain|stage=passed@" in tail, tail)
    rc, tails, out = recon()
    ck("S2b reconcile 无尾报警", not tails and rc == 0, tails or out[-300:])


# ─────────────────────── S3 正常圈审计痕不失明 ───────────────────────
def s3():
    reset([("TAIL-S3", "DONE")])
    cards = {"TAIL-S3": {"status": "DONE", "commit": "0754e3a7" * 5, "repo": "R",
                         "verify": 'python -c "print(1)"', "done_at": "2026-11-03T08:00:00+0800"}}
    state = {"cards": {}, "restarts": {}}
    g = mt.stage1_verify(cards, state, _home, True, lambda s: None, FakeRing(),
                         git=fake_git_anc(1))
    tail = tail_lines("TAIL-S3")[-1]
    ck("S3 卡面仍 DONE：痕照落 DONE->DONE 自环（修 bug≠销审计痕），尾=面自洽",
       g["passed"] == 1 and "|DONE->DONE|mergetrain|stage=passed@" in tail, tail)
    rc, tails, out = recon()
    ck("S3b reconcile 无尾报警", not tails and rc == 0, tails or out[-300:])


# ─────────────────────── S4 无卡响亮跳过 ───────────────────────
def has_now_entry():
    """修复未落地（git_ev_now 入口整个缺席=旧代码世界）时，各动态段干净判红不炸栈。"""
    if hasattr(mt, "git_ev_now"):
        return True
    bad("S4/S5/R1 前置：mergetrain 缺 git_ev_now 现读入口（修复未落地）", "")
    return False


def s4():
    if not has_now_entry():
        return
    reset([("TAIL-S4", "VERIFIED")])
    before = len(tail_lines())
    outw = capture(mt.git_ev_now, _home, "TAIL-GHOST", "stage=passed@deadbeef")
    ck("S4 卡面读不到=零落笔+WARN 点名（盲补=幽灵判据第二种红，宁缺痕不毒尾）",
       len(tail_lines()) == before and "不补笔" in outw, outw)
    rc, tails, out = recon()
    ck("S4b reconcile 无尾报警且无幽灵 ALARM",
       not tails and "TAIL-GHOST" not in out, tails or out[-300:])


# ─────────────────────── S5 任意现态自洽 ───────────────────────
def s5():
    if not has_now_entry():
        return
    reset([("TAIL-S5", "OPEN")])                          # REJECT 重做后火车残留圈
    capture(mt.git_ev_now, _home, "TAIL-S5", "待验收: sandbox")
    tail = tail_lines("TAIL-S5")[-1]
    rc, tails, out = recon()
    ck("S5 卡面=OPEN：尾 OPEN->OPEN 自洽（不变量与具体态无关）",
       "|OPEN->OPEN|mergetrain|" in tail and not tails and rc == 0, tail or out[-300:])


# ─────────────────────── R1 撤针必红 + 补笔修复演示 ───────────────────────
def r1():
    if not has_now_entry():
        return
    reset([("TAIL-R1", "DONE")])
    c_snap = dict(_graph.load("TAIL-R1"))
    rec = {"stage": "passed", "commit": "0754e3a7" * 5, "repo": "R"}
    root = os.path.join(_home, "sb-r1")
    os.makedirs(os.path.join(root, "wt"), exist_ok=True)
    tip0, ext0, cln0 = mt.tip_of, mt.sandbox_external, mt.sandbox_cleanup
    pin0 = mt.git_ev_now
    mt.tip_of = lambda repo, ref, git=None: "f" * 40
    mt.sandbox_external = lambda repo, tip, cid, git: (root, "")
    mt.sandbox_cleanup = lambda repo, path, git: None
    try:
        # 撤针=修复前形态：拿环初快照硬笔 DONE->DONE（生产 08:38:53 那一笔）
        mt.git_ev_now = lambda team, cid, extra: mt.git_ev(team, cid, "DONE", "DONE", extra)
        fv = FakeExtVerifier()
        res = mt._ext_merge_closure("TAIL-R1", rec, c_snap, "R", "main", _home,
                                    True, lambda s: None, fv, lambda repo, *a: (0, ""))
        rc, tails, out = recon()
        ck("R1 撤针必红：尾=DONE vs 面=VERIFIED → 判据一恰报一条（ADR-1 原形）",
           res == "ok" and len(tails) == 1 and rc == 1
           and re.search(r"ALARM: TAIL-R1 卡面=VERIFIED 但事件流水尾部=TAIL-R1->DONE\(裸改",
                         tails[0] or ""), (res, tails, out[-300:]))
        # 复针 + 现读补一笔 = 现账修复通道演示（append-only 出尾自洽，报警消）
        mt.git_ev_now = pin0
        capture(mt.git_ev_now, _home, "TAIL-R1", "流水尾修复（现账补笔机制演示）")
        rc2, tails2, out2 = recon()
        ck("R1b 复针后补笔回自洽：尾 VERIFIED->VERIFIED，reconcile 绿",
           not tails2 and rc2 == 0
           and "|VERIFIED->VERIFIED|" in tail_lines("TAIL-R1")[-1], tails2 or out2[-300:])
    finally:
        mt.git_ev_now = pin0
        mt.tip_of, mt.sandbox_external, mt.sandbox_cleanup = tip0, ext0, cln0


def main():
    a_static()
    s1()
    s2()
    s3()
    s4()
    s5()
    r1()
    shutil.rmtree(_home, ignore_errors=True)
    print("=== recon-tailrace fails=%d ===" % len(fails))
    print("TAILRACE_LOCK_PASS" if not fails else "TAILRACE_LOCK_FAIL: " + "; ".join(fails[:8]))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
