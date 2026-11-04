#!/usr/bin/env python3
# state_std_test.py — MECH-STATE-STD-1 台账状态标准化定版·同源锁（业主信箱 2026-11-02 seq=4）。
# 独立文件（冲突纪律：不挤 selftest.sh 尾部与 MECH-DONE-SWEEP-1 撞车；selftest.sh 只加一行 #73 调用）。
# 五层断言：
#   S1 表自洽：术语/属主/必填字段覆盖七态且无表外状态；迁移矩阵无重复、无表外态/通道、
#      set 四态互移禁直写 CLAIMED/DONE/VERIFIED、终态出边封闭、非终态必有出口、STATUS_ENUM 不动。
#   S2 README↔代码同源：README 三张表（状态=属主/术语/迁移矩阵，标记包裹）与 statemachine 数据逐行对读，
#      改一边不改另一边必红。
#   S3 require_edge 单元：表内边全放行；表外关键边样例必拒且喊「迁移矩阵」。
#   S4 CLI 正向全边覆盖：矩阵每一行都走一遍真实命令通道（34 边全通、落态一致），
#      防「表里有、代码走不通」的假同源。done 收口必写 done_at（带冒号 ISO8601 可解），
#      verdict REJECT 与 claimed_at 同清。
#   S5 CLI 负向抽测：各命令喂错态卡必拒（防代码私自放行表外边）。
#   S6 缺字段闸三档：DONE 无 done_at → warn(缺省)只喊不进告警账 / enforce 进账 / off 静默。
# 跑法：python3 cc-team/tests/state_std_test.py   （exit 0=全绿；独立临时沙箱，不碰正账）
import io, os, re, shutil, subprocess, sys, tempfile, types

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # cc-team 根
try:      # 宿主控制台 cp936 会炸 ↔/中文混排——输出流钉 UTF-8（WSL 侧本就 UTF-8，双侧兼容）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, str(detail)[:200])


def _pin_bash():
    # 宿主钉定（记忆坑：win python 的 subprocess("bash") 被 System32 WSL bash 劫持）：
    # 优先 Git 自带 bash；配了就不动。
    if os.environ.get("CARD_VERIFY_BASH"):
        return
    g = shutil.which("git")
    if g:
        root = os.path.dirname(os.path.dirname(os.path.dirname(g)))
        for c in (os.path.join(root, "bin", "bash.exe"), os.path.join(root, "usr", "bin", "bash.exe")):
            if os.path.isfile(c) and "System32" not in c:
                os.environ["CARD_VERIFY_BASH"] = c
                return


_home = tempfile.mkdtemp(prefix="cc-state-std-home-")
_repo = tempfile.mkdtemp(prefix="cc-state-std-repo-")
os.environ["CC_TEAM_HOME"] = _home
os.environ.pop("CC_AUTO_VERIFY", None)
os.environ.pop("CARD_FIELD_GATE", None)
_pin_bash()
sys.path.insert(0, SRC)
from ccards import graph, statemachine as sm                     # noqa: E402
from ccards.config import STATUS_ENUM                            # noqa: E402
from ccards import cli as _cli                                   # noqa: E402


def run(*args):
    """进程内走一张闸：返回 (rc, 合并输出)。die/SystemExit/argparse 拒=非 0，同 shell 语义。"""
    buf = io.StringIO()
    _old_out, _old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        rc = _cli.run(list(args))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    except Exception as e:                                        # 闸内异常=红，但测试要继续统计
        rc = 99
        buf.write("EXC %r" % e)
    finally:
        sys.stdout, sys.stderr = _old_out, _old_err
    return rc, buf.getvalue()


def card(cid):
    return graph.load(cid)


def git_in_repo(*args):
    return subprocess.run(["git"] + list(args), cwd=_repo, capture_output=True)


def mk_commit(tag):
    with open(os.path.join(_repo, "a.py"), "a", encoding="utf-8") as f:
        f.write("# %s\n" % tag)
    git_in_repo("add", "-A")
    r = git_in_repo("commit", "-qm", "feat: %s" % tag)
    assert r.returncode == 0, r.stderr
    out = git_in_repo("rev-parse", "HEAD").stdout.decode().strip()
    return out


_ctr = [0]


def nid():
    _ctr[0] += 1
    return "STD%03d" % _ctr[0]


def mk(status, deps=None, lanes="", resident=False):
    """用合法通道造一张到目标态的卡（属主=owner_<cid>，verify=true，无 files 域）。
    resident=True 造保障/岗卡（四件占位；VERIFIED→CANCELED 转性边仅此类卡可走）。"""
    cid = nid()
    a = ["create", cid, "--title", "t", "--status", "DRAFT" if status == "DRAFT" else "OPEN",
         "--verify", "true"]
    if resident:
        a += ["--resident", "--duty", "t", "--anchor", "t", "--heartbeat", "true", "--fail", "t"]
    if status == "HOLD":
        a = ["create", cid, "--title", "t", "--status", "HOLD", "--verify", "true"]
    if deps:
        a += ["--deps", deps]
    if lanes:
        a += ["--lanes", lanes]
    rc, out = run(*a)
    assert rc == 0, (cid, out)
    if status == "DRAFT" or status == "OPEN" or status == "HOLD":
        return cid
    if status == "CANCELED":            # 作废只能从候派态直落（DONE/VERIFIED 无需作废=闸既有语义）
        rc, out = run("cancel", cid)
        assert rc == 0, (cid, out)
        return cid
    rc, out = run("claim", cid, "--owner", "o_" + cid)
    assert rc == 0, (cid, out)
    if status == "CLAIMED":
        return cid
    rc, out = run("done", cid, "--owner", "o_" + cid, "--commit", COMMIT, "--repo", _repo,
                  "--evidence", "x")
    assert rc == 0, (cid, out)
    if status == "DONE":
        return cid
    rc, out = run("verdict", cid, "PASS")
    assert rc == 0, (cid, out)
    return cid


DONE_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

# ─────────────────────────── S1 表自洽 ───────────────────────────
def s1():
    if set(sm.TERMS) != set(STATUS_ENUM):
        bad("S1 术语表七态全覆盖", sm.TERMS.keys())
    else:
        ok("S1 术语表覆盖七态")
    if len(set(sm.TERMS.values())) != 7:
        bad("S1 中文术语互不重复")
    else:
        ok("S1 中文术语互不重复")
    if set(sm.OWNER_MATRIX) == set(STATUS_ENUM) and set(sm.REQUIRED_FIELDS) == set(STATUS_ENUM):
        ok("S1 属主/必填字段表覆盖七态")
    else:
        bad("S1 属主/必填字段表覆盖七态")
    if STATUS_ENUM == ["DRAFT", "OPEN", "CLAIMED", "HOLD", "DONE", "VERIFIED", "CANCELED"]:
        ok("S1 STATUS_ENUM 七态未动（业主边界）")
    else:
        bad("S1 STATUS_ENUM 被动过!", STATUS_ENUM)
    if set(sm.TERMINAL) == {"VERIFIED", "CANCELED"}:
        ok("S1 终态=VERIFIED/CANCELED（不再照看）")
    else:
        bad("S1 终态定义漂移", sm.TERMINAL)
    triples = [(f, t, v) for f, t, v, _n in sm.TRANSITIONS]
    if len(triples) != len(set(triples)):
        bad("S1 矩阵边无重复")
    else:
        ok("S1 矩阵边无重复(%d条)" % len(triples))
    states = set(STATUS_ENUM) | {"-"}
    bads = [(f, t, v) for f, t, v in triples if f not in states or t not in states]
    if bads or any(v not in sm.CHANNELS for _f, _t, v in triples):
        bad("S1 矩阵态/通道全在表内", bads)
    else:
        ok("S1 矩阵态/通道全在表内")
    if {f for f, t, v in triples if v == "create"} == {"-"} and \
       {t for f, t, v in triples if f == "-"} == {"DRAFT", "OPEN", "HOLD"}:
        ok("S1 create 只放 DRAFT/OPEN/HOLD（- 起点唯一通道）")
    else:
        bad("S1 create 起点漂移")
    set_edges = [(f, t) for f, t, v in triples if v == "set"]
    four = {"DRAFT", "OPEN", "HOLD", "CANCELED"}
    if {f for f, t in set_edges} == four and {t for f, t in set_edges} == four and len(set_edges) == 16:
        ok("S1 set=四态互移16边（禁直写 CLAIMED/DONE/VERIFIED）")
    else:
        bad("S1 set 边集漂移", set_edges)
    # 终态出边封闭 + 非终态必有出口
    out_by = {}
    for f, t, v in triples:
        out_by.setdefault(f, []).append((t, v))
    # VERIFIED→CANCELED(cancel)=seq=10 业主补充令开边的系统保障转性专用通道（命令闸限 resident，
    # 业务终态卡行为仍封闭——S5 负向钉着），列入出边封闭表的显式豁免。
    exp_term = {"VERIFIED": {("OPEN", "verdict REJECT"), ("VERIFIED", "verdict --reverify"),
                             ("CANCELED", "cancel")},
                "CANCELED": {("DRAFT", "set"), ("OPEN", "set"), ("HOLD", "set"), ("CANCELED", "set")}}
    if all(set(out_by.get(s, [])) == exp for s, exp in exp_term.items()):
        ok("S1 终态出边封闭（REJECT/复活口子外无路）")
    else:
        bad("S1 终态出边漂移", {s: out_by.get(s) for s in exp_term})
    if all(s in out_by for s in STATUS_ENUM if s not in sm.TERMINAL):
        ok("S1 非终态必有出口（无死态）")
    else:
        bad("S1 存在无出口死态")

# ─────────────────────────── S2 README↔代码同源 ───────────────────────────
def _readme_tbl(marker):
    txt = io.open(os.path.join(SRC, "README.md"), encoding="utf-8").read()
    try:
        seg = txt.split("<!-- %s-BEGIN -->" % marker, 1)[1].split("<!-- %s-END -->" % marker, 1)[0]
    except IndexError:
        return None
    rows = []
    for ln in seg.splitlines():
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        # 表头行=状态/从；分隔行=**每一格**都只由 -: 空格组成（「| - |」的 create 行首格是单个 -，
        # 不能误当分隔行丢弃——同源断言漏 3 条 create 边的实修）
        if cells[0] in ("状态", "从") or all(c and set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows


def s2():
    for key in ("状态=属主", "迁移矩阵"):
        txt = io.open(os.path.join(SRC, "README.md"), encoding="utf-8").read()
        if key not in txt:
            bad("S2 README 含「%s」" % key)
        else:
            ok("S2 README 含「%s」" % key)
    t = _readme_tbl("STATE-OWNER")
    exp = [[s, sm.TERMS[s], sm.OWNER_MATRIX[s][0], sm.OWNER_MATRIX[s][1], sm.OWNER_MATRIX[s][2]]
           for s in STATUS_ENUM]
    if t == exp:
        ok("S2 状态=属主矩阵 README↔代码逐行同源")
    else:
        bad("S2 属主矩阵漂移", (t or "缺表") if t != exp else "")
    t = _readme_tbl("STATE-TERMS")
    if t and [[r[0], r[1]] for r in t] == [[s, sm.TERMS[s]] for s in STATUS_ENUM]:
        ok("S2 术语表 README↔代码同源")
    else:
        bad("S2 术语表漂移", t)
    t = _readme_tbl("STATE-MATRIX")
    exp = [[f, to, v, n] for f, to, v, n in sm.TRANSITIONS]
    if t and sorted(map(tuple, t)) == sorted(map(tuple, exp)):
        ok("S2 迁移矩阵 README↔代码 %d 边全同行" % len(exp))
    else:
        bad("S2 迁移矩阵漂移", "README行数=%s 期望=%s" % (len(t) if t else None, len(exp)))
    t = _readme_tbl("STATE-FIELDS")
    if t and sorted(r[0] for r in t) == sorted(STATUS_ENUM):
        ok("S2 必填字段表 README 覆盖七态")
    else:
        bad("S2 必填字段表缺态", t)

# ─────────────────────────── S3 require_edge ───────────────────────────
def _capture(fn):
    buf = io.StringIO()
    _o, _e = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        fn()
        rc = 0
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    except BaseException:
        rc = 98
    finally:
        sys.stdout, sys.stderr = _o, _e
    return rc, buf.getvalue()


def s3():
    bads = []
    for f, t, v, _n in sm.TRANSITIONS:
        rc, out = _capture(lambda f=f, t=t, v=v: sm.require_edge(f, t, v))
        if rc != 0:
            bads.append((f, t, v, out[:80]))
    if bads:
        bad("S3 表内边被 require_edge 拒!", bads[:3])
    else:
        ok("S3 表内 %d 边全放行" % len(sm.TRANSITIONS))
    # ("VERIFIED","CANCELED","cancel") 自 seq=10 起为合法边（系统保障转性专用，命令闸限 resident；
    # 业务卡行为拒由 S5 负向覆盖）——不再列入表外边。
    illegal = [("OPEN", "DRAFT", "claim"), ("OPEN", "DONE", "done"), ("DONE", "CLAIMED", "verdict PASS"),
               ("CLAIMED", "VERIFIED", "verdict PASS"), ("HOLD", "CLAIMED", "claim"),
               ("-", "VERIFIED", "create"), ("-", "CLAIMED", "create"),
               ("DONE", "DRAFT", "set"), ("VERIFIED", "OPEN", "release"), ("HOLD", "OPEN", "claim"),
               ("DRAFT", "OPEN", "thaw"), ("CANCELED", "DONE", "set"), ("CLAIMED", "HOLD", "done"),
               ("OPEN", "OPEN", "claim")]
    miss = []
    for f, t, v in illegal:
        rc, out = _capture(lambda f=f, t=t, v=v: sm.require_edge(f, t, v))
        if rc == 0 or "迁移矩阵" not in out:
            miss.append((f, t, v, rc, out[:60]))
    if miss:
        bad("S3 表外边未被拒!", miss)
    else:
        ok("S3 表外 %d 关键边全被拒（防任意态互窜）" % len(illegal))

# ─────────────────────────── S4 CLI 正向全边覆盖 ───────────────────────────
def s4():
    done_at_seen = {}
    for f, t, v, _n in sm.TRANSITIONS:
        if v == "create":
            cid = nid()
            rc, out = run("create", cid, "--title", "t", "--status", t, "--verify", "true")
            good = rc == 0 and card(cid)["status"] == t
        elif v == "set":
            cid = mk(f)
            rc, out = run("set", cid, "--status", t)
            good = rc == 0 and card(cid)["status"] == t
        elif v == "claim":
            cid = mk("OPEN", lanes="sd1")
            rc, out = run("claim", cid, "--owner", "sd1")
            good = rc == 0 and card(cid)["status"] == "CLAIMED"
        elif v == "start":
            cid = mk(f)
            rc, out = run("start", cid, "--owner", "s_" + cid)
            good = rc == 0 and card(cid)["status"] == "CLAIMED"
        elif v == "done":
            cid = mk("CLAIMED")
            rc, out = run("done", cid, "--owner", "o_" + cid, "--commit", COMMIT, "--repo", _repo,
                          "--evidence", "x")
            c = card(cid)
            good = rc == 0 and c["status"] == "DONE"
            if good:
                da = c.get("done_at") or ""
                good = bool(DONE_AT_RE.match(da))  # 格式=ISO8601带冒号偏移
                done_at_seen[cid] = da
                import datetime
                try:
                    datetime.datetime.fromisoformat(da)
                except ValueError:
                    good = False
        elif v == "verdict PASS":
            cid = mk("DONE")
            rc, out = run("verdict", cid, "PASS")
            good = rc == 0 and card(cid)["status"] == "VERIFIED"
        elif v == "verdict REJECT":
            cid = mk(f)
            rc, out = run("verdict", cid, "REJECT")
            c = card(cid)
            good = rc == 0 and c["status"] == "OPEN" and not c.get("done_at") and not c.get("claimed_at")
        elif v == "verdict --reverify":
            cid = mk("VERIFIED")
            rc, out = run("verdict", cid, "PASS", "--as", "Verifier", "--reverify", "--reason", "同源锁")
            good = rc == 0 and card(cid)["status"] == "VERIFIED"
        elif v == "release":
            cid = mk("CLAIMED")
            rc, out = run("release", cid, "--who", "tester")
            good = rc == 0 and card(cid)["status"] == "OPEN"
        elif v == "cancel":
            # VERIFIED→CANCELED（seq=10 保障转性边）仅 resident 卡可走，其余作废边照旧业务卡
            cid = mk(f, resident=(f == "VERIFIED"))
            rc, out = run("cancel", cid, "--force")
            good = rc == 0 and card(cid)["status"] == "CANCELED"
        elif v == "thaw":
            up = mk("DONE")
            cid = mk("HOLD", deps=up)
            rc, out = run("thaw")
            good = rc == 0 and card(cid)["status"] == "OPEN"
        else:
            good = False
            out = "unknown via"
        if good:
            ok("S4 边(%s,%s,%s)可走通" % (f, t, v))
        else:
            bad("S4 边(%s,%s,%s)走不通!" % (f, t, v), out)
    if len(done_at_seen) == 1:
        ok("S4 done 收口落卡面 done_at（带冒号 ISO8601，fromisoformat 可解）")
    else:
        bad("S4 done_at 落卡面异常", done_at_seen)

# ─────────────────────────── S5 CLI 负向抽测 ───────────────────────────
def s5():
    neg = [
        ("claim", lambda: mk("DONE"), ("claim", "_C", "--owner", "n1")),
        ("claim", lambda: mk("CLAIMED"), ("claim", "_C", "--owner", "o_" )),
        ("done", lambda: mk("OPEN"), ("done", "_C", "--owner", "x", "--commit", COMMIT, "--repo", _repo)),
        ("done", lambda: mk("VERIFIED"), ("done", "_C", "--owner", "x", "--commit", COMMIT, "--repo", _repo)),
        ("release", lambda: mk("DONE"), ("release", "_C")),
        ("cancel", lambda: mk("DONE"), ("cancel", "_C")),
        ("cancel", lambda: mk("VERIFIED"), ("cancel", "_C")),
        ("verdict PASS", lambda: mk("CLAIMED"), ("verdict", "_C", "PASS")),
        ("verdict REJECT", lambda: mk("CLAIMED"), ("verdict", "_C", "REJECT")),
        ("start", lambda: mk("DONE"), ("start", "_C", "--owner", "n2")),
        ("start", lambda: mk("CLAIMED"), ("start", "_C", "--owner", "n2")),
        ("set", lambda: mk("CLAIMED"), ("set", "_C", "--status", "OPEN")),
    ]
    for label, mkf, args in neg:
        cid = mkf()
        real = tuple(cid if a == "_C" else a for a in args)
        if real[0] == "done":
            real = real + ("--evidence", "x")
        rc, out = run(*real)
        if rc == 0:
            bad("S5 非法通道被放行[%s]" % label, out)
        else:
            ok("S5 %s 拒于错态（rc=%s）" % (label, rc))
    for badstat in ("CLAIMED", "DONE", "VERIFIED", "CANCELED", "BOGUS"):
        cid = nid()
        rc, out = run("create", cid, "--title", "t", "--status", badstat)
        if rc == 0:
            bad("S5 create --status %s 被放行" % badstat)
        else:
            ok("S5 create --status %s 拒（choices+矩阵双钉）" % badstat)

# ─────────────────────────── S6 缺字段闸三档 ───────────────────────────
def s6():
    cid = mk("DONE")
    c = card(cid)
    c["done_at"] = None            # 模拟存量欠账（sweep 补账前的世界）
    graph.save(c)
    alerts_p = os.path.join(_home, "state", "alerts.json")

    def run_dag(mode):
        if mode is None:
            os.environ.pop("CARD_FIELD_GATE", None)
        else:
            os.environ["CARD_FIELD_GATE"] = mode
        rc, out = run("dag")
        raw = io.open(alerts_p, encoding="utf-8").read() if os.path.exists(alerts_p) else ""
        return rc, out, (cid in raw and "缺必填字段" in raw)

    rc, out, in_alerts = run_dag(None)
    if rc == 0 and ("WARN: 缺必填字段 " in out and cid in out and "done_at" in out) and not in_alerts:
        ok("S6 warn 档（缺省）：DONE 无 done_at 喊红但不进告警账（防存量满盘红误报）")
    else:
        bad("S6 warn 档异常", out[-160:])
    rc, out, in_alerts = run_dag("enforce")
    if rc == 0 and "ALARM: 缺必填字段 " in out and in_alerts:
        ok("S6 enforce 档：ALARM+进 alerts 账（存量补齐后 PM 切档启用）")
    else:
        bad("S6 enforce 档异常", out[-160:])
    rc, out, in_alerts = run_dag("off")
    if "缺必填字段" not in out:
        ok("S6 off 档静默")
    else:
        bad("S6 off 档未静默", out[-160:])
    # 补上账就闭嘴：闸必须只对「缺」亮，防永报警假闸
    c = card(cid)
    c["done_at"] = "2026-11-02T00:00:00+08:00"
    graph.save(c)
    rc, out, _ = run_dag("enforce")
    if ("缺必填字段 %s" % cid) not in out:
        ok("S6 补齐字段后闸即闭嘴（空检查/永报警=假闸防住）")
    else:
        bad("S6 补齐后仍在报", out[-160:])


def main():
    global COMMIT
    git_in_repo("init", "-q")
    git_in_repo("config", "user.email", "t@t")
    git_in_repo("config", "user.name", "t")
    COMMIT = mk_commit("seed")
    s1()
    s2()
    s3()
    s4()
    s5()
    s6()
    shutil.rmtree(_home, ignore_errors=True)
    shutil.rmtree(_repo, ignore_errors=True)
    print("=== state_std fails=%d ===" % len(fails))
    print("STATE_STD_PASS" if not fails else "STATE_STD_FAIL: " + "; ".join(fails[:8]))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
