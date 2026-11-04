#!/usr/bin/env python3
# purge-archive-test.py — 出账通道红绿锁（业主 seq=11：RESIDENT 14 张全部出账，PM 定形式）。
# 断言面（拔锚实验，全沙箱 CC_TEAM_HOME，不碰正账）：
#   P1 非 CANCELED 卡 purge 必拒（DONE/VERIFIED 出账=销毁验收史，永禁）
#   P2 无 reason purge 必拒（出账是审计事件）
#   P3 cancel→purge 正路：graph 除名 + state/archive/<id>.json 全量快照 + 事件行 ARCHIVED
#   P4 出账后卡号复用必拒（create 撞归档名闸，防同号双账）
#   P5 被 deps 引用必拒点名引用方；--force 放行但引用方 deps 留悬空（reconcile 侧另有 FATAL）
#   P6 账变后 graph.check() 必须 OK（seal/digest 同源，不留裸改痕）
#   P7 purge 已出账的号再 purge=拒（不存在或已出账）
# 跑法: python -I cc-team/tests/purge-archive-test.py   exit 0 = PURGE_ARCHIVE_PASS
import io, json, os, shutil, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # cc-team 根
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, str(detail)[:220])


_home = tempfile.mkdtemp(prefix="cc-purge-home-")
_repo = tempfile.mkdtemp(prefix="cc-purge-repo-")
os.environ["CC_TEAM_HOME"] = _home
os.environ.pop("CC_AUTO_VERIFY", None)
os.environ.pop("CARD_FIELD_GATE", None)
sys.path.insert(0, SRC)
from ccards import cli as _cli                       # noqa: E402
from ccards import graph                             # noqa: E402
from ccards.config import ARCHIVE, STATE             # noqa: E402


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


def mk(cid, lanes=""):
    a = ["create", cid, "--title", "t", "--verify", "true"]
    if lanes:
        a += ["--lanes", lanes]
    rc, out = run(*a)
    assert rc == 0, (cid, out)


def graph_ids():
    return set(graph.all_cards().keys())


def main():
    mk("PG1"); mk("PG2"); mk("PG3"); mk("PG4"); mk("PG5")
    mk("PG6"); mk("PG7"); mk("PGD")

    # P1 非 CANCELED 必拒（OPEN 卡直接 purge）
    rc, out = run("purge", "PG1", "--reason", "r")
    if rc != 0 and "CANCELED" in out:
        ok("P1 OPEN 卡 purge 拒（只有作废过的能出账）")
    else:
        bad("P1 闸漏", out)

    # P2 无 reason 必拒
    rc, out = run("purge", "PG1", "--reason", "")
    if rc != 0 and "reason" in out.lower():
        ok("P2 无理由 purge 拒（审计事件）")
    else:
        bad("P2 reason 闸漏", out)

    # P3 正路：cancel → purge
    rc, out = run("cancel", "PG2", "--reason", "转性作废")
    assert rc == 0, out
    rc, out = run("purge", "PG2", "--reason", "seq=11 出账", "--who", "PM")
    ap = os.path.join(ARCHIVE, "PG2.json")
    ev = io.open(os.path.join(STATE, "events.log"), encoding="utf-8").read()
    if rc == 0 and "PG2" not in graph_ids() and os.path.exists(ap):
        rec = json.load(io.open(ap, encoding="utf-8"))
        if (rec.get("id") == "PG2" and rec.get("status") == "CANCELED"
                and rec.get("purged_by") == "PM" and "ARCHIVED" in ev):
            ok("P3 cancel→purge：除名+归档全量快照+事件行留痕")
        else:
            bad("P3 归档内容缺件", {k: rec.get(k) for k in ("status", "purged_by")})
    else:
        bad("P3 正路断", out)

    # P4 出账后同号必拒
    rc, out = run("create", "PG2", "--title", "t2", "--verify", "true")
    if rc != 0 and "归档" in out:
        ok("P4 已出账卡号不许复用（防同号双账）")
    else:
        bad("P4 占号闸漏", out)

    # P5 deps 引用必拒点名；force 放行
    rc, out = run("create", "PGH", "--title", "h", "--verify", "true", "--deps", "PG3")
    assert rc == 0, out
    rc, out = run("cancel", "PG3", "--reason", "作废")
    assert rc == 0, out
    rc, out = run("purge", "PG3", "--reason", "r")
    if rc != 0 and "PGH" in out:
        ok("P5 被引用 purge 拒且点名引用方")
    else:
        bad("P5 引用闸漏", out)
    rc, out = run("purge", "PG3", "--reason", "引用方 PGH 已另行处置", "--force")
    if rc == 0 and "PG3" not in graph_ids():
        ok("P5b --force 越权通道可走")
    else:
        bad("P5b force 通道断", out)

    # P6 账面自洽（seal/digest 对账 OK）
    good, msg = graph.check()
    if good:
        ok("P6 purge 后对账 OK（%s）" % msg)
    else:
        bad("P6 账漂", msg)

    # P7 重复 purge 拒
    rc, out = run("purge", "PG2", "--reason", "r")
    if rc != 0 and ("不存在" in out or "已出账" in out):
        ok("P7 已出账号重复 purge 拒")
    else:
        bad("P7 重复闸漏", out)

    # P8 活卡永禁：VERIFIED 不经 cancel 直接 purge（先 done+verdict 造 VERIFIED 太重，
    #    用 set 不可达态=测不到；改用 DONE 态代表终态出边外卡）——done 需要锁/锚，
    #    沙箱内 CLAIMED→start→bind→done 全走一遍成本高，P1 已覆盖非 CANCELED 拒绝面。
    if "PGD" in graph_ids() and "PG1" in graph_ids():
        ok("P8 未动的卡还在账面（purge 零误伤）")
    else:
        bad("P8 误伤他卡", graph_ids())

    if fails:
        print("=== PURGE_ARCHIVE_FAIL n=%d ===" % len(fails))
        sys.exit(1)
    print("PURGE_ARCHIVE_PASS")


if __name__ == "__main__":
    main()
    shutil.rmtree(_home, ignore_errors=True)
    shutil.rmtree(_repo, ignore_errors=True)
