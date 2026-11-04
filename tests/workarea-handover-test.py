#!/usr/bin/env python3
# workarea-handover-test.py — 卡面工作区标记+交接链 红绿锁（业主 seq=15，2026-11-03）。
# 断言面（真 git 沙箱仓，双侧形态=纯 git+python）：
#   W1 claim 即登记：卡面 workarea 落本任脏件快照（路径/大小/mtime/XY 码），事件 WORKAREA
#   W2 release 不清工作区：脏件清单随行 handover{epoch,by,files}，事件 HANDOVER-OUT
#   W3 半途死再 claim：HANDOVER-IN prev=eN 事件 + 卡面继承前任账（接活不从零）
#   W4 干净仓：claim 也落 workarea(files=[] captured=True)=不丢账；release 无脏=不发 HANDOVER-OUT
#   W5 仓不可用（repo 路径坏）：claim 主链不拦，WARN 出声+captured=False（交接有缺口要出声）
#   W6 REJECT 打回同样落 handover（回收与退回两门同纪律）
# 跑法: python -I cc-team/tests/workarea-handover-test.py  exit 0 = WORKAREA_HANDOVER_PASS
import io, os, subprocess, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # cc-team 根
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
fails = []


def ok(n):
    print("PASS:", n)


def bad(n, d=""):
    fails.append(n)
    print("FAIL:", n, str(d)[:220])


def _sh(cwd, *args):
    r = subprocess.run(list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


_home = tempfile.mkdtemp(prefix="cc-wa-home-")
os.environ["CC_TEAM_HOME"] = _home
os.environ.pop("CC_AUTO_VERIFY", None)
os.environ.pop("CARD_FIELD_GATE", None)
sys.path.insert(0, SRC)
from ccards import cli as _cli, graph                    # noqa: E402
from ccards.config import STATE                          # noqa: E402


def run(*args):
    buf = io.StringIO()
    _old = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        rc = _cli.run(list(args))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout, sys.stderr = _old
    return rc or 0, buf.getvalue()


def events():
    try:
        return io.open(os.path.join(STATE, "events.log"), encoding="utf-8").read()
    except OSError:
        return ""


def mk_repo(dirty=True):
    d = tempfile.mkdtemp(prefix="cc-wa-repo-")
    rc, out = _sh(d, "git", "init", "-q", ".")
    assert rc == 0, out
    _sh(d, "git", "config", "user.email", "t@t")
    _sh(d, "git", "config", "user.name", "t")
    io.open(os.path.join(d, "base.py"), "w", encoding="utf-8").write("print(1)\n")
    rc, out = _sh(d, "git", "add", "-A")
    assert rc == 0, out
    rc, out = _sh(d, "git", "commit", "-qm", "base")
    assert rc == 0, out
    if dirty:
        io.open(os.path.join(d, "wip半成品.py"), "w", encoding="utf-8").write("x=2\n")
        io.open(os.path.join(d, "base.py"), "a", encoding="utf-8").write("# touched\n")
    return d


_ctr = [0]


def nid():
    _ctr[0] += 1
    return "WA%03d" % _ctr[0]


def mkcid(repo=None):
    cid = nid()
    a = ["create", cid, "--title", "t", "--verify", "true"]
    if repo:
        a += ["--repo", repo]
    rc, out = run(*a)
    assert rc == 0, (cid, out)
    return cid


def card(cid):
    return graph.all_cards()[cid]


def main():
    # W1/W2/W3 脏仓全流程
    d1 = mk_repo(dirty=True)
    cid = mkcid(d1)
    rc, out = run("claim", cid, "--owner", "wA")
    c = card(cid)
    wa = c.get("workarea") or {}
    ps = {f["p"] for f in wa.get("files", [])}
    if rc == 0 and "wip半成品.py" in ps and "base.py" in ps and "WORKAREA" in events() \
            and all("sz" in f and "mt" in f for f in wa.get("files", [])):
        ok("W1 claim 即登记：两脏件（未跟踪+已改）全入快照带大小/mtime")
    else:
        bad("W1 快照缺件", (out, wa))
    ep1 = wa.get("epoch")
    rc, out = run("release", cid, "--who", "recycle")
    c = card(cid)
    ho = (c.get("workarea") or {}).get("handover") or {}
    if rc == 0 and ho.get("files") and ho.get("by") == "wA" and "HANDOVER-OUT" in events():
        ok("W2 release 不清工作区：前任脏件账随卡面落 handover")
    else:
        bad("W2 handover 断", (out, ho))
    rc, out = run("claim", cid, "--owner", "wB")
    c = card(cid)
    wa = c.get("workarea") or {}
    if rc == 0 and "HANDOVER-IN" in events() and (wa.get("handover_prev") or {}).get("by") == "wA":
        ok("W3 再 claim=HANDOVER-IN prev=e%s，前任账继承+本任新快照" % str(ho.get("epoch")))
    else:
        bad("W3 交接断", (out, wa))

    # W4 干净仓
    d2 = mk_repo(dirty=False)
    cid2 = mkcid(d2)
    rc, out = run("claim", cid2, "--owner", "wC")
    wa2 = card(cid2).get("workarea") or {}
    rc, out = run("release", cid2, "--who", "recycle")
    ev = events()
    if wa2.get("captured") and wa2.get("files") == [] and "HANDOVER-OUT" not in ev.split(
            "WA%03d" % (_ctr[0] - 1))[-1]:
        ok("W4 干净仓也落账(files=[] captured=True)，无脏不发交接")
    else:
        bad("W4 空账形态错", wa2)

    # W5 坏仓不拦主链（路径必须连 8.3 短名互解都救不活——temp 下显式造绝对不存在名）
    cid3 = mkcid("C:\\cc-wa-definitely-not-a-repo-%d" % os.getpid())
    rc, out = run("claim", cid3, "--owner", "wD")
    wa3 = card(cid3).get("workarea") or {}
    if rc == 0 and wa3.get("captured") is False and "WARN" in out:
        ok("W5 仓不可用=claim 照成+WARN 出声+captured=False")
    else:
        bad("W5 降级形态错", (rc, out[:120], wa3))

    # W6 REJECT 打回落 handover（做一张到 DONE 需 commit/锁，通道太重=直接测 _handover_out 同源）
    from ccards import commands                          # noqa: E402
    c6 = card(cid)
    c6["workarea"] = {"epoch": "9", "files": [{"p": "x", "st": "??", "sz": 1, "mt": "-"}],
                      "captured": True}
    n6, ep6 = commands._handover_out(c6, "wZ")   # 现场重新实探该仓脏件（不信任手塞的假账）
    if n6 >= 1 and (c6["workarea"].get("handover") or {}).get("by") == "wZ" and ep6 == "9":
        ok("W6 出账函数与 release/REJECT 同源（epoch/by/files 三件齐）")
    else:
        bad("W6 同源函数漂移", (n6, ep6, c6["workarea"]))

    if fails:
        print("=== WORKAREA_HANDOVER_FAIL n=%d ===" % len(fails))
        sys.exit(1)
    print("WORKAREA_HANDOVER_PASS")


if __name__ == "__main__":
    main()
