#!/usr/bin/env python3
# train-reverify-determinism-test.py — BUG-TRAIN-REVERIFY-FLIP-1 红绿锁（差分对，撤闸必红）。
# 病根三笔（证据文 cc-team/docs/train-reverify-flip-20261105.md）：
#   ①队列复验红因不可点名：all.sh 点名红行在头部，旧 _tail(160) 只留尾=PASS 噪声，
#     00:36/00:54 两笔 refailed 同事件「fails=1 skips=11」查无成员名（修复=_fail_digest）。
#   ②同输入每圈重掷骰子：stage2 队列复验输入=(卡 commit, 主干 tip0) 未变时结果确定
#     （E1/E2 隔离串行双跑同红同签名实证），旧式每圈全量重跑=白烧 ~7min+每圈一笔同文案
#     事件，与 ①面 passed 同词面交错=「红绿翻转」观感放大器（修复=_reval_hold 复用维持）。
#   ③轮转/done-sweep/巡逻验证宿主走 run_argv **继承面**，判据内含套件时落默认 /tmp 固定名
#     沙箱与泳道 worker/火车他圈并跑互踩（E3 共根并发实锤 fails=54~75 污染风暴；修复=
#     recycle 线程局部 rotate_isolation/rotation_root 窗 + mergetrain.suite_env 单入口）。
# 手法（撤闸必红的差分对，全沙箱零真账零真套件）：
#   T0 静态闸：单入口（套件族八键只许在 mergetrain.suite_env 定义一处出现；recycle 禁散点
#      字面量）+ run_argv 签名/base 形态引用 + 四个闸位各在其位。
#   T1 _reval_hold 纯函数四态。
#   T2 stage2 双圈假件驱动：首圈判红落账+事件；二圈同输入零重跑零事件（REVAL-HOLD 行在账）；
#      三圈主干推进必重跑；撤针（hold 闸改 `if False:`）副本二圈必重跑=锁咬得住。
#   T3 共根并发场景（卡判据③原文要求）：沙箱内两线程共用一个「默认根」交错 rm/write=
#      必互踩（红现形）；各经 rotation_root 窗=必互不侵犯（绿）；撤窗面=_rot_env 窗外 None=
#      子进程落继承面（共根前提）。
#   T4 红因点名：合成 all.sh 原文（FAIL 头+40 行 PASS 噪声尾）过 scope_lock_step——
#      detail 必含成员名；撤针副本（_fail_digest→_tail）必丢名。
# 跑法: python -I cc-team/tests/train-reverify-determinism-test.py   exit 0=末行 TRAIN_REVERIFY_DET_PASS
import importlib.util, io, json, os, re, shutil, subprocess, sys, tempfile, threading, time

try:  # 本锁自身出口同钉（GBK 宿主裸 print 会重现症状——dogfood，与 hostpin 同纪律）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # cc-team 根
if CT not in sys.path:
    sys.path.insert(0, CT)
NO_WIN = 0x08000000 if os.name == "nt" else 0
fails = []


def ok(tag, extra=""):
    print("PASS:", tag, extra)


def bad(tag, *ctx):
    fails.append(tag)
    print("FAIL:", tag, *[str(c)[:220] for c in ctx])


def spec_load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def src_slice(src, marker):
    i = src.index("def " + marker)
    j = src.find("\ndef ", i + 1)
    return src[i:j if j > 0 else len(src)]


FAKE_ROOTS = []


def _newroot():
    d = tempfile.mkdtemp(prefix="det-fakeroot-")
    os.makedirs(os.path.join(d, "wt"))     # stage2 真 chdir(root/wt)——桩根必须真实在场
    FAKE_ROOTS.append(d)
    return d


MT_PATH = os.path.join(CT, "ops", "mergetrain.py")
RC_PATH = os.path.join(CT, "ops", "recycle.py")
mt_src = io.open(MT_PATH, encoding="utf-8").read()
rc_src = io.open(RC_PATH, encoding="utf-8").read()

# ══ T0 静态闸：单入口 + 四闸位在位 ══
SUITE_KEYS = ("CC_ADR_HOME", "CC_ADR_REPO", "CC_TEST_HOME", "CC_TEST_REPO",
              "CC_ST_TMP", "CC_RECGUARD_HOME", "CC_RECGUARD_REPO", "CCSUITE_DIR")
se_body = src_slice(mt_src, "suite_env")
stray = []
for k in SUITE_KEYS:
    pat = '"%s":' % k
    n_mt = mt_src.count(pat) - se_body.count(pat)
    if mt_src.count(pat) != 1 or n_mt != 0:
        stray.append("%s×%s" % (k, mt_src.count(pat)))
    if ('"%s":' % k) in rc_src:
        stray.append("rc:%s" % k)
if stray:
    bad("T0a 套件族键散点回潮（单入口=mergetrain.suite_env）", ",".join(stray))
else:
    ok("T0a 套件族八键唯一定义在 suite_env，mergetrain/recycle 零散点字面量")
if "def run_argv(argv, to)" in rc_src and "env=_rot_env()" in src_slice(rc_src, "run_argv"):
    ok("T0b run_argv 保持 base 签名且体内经 _rot_env（窗外=None 旧样继承=REDAUTO 链零扰动）")
else:
    bad("T0b run_argv 注入形缺失或签名被改", "")
if "rotate_isolation" in src_slice(mt_src, "card_verify_step"):
    ok("T0c 火车步②挂 rotate_isolation 继承窗")
else:
    bad("T0c card_verify_step 未挂套件根窗", "")
if "rotation_root()" in src_slice(rc_src, "done_sweep_scan"):
    ok("T0d done-sweep/轮转/巡逻宿主套 rotation_root 独立根")
else:
    bad("T0d done_sweep_scan 未套独立套件根", "")
if "_fail_digest(out)" in src_slice(mt_src, "scope_lock_step"):
    ok("T0e scope_lock_step 红因走 _fail_digest 点名")
else:
    bad("T0e scope_lock_step 未点名（截尾旧形）", "")
if "_reval_hold(rec, commit, tip0)" in src_slice(mt_src, "stage2_merge"):
    ok("T0f stage2 队列有 _reval_hold 复用闸")
else:
    bad("T0f stage2 缺同输入复用闸", "")

# ══ T1 _reval_hold 四态 ══
mt = spec_load("mt_det", MT_PATH)
C = "c" * 40
T = "t" * 40
U = "u" * 40
rec = {"stage": "refailed", "reval_key": "%s@%s" % (C[:12], T[:12])}
if mt._reval_hold(rec, C, T):
    ok("T1① 同卡同主干+refailed=复用维持")
else:
    bad("T1① 同输入未判复用（翻转放大器未拆）", "")
if not mt._reval_hold(rec, C, U):
    ok("T1② 主干推进=必重验")
else:
    bad("T1② 主干推进仍复用（陈旧红冒充本圈判定）", "")
if not mt._reval_hold({"stage": "passed", "reval_key": "%s@%s" % (C[:12], T[:12])}, C, T):
    ok("T1③ 非 refailed 态不复用")
else:
    bad("T1③ passed 态被误复用", "")
if not mt._reval_hold({"stage": "refailed"}, C, T):
    ok("T1④ 无 reval_key（超时作废/旧账）=重掷")
else:
    bad("T1④ 无锚复用（TIMEOUT 作废面破）", "")


# ══ T2 stage2 双圈假件驱动（真判红→复用→主干推进重验；撤针副本必重掷） ══
ORIG_FNS = {n: getattr(mt, n) for n in (
    "queue_of", "main_ref", "tip_of", "is_ancestor", "merge_tree_probe",
    "changed_files", "sandbox_materialize", "py_compile_step", "card_verify_step",
    "scope_lock_step", "git_ev_now", "_conflict_closure", "sandbox_cleanup", "main_worktree")}

class Ev:
    def __init__(self):
        self.calls = []

    def __call__(self, team, cid, text):
        self.calls.append(text)


def drive(module, tip, recycle_mod=None):
    """按给定 tip 跑一圈 stage2；返回 (计数dict, rec, ev)。全部外联打桩，零真 git/沙箱/账。"""
    cnt = {"sb": 0, "scope": 0}
    ev = Ev()
    module.queue_of = lambda cards, state: [("X1", 1, 0.0)]
    module.main_ref = lambda repo, git: "main"
    module.tip_of = lambda repo, ref, git: tip
    module.is_ancestor = lambda repo, commit, ref, git: False
    module.merge_tree_probe = lambda repo, ref, commit, git: {"ok": True, "clean": True, "conflicts": [],
                                                              "tree": "e" * 40}
    module.changed_files = lambda repo, ref, commit, git: ["cc-team/ops/dispatch.py"]
    module.sandbox_materialize = lambda *a, **k: (cnt.__setitem__("sb", cnt["sb"] + 1), _newroot(), "sha")[1:]
    module.py_compile_step = lambda root, files: (0, "py_compile 绿")
    module.card_verify_step = lambda root, verify, rmod: (0, "本卡判据 绿")
    module.scope_lock_step = lambda root, ch, run=None: (cnt.__setitem__("scope", cnt["scope"] + 1),
                                                         (1, "改动面锁 bash tests/all.sh rc=1 FAIL: 85 …"))[1]
    module.git_ev_now = ev
    module._conflict_closure = lambda *a, **k: None
    module.sandbox_cleanup = lambda *a, **k: None
    module.main_worktree = lambda repo, ref, git: None
    state = {"cards": {"X1": {"repo": "R", "commit": C, "stage": "passed"}}}
    cards = {"X1": {"status": "DONE", "verify": "true"}}
    logbuf = []
    n = module.stage2_merge(cards, state, "TEAM", True, logbuf.append,
                            recycle_mod or object(), git=lambda *a: (0, ""))
    return cnt, state["cards"]["X1"], ev, logbuf, n


cnt1, rec1, ev1, log1, n1 = drive(mt, T)
if cnt1["sb"] == 1 and rec1.get("stage") == "refailed" and rec1.get("reval_key") == "%s@%s" % (C[:12], T[:12]) \
        and len(ev1.calls) == 1 and "refailed" in ev1.calls[0]:
    ok("T2① 首圈真判红：沙箱起+全步跑+事件一笔+reval_key 落账", "detail=%s" % (rec1.get("fail_detail") or "")[:60])
else:
    bad("T2① 首圈判红形态破", cnt1, rec1, ev1.calls)
# 二圈=同 state 续跑（复用面必须见到一圈的账）——重设桩但保留 state/rec
def drive_again(state, tip):
    cnt = {"sb": 0, "scope": 0}
    ev = Ev()
    mt.queue_of = lambda cards, state: [("X1", 1, 0.0)]
    mt.main_ref = lambda repo, git: "main"
    mt.tip_of = lambda repo, ref, git: tip
    mt.is_ancestor = lambda repo, commit, ref, git: False
    mt.merge_tree_probe = lambda repo, ref, commit, git: {"ok": True, "clean": True, "conflicts": [],
                                                        "tree": "e" * 40}
    mt.changed_files = lambda repo, ref, commit, git: ["cc-team/ops/dispatch.py"]
    mt.sandbox_materialize = lambda *a, **k: (cnt.__setitem__("sb", cnt["sb"] + 1), _newroot(), "sha")[1:]
    mt.py_compile_step = lambda root, files: (0, "py_compile 绿")
    mt.card_verify_step = lambda root, verify, rmod: (0, "本卡判据 绿")
    mt.scope_lock_step = lambda root, ch, run=None: (cnt.__setitem__("scope", cnt["scope"] + 1),
                                                     (1, "改动面锁 bash tests/all.sh rc=1 FAIL: 85 …"))[1]
    mt.git_ev_now = ev
    mt._conflict_closure = lambda *a, **k: None
    mt.sandbox_cleanup = lambda *a, **k: None
    mt.main_worktree = lambda repo, ref, git: None
    cards = {"X1": {"status": "DONE", "verify": "true"}}
    logbuf = []
    n = mt.stage2_merge(cards, state, "TEAM", True, logbuf.append, object(), git=lambda *a: (0, ""))
    return cnt, ev, logbuf, n


state = {"cards": {"X1": dict(rec1)}}
cnt2, ev2, log2, n2 = drive_again(state, T)
if cnt2["sb"] == 0 and cnt2["scope"] == 0 and len(ev2.calls) == 0 \
        and any("REVAL-HOLD" in l for l in log2) and n2["refail"] == 1:
    ok("T2② 同输入二圈零重掷：不起沙箱不跑锁不补事件，滞留计数照给")
else:
    bad("T2② 同输入仍重掷（翻转放大器未拆）", cnt2, ev2.calls, log2)
state3 = {"cards": {"X1": dict(rec1)}}
cnt3, ev3, log3, n3 = drive_again(state3, U)
if cnt3["sb"] == 1 and len(ev3.calls) == 1:
    ok("T2③ 主干推进三圈必重验（复用只省同输入，不省判定）")
else:
    bad("T2③ 主干推进未重验（陈旧判决污染新面）", cnt3, ev3.calls)

old_src = mt_src.replace("if _reval_hold(rec, commit, tip0):", "if False and _reval_hold(rec, commit, tip0):", 1)
if old_src == mt_src:
    bad("T2哨 撤针替换未命中（检测式损坏=本锁空转）", "")
    mto = None
else:
    d = tempfile.mkdtemp(prefix="det-needle-")
    try:
        p = os.path.join(d, "mergetrain_old.py")
        io.open(p, "w", encoding="utf-8").write(old_src)
        mto = spec_load("mt_old", p)
        stateo = {"cards": {"X1": dict(rec1)}}

        def drive_old():
            cnt = {"sb": 0}
            ev = Ev()
            mto.queue_of = lambda cards, state: [("X1", 1, 0.0)]
            mto.main_ref = lambda repo, git: "main"
            mto.tip_of = lambda repo, ref, git: T
            mto.is_ancestor = lambda repo, commit, ref, git: False
            mto.merge_tree_probe = lambda repo, ref, commit, git: {"ok": True, "clean": True, "conflicts": [],
                                                        "tree": "e" * 40}
            mto.changed_files = lambda repo, ref, commit, git: ["cc-team/ops/dispatch.py"]
            mto.sandbox_materialize = lambda *a, **k: (cnt.__setitem__("sb", cnt["sb"] + 1), _newroot(), "sha")[1:]
            mto.py_compile_step = lambda root, files: (0, "绿")
            mto.card_verify_step = lambda root, verify, rmod: (0, "绿")
            mto.scope_lock_step = lambda root, ch, run=None: (1, "红")
            mto.git_ev_now = ev
            mto._conflict_closure = lambda *a, **k: None
            mto.sandbox_cleanup = lambda *a, **k: None
            mto.main_worktree = lambda repo, ref, git: None
            logbuf = []
            mto.stage2_merge({"X1": {"status": "DONE", "verify": "true"}}, stateo, "TEAM", True,
                             logbuf.append, object(), git=lambda *a: (0, ""))
            return cnt, len(ev.calls)

        # 先把 reval_key 种进一圈的账（同 rec1 形态），撤针副本二圈必须仍重掷
        cnto, evo = drive_old()
        if cnto["sb"] >= 1:
            ok("T2撤针 复用闸拆除后同输入照旧每圈重掷（锁咬得住）", "sb=%s ev=%s" % (cnto["sb"], evo))
        else:
            bad("T2撤针 拆闸仍不重掷（T2②为假绿）", cnto)
    finally:
        shutil.rmtree(d, ignore_errors=True)

for _n, _f in ORIG_FNS.items():
    setattr(mt, _n, _f)
ok("T2z 桩面复原（同 mt 对象交还真函数=后段 T3/T4 不啃 stub）")

# ══ T3 共根并发场景 + 独立根窗（卡判据③原文「沙箱仓现造共根并发场景，撤隔离闸必红」） ══
rc = spec_load("rc_det", RC_PATH)
if rc._rot_env() is None:
    ok("T3① 窗外 _rot_env=None（子进程旧样继承 os.environ=默认固定名共根的前提面）")
else:
    bad("T3① 窗外竟注入（REDAUTO 共享链被扰动）", "")

BASH = None
try:
    BASH, err = mt._scope_bash()
except Exception as exc:
    BASH, err = None, repr(exc)

SH = 'rm -rf "$CCSUITE_DIR" 2>/dev/null; mkdir -p "$CCSUITE_DIR"; echo "%s" > "$CCSUITE_DIR/%s.mark"'


def run_in_windows(isolated):
    """两线程并跑「套件」（各先 rm -rf 自家根再写 mark）：isolated=各经 rotation_root 窗
    （独立根，mark 必全存活）；否则两线程显式共一个 shared 根（旧世界=默认固定名的等价形，
    键全同→B 的 rm 必抹 A 的成果/或 A 写时目录已被抹=rc≠0）。mark 存活判定点在**关窗前**
    （rotation_root finally 会 rmtree 自家根——判晚=自拆现场假红）。返回 (marks, results,
    rootset)，results[tag]=(rc, root, alive)。"""
    shared = tempfile.mkdtemp(prefix="det-shared-").replace("\\", "/")
    results = {}
    barrier = threading.Barrier(2)

    def worker(tag):
        if isolated:
            with rc.rotation_root():
                ccroot = rc._rot_env()["CCSUITE_DIR"]
                barrier.wait(timeout=15)
                time.sleep(0.2 if tag == "B" else 0)
                rcx, out = rc.run_argv([BASH, "-c", SH % (tag, tag)], 30)
                alive = os.path.isfile(os.path.join(ccroot.replace("/", os.sep), tag + ".mark"))
                results[tag] = (rcx, ccroot, alive)
        else:
            barrier.wait(timeout=15)
            time.sleep(0.2 if tag == "B" else 0)
            script = 'CCSUITE_DIR="%s"; export CCSUITE_DIR; %s' % (shared, SH % (tag, tag))
            rcx, out = rc.run_argv([BASH, "-c", script], 30)
            results[tag] = (rcx, shared, None)   # alive 不在本线程判（见下方全跑完后的确定性对账）

    ts = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    if isolated:
        marks = sum(1 for v in results.values() if v[2])       # 窗内存活判定（各自独立根）
    else:
        # 共根世界（默认固定名等价形）：每套「开跑即 rm -rf 自家根」= 全跑完后 shared 里
        # 只可能剩下**最后落笔者**的 mark（先完成者的成果被后起者抹除）——不赌时序的逻辑必然。
        marks = sum(1 for tag in ("A", "B")
                    if os.path.isfile(os.path.join(shared.replace("/", os.sep), tag + ".mark")))
        for tag in results:
            rcx, root, _ = results[tag]
            results[tag] = (rcx, root, rcx == 0 and os.path.isfile(
                os.path.join(shared.replace("/", os.sep), tag + ".mark")))
    rootset = set(v[1] for v in results.values())
    shutil.rmtree(shared, ignore_errors=True)
    return marks, results, rootset


if BASH and os.path.isfile(BASH):
    marks_o, res_o, roots_o = run_in_windows(isolated=False)
    # 共根交错：后起者 rm 抹掉先到者成果（先到者写时目录已被 rm=rc≠0，或 mark 被抹）
    if marks_o < 2 or any(v[0] != 0 for v in res_o.values()):
        ok("T3② 共根并发必互踩（撤隔离现形：marks=%s rc=%s roots=%s）"
           % (marks_o, [v[0] for v in res_o.values()], len(roots_o)), "")
    else:
        bad("T3② 共根场景未互踩（检测式空转？重跑本锁确认）", marks_o, res_o)
    marks_n, res_n, roots_n = run_in_windows(isolated=True)
    if marks_n == 2 and all(v[0] == 0 for v in res_n.values()) and len(roots_n) == 2:
        ok("T3③ 各经 rotation_root 窗并跑互不侵犯（两独立根两 mark 全存活）")
    else:
        bad("T3③ 独立根窗未隔离", marks_n, res_n, roots_n)
    # 单入口对账：窗内八键全指本次根
    with rc.rotate_isolation("/tmp/X") as r:
        e = rc._rot_env()
    if e and all(str(e[k]).startswith("/tmp/X") for k in SUITE_KEYS):
        ok("T3④ 窗内八键全经 mergetrain.suite_env 指本次根（单入口注入）")
    else:
        bad("T3④ 窗内键族不完整或指向漂移", e and sorted(e.keys())[:4])
else:
    bad("T3 宿主 bash 解析不可达（宁红不 SKIP：本锁判据含真子进程面）", BASH, err)

# ══ T4 红因点名（scope_lock_step 假 run：FAIL 头 + 40 行 PASS 噪声尾） ══
noise = "\n".join("PASS: %d 噪声断言" % i for i in range(40))
SUITE_OUT = ("FAIL: selftest (rc=1) —— /tmp/ccsuite/selftest.log 全部红条+尾部:\n"
             "FAIL: 85 补绑失灵或幂等破(起班窗口没赶上=永远缺锚)\n"
             "=== fails=1 skips=11 ===\n"
             + noise + "\n=== fails=1 skips=11 ===\n"
             "selftest=333s adversary=78s guard=12s pidalive=4s ccenv=4s wall=334s\n")


def stub_run(cmd, cwd=None, env=None, to=None):
    if "select.sh" in cmd[-1] or (len(cmd) > 1 and cmd[1] == "tests/select.sh"):
        return 0, "FULL: bash tests/all.sh\n"
    return 1, SUITE_OUT


root = tempfile.mkdtemp(prefix="det-scope-")
try:
    d = os.path.join(root, "wt", "cc-team", "tests")
    os.makedirs(d)
    io.open(os.path.join(d, "select.sh"), "w", encoding="utf-8").write("#!/bin/bash\n")
    rco, det = mt.scope_lock_step(root, ["cc-team/ops/dispatch.py"], run=stub_run)
    if rco == 1 and "FAIL: 85" in det and "fails=1" in det:
        ok("T4① 红卷点名：成员名+fails 计数进 fail_detail（不再被截尾吃掉）",
           "len=%s" % len(det))
    else:
        bad("T4① 红卷未点名", rco, det[:150])
    # 撤针面：_tail(160) 旧形在这份原文里必丢成员名（差分对的「红得起来」侧）
    old_line = mt._tail(SUITE_OUT)
    if "FAIL: 85" not in old_line:
        ok("T4② 撤针对照：旧 _tail 形态确实现形丢名（T4①为真锁非空转）")
    else:
        bad("T4② 旧形竟保留名（本锁检测式与病灶脱钩）", old_line[:120])
    d_src = mt_src.replace("_fail_digest(out))", "_tail(out))", 1)
    if d_src == mt_src:
        bad("T4哨 点名撤针替换未命中", "")
    else:
        dd = tempfile.mkdtemp(prefix="det-needle4-")
        try:
            p2 = os.path.join(dd, "mergetrain_old4.py")
            io.open(p2, "w", encoding="utf-8").write(d_src)
            mto4 = spec_load("mt_old4", p2)
            rc4, det4 = mto4.scope_lock_step(root, ["cc-team/ops/dispatch.py"], run=stub_run)
            if rc4 == 1 and "FAIL: 85" not in det4:
                ok("T4③ 撤闸副本必丢名（隔离点名闸咬得住）", det4[:80])
            else:
                bad("T4③ 撤闸副本仍点名（锁假绿）", det4[:150])
        finally:
            shutil.rmtree(dd, ignore_errors=True)
finally:
    shutil.rmtree(root, ignore_errors=True)

for _d in FAKE_ROOTS:
    shutil.rmtree(_d, ignore_errors=True)
print("=" * 46)
if fails:
    print("TRAIN_REVERIFY_DET_FAIL %d 条: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("TRAIN_REVERIFY_DET_PASS")
