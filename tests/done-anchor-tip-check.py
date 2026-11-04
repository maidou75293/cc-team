#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# done-anchor-tip-check.py — MECH-DONEANCHOR-TIP-1 交卷锚红绿锁。
# 病因（6b6d0be0 记档候立卡，fuse 文档①「卡锚单 commit 对多笔半成品卡失真」）：cmd_done 只收
#   工人自报 commit——「自报≠树尖」时账上无实据可对（23:3x 孤儿进程案、EPOCH-STALE done 拒收案
#   当时只能事后口头对质）。
# 机制（ccards/commands.py）：done 落账同时记交卷瞬间 worktree 实 tip + decl/tip 分型：
#   same=正常落账 / ahead=「证据拆独立提交」等合法推进，WARN 且落 tip/ahead 差异（不得拦红）
#   / diverged=自报不在树尖历史（孤儿/错仓/改写哈希签名）→ 交卷必红、归因可见、账不落（--force
#   明示放行留痕）；树尖读不到=红（宁红不猜）。REJECT 与 commit/done_at 同笔清锚。
# 锁面（全沙箱：独立 CC_TEAM_HOME + 独立 git 仓，不碰正账；跑法 python3 -I -I
#   cc-team/tests/done-anchor-tip-check.py；exit 0=全绿）：
#   A  静态段：助手在位/cmd_done 调用+落账行/事件行带 tip/REJECT 清锚——物理摘记录段先红在这里。
#   S1 正常交卷 tip 落账（decl==tip；卡面 done_anchor + graph.json 实文 + events 行三处齐）；
#      S1b 短哈希自报归一化后仍判 same。
#   S2 ahead 合法形态：tip 比自报多一笔（证据拆独立提交）→ rc0 不拦 + WARN 归因 + rel/ahead 落账。
#   S3 diverged 交卷必红且归因可见：自报/实 tip/branch 三件齐喊 + events 留痕 + 卡面纹丝不动
#      （账不落）；S3b --force 明示放行但 rel 如实 diverged+forced痕。
#   S4 摘针必红面（记忆纪律「守卫要验过它红得起来」）：monkeypatch _done_anchor_tip 模拟摘记录段
#      → 同一正常交卷的 S1② 断言（锚落账）在该世界必假——证明锁真挂在记录段上、非虚挂；
#      复针后锚照落、diverged 闸同回。
#   S5 REJECT 清锚：退回重做的卡不带上一班树尖进下一轮。
#   S6 树尖读不到=红：HEAD 指向不存在 ref（worktree 损坏签名）宁红不猜拒落账。
import io, os, re, shutil, subprocess, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # cc-team 根
try:      # 宿主 cp936 控制台防炸（同 state_std_test 双侧形态）
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


_home = tempfile.mkdtemp(prefix="cc-done-anchor-home-")
_repos = []
os.environ["CC_TEAM_HOME"] = _home
os.environ.pop("CC_AUTO_VERIFY", None)   # 沙箱 done 不拉自动验收（同 merge-train/state_std 纪律）
sys.path.insert(0, SRC)
from ccards import cli as _cli            # noqa: E402
from ccards import commands as _cmds      # noqa: E402
from ccards import graph as _graph        # noqa: E402


def run(*args):
    """进程内走真通道：返回 (rc, 合并输出)。die/SystemExit/闸内异常=非 0，同 shell 语义。"""
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


def card(cid):
    return _graph.load(cid)


def gitc(d, *args):
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", d] + list(args),
                       capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    assert r.returncode == 0, "git %s rc=%s %s" % (" ".join(args), r.returncode,
                                                   (r.stderr or b"").decode("utf-8", "replace"))
    return out


def make_repo(tag, branch="lane"):
    d = os.path.join(tempfile.gettempdir(), "cc-done-anchor-%s-%s" % (tag, os.getpid()))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "sub"), exist_ok=True)
    gitc(d, "init", "-q", "-b", branch)
    gitc(d, "config", "user.email", "anchor@test")
    gitc(d, "config", "user.name", "anchor")
    gitc(d, "config", "core.autocrlf", "false")
    with open(os.path.join(d, "sub", "a.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("seed\n")
    gitc(d, "add", "-A")
    gitc(d, "commit", "-qm", "seed")
    _repos.append(d)
    return d


def commit(d, name):
    with open(os.path.join(d, "sub", name), "w", encoding="utf-8", newline="\n") as f:
        f.write("%s\n" % name)
    gitc(d, "add", "-A")
    gitc(d, "commit", "-qm", "feat: %s" % name)
    return gitc(d, "rev-parse", "HEAD")


_ctr = [0]


def mk_card():
    """开一张 CLAIMED 卡（owner 唯一避「一道一活」道锁互踩；无 files 域避越域闸干扰）。"""
    _ctr[0] += 1
    cid = "ANCH%02d" % _ctr[0]
    rc, out = run("create", cid, "--title", "锚沙箱卡", "--verify", "true")
    assert rc == 0, out
    rc, out = run("claim", cid, "--owner", "o_" + cid)
    assert rc == 0, out
    return cid


def do(cid, repo, sha, extra=()):
    return run("done", cid, "--owner", "o_" + cid, "--commit", sha,
               "--repo", repo, "--evidence", "x", *extra)


def events_text():
    p = os.path.join(_home, "state", "events.log")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def graph_text():
    p = os.path.join(_home, "state", "graph.json")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


# ─────────────────────────── A 静态段（物理摘记录段=先红在这里） ───────────────────────────
def a_static():
    src = io.open(os.path.join(SRC, "ccards", "commands.py"), encoding="utf-8").read()
    ck("A1 _done_anchor_tip 助手在位", "def _done_anchor_tip(c, repo, decl, force):" in src, "助手被摘")
    m = re.search(r"def cmd_done\(a\):.*?\ndef ", src, re.S)
    seg = m.group(0) if m else ""
    ck("A2 cmd_done 调用锚+落账行在位（摘记录段=本断言红）",
       "_done_anchor_tip(c, repo," in seg and 'c["done_anchor"] = anchor' in seg,
       "cmd_done 内缺锚调用或缺 c[\"done_anchor\"] 落账行")
    ck("A3 DONE 事件行带 tip 事实（events 可后查）", "commit=%s tip=%s" in seg,
       "事件行格式回退成裸 commit=（锚不进流水）")
    ck("A4 REJECT 同笔清锚（防旧锚污染下一班）", 'c["done_anchor"] = None' in src, "清锚行被摘")
    ck("A5 diverged 拒收走 events 留痕+die 双件套", "DONEANCHOR-REJECT" in src, "拒收痕缺失")

# ─────────────────────────── S1 正常交卷 tip 落账 ───────────────────────────
def s1():
    repo = make_repo("s1")
    sha = commit(repo, "work1.txt")
    cid = mk_card()
    rc, out = do(cid, repo, sha)
    a = (card(cid).get("done_anchor") or {})
    ck("S1① decl==tip 交卷绿且 status DONE", rc == 0 and card(cid)["status"] == "DONE", (rc, out))
    ck("S1② 卡面 done_anchor 落账 tip==实tip rel=same",
       a.get("tip") == sha and a.get("rel") == "same" and a.get("branch") == "lane", a)
    ck("S1③ graph.json 实文含锚（账在文件不在内存）", '"done_anchor"' in graph_text(), "")
    ck("S1④ events DONE 行含 commit+tip+same 三件",
       ("->DONE|o_%s|commit=%s tip=%s same" % (cid, sha, sha[:12])) in events_text(),
       [ln for ln in events_text().splitlines() if "->DONE" in ln][-1:])
    # S1b 短哈希自报：归一化后仍判 same（工人惯报 7 位）
    repo2 = make_repo("s1b")
    sha2 = commit(repo2, "work2.txt")
    cid2 = mk_card()
    rc2, out2 = do(cid2, repo2, sha2[:7])
    a2 = (card(cid2).get("done_anchor") or {})
    ck("S1b 短哈希自报归一化 tip 落账 rel=same",
       rc2 == 0 and a2.get("tip") == sha2 and a2.get("rel") == "same", (rc2, out2, a2))

# ─────────────────────────── S2 ahead：证据拆独立提交形态不得拦红 ───────────────────────────
def s2():
    repo = make_repo("s2")
    code = commit(repo, "code.txt")
    evi = commit(repo, "evidence.txt")          # 交卷后 tip 合法再推进一笔
    cid = mk_card()
    rc, out = do(cid, repo, code)
    a = (card(cid).get("done_anchor") or {})
    ck("S2① decl 是 tip 祖先 → 放行（done 写闸「证据拆独立提交」惯例不被本闸拦红）",
       rc == 0 and card(cid)["status"] == "DONE", (rc, out))
    ck("S2② 落账 rel=ahead tip==实tip ahead==1",
       a.get("rel") == "ahead" and a.get("tip") == evi and a.get("ahead") == 1, a)
    ck("S2③ WARN 归因可见（自报/树尖短哈希两要素）",
       "WARN: 交卷锚" in out and code[:12] in out and evi[:12] in out, out[-200:])
    ck("S2④ events 行含 ahead=1", "ahead=1" in events_text(), "")

# ─────────────────────────── S3 diverged 交卷必红且归因可见 ───────────────────────────
def s3():
    repo = make_repo("s3")
    tip = commit(repo, "on-lane.txt")
    gitc(repo, "checkout", "-q", "-b", "alt")
    ghost = commit(repo, "orphan-side.txt")     # 孤儿进程自报的 commit：存在但不在树尖历史
    gitc(repo, "checkout", "-q", "lane")
    cid = mk_card()
    before = events_text().count("->DONE")
    rc, out = do(cid, repo, ghost)
    c = card(cid)
    ck("S3① decl≠tip 且分叉 → 交卷必红（rc≠0）", rc != 0, (rc, out))
    ck("S3② 归因可见：自报/实 tip/branch 三件齐喊",
       ghost[:12] in out and tip[:12] in out and "branch=lane" in out, out[-300:])
    ck("S3③ 账不落：卡仍 CLAIMED 且无 done_anchor",
       c["status"] == "CLAIMED" and not c.get("done_anchor"), c.get("status"))
    ck("S3④ 拒收留痕 events DONEANCHOR-REJECT（且没混进一笔假 DONE）",
       "DONEANCHOR-REJECT" in events_text() and events_text().count("->DONE") == before,
       events_text()[-300:])
    rc2, out2 = do(cid, repo, ghost, extra=("--force",))
    a = (card(cid).get("done_anchor") or {})
    ck("S3b --force 明示放行但 rel 如实 diverged+留痕",
       rc2 == 0 and a.get("rel") == "diverged" and a.get("forced") is True
       and a.get("tip") == tip and "force" in out2 and "明示放行" in out2, (rc2, a))

# ─────────────────────────── S4 摘针必红面：锁绿全凭记录段 ───────────────────────────
def s4():
    repo = make_repo("s4")
    sha = commit(repo, "s4.txt")
    cid = mk_card()
    orig = _cmds._done_anchor_tip
    try:
        _cmds._done_anchor_tip = lambda c, repo, decl, force: None   # =摘掉记录段的效果
        rc, out = do(cid, repo, sha)
    finally:
        _cmds._done_anchor_tip = orig
    a = (card(cid).get("done_anchor") or {})
    # 摘针世界里 S1② 的断言（锚落账且 tip==实tip）必须为假——无论表现为崩溃还是静默漏账
    ck("S4① 摘针世界：S1② 断言（done_anchor.tip==实tip）必假（撤修复=本锁相应红，非虚挂）",
       (rc != 0) or (a.get("tip") != sha), (rc, a))
    cid2 = mk_card()
    rc2, out2 = do(cid2, repo, sha)             # 复针后再交卷同一 sha
    a2 = (card(cid2).get("done_anchor") or {})
    ck("S4② 复针世界：同一 sha 锚照落（摘/落差异全在记录段上，非环境噪声）",
       rc2 == 0 and a2.get("tip") == sha and a2.get("rel") == "same", (rc2, a2, out2))
    ck("S4③ 复针后 diverged 闸也随段回来（摘针同时摘掉拒收闸）",
       _cmds._done_anchor_tip is orig, "未复原成同一函数对象")

# ─────────────────────────── S5 REJECT 清锚 ───────────────────────────
def s5():
    repo = make_repo("s5")
    sha = commit(repo, "s5.txt")
    cid = mk_card()
    rc, out = do(cid, repo, sha)
    assert rc == 0 and (card(cid).get("done_anchor") or {}).get("tip") == sha, (rc, out)
    rc2, out2 = run("verdict", cid, "REJECT", "--reason", "锚沙箱退回")
    c = card(cid)
    ck("S5 REJECT 与 commit/done_at 同笔清 done_anchor（旧树尖不陪下一班）",
       rc2 == 0 and c["status"] == "OPEN" and not c.get("commit")
       and not c.get("done_at") and not c.get("done_anchor"), (rc2, out2, c.get("done_anchor")))

# ─────────────────────────── S6 树尖读不到=红（宁红不猜） ───────────────────────────
def s6():
    repo = make_repo("s6")
    sha = commit(repo, "s6.txt")
    gitc(repo, "symbolic-ref", "HEAD", "refs/heads/ghost-ref")    # worktree HEAD 损坏签名
    cid = mk_card()
    rc, out = do(cid, repo, sha)
    ck("S6 HEAD 不可读 → 拒落账且喊「交卷锚失败」（宁红不猜）",
       rc != 0 and "交卷锚失败" in out and "HEAD" in out, (rc, out[-200:]))
    ck("S6b 账不落：卡仍 CLAIMED", card(cid)["status"] == "CLAIMED", "")


def main():
    a_static()
    s1()
    s2()
    s3()
    s4()
    s5()
    s6()
    for d in _repos:
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(_home, ignore_errors=True)
    print("=== done-anchor-tip fails=%d ===" % len(fails))
    print("ANCHOR_TIP_PASS" if not fails else "ANCHOR_TIP_FAIL: " + "; ".join(fails[:8]))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
