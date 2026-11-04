#!/usr/bin/env python3
# reconcile-tombstone-test.py — 出账墓碑对账豁免 红绿锁（业主 seq=11 purge 落地后
# reconcile_events 判据一 14 条假红实锤 2026-11-03，PM 直管框架面即修）。
# 断言面（沙箱 CC_TEAM_HOME，真 cards_dag CLI 造真账，真 reconcile_events 子进程跑）：
#   E1 合法出账形（graph 除名+archive 盖 purged_at+尾行->ARCHIVED）→ 对账 rc=0 零 ALARM
#   E2 墓碑后伪造尾行（追加 CANCELED->DONE）→ 必红（豁免只认 ARCHIVED 尾，blanket 豁免=更糟）
#   E3 归档件被抹掉 purged_at → 必红（认戳不认名）
#   E4 复原后复绿 + 在途活卡（TB-B OPEN）零误伤
# 跑法: python -I cc-team/tests/reconcile-tombstone-test.py  exit 0 = TOMBSTONE_RECON_PASS
# 变异红检（改后必红的两条撤闸路径，另跑 sed 副本）：
#   'if cid in purged and'->'if False and' → E1 红（假红复辟）；'== "ARCHIVED"'->'!=' → E2 红（滥豁免）
import io, json, os, subprocess, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cc-team 根
RECON = os.path.join(SRC, "ops", "reconcile_events.py")
CLI = os.path.join(SRC, "cards_dag.py")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
fails = []
_home = tempfile.mkdtemp(prefix="cc-tb-home-")
os.environ["CC_TEAM_HOME"] = _home
STATE = os.path.join(_home, "state")


def ok(n):
    print("PASS:", n)


def bad(n, d=""):
    fails.append(n)
    print("FAIL:", n, str(d)[:250])


def cli(*args):
    return subprocess.run([sys.executable, "-I", CLI] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=dict(os.environ), cwd=SRC)


def run_recon():
    stub = os.path.join(_home, "board-stub.py")   # 判据四钉桩：missing 会自造 ALARM 搅断言
    io.open(stub, "w", encoding="utf-8").write("import sys;sys.exit(0)\n")
    env = dict(os.environ)
    env["BOARD_SELFAUDIT_PATH"] = stub
    r = subprocess.run([sys.executable, "-I", RECON], stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, env=env, cwd=SRC)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def alarms_of(out):
    return [l for l in out.splitlines() if l.startswith("ALARM")]


def main():
    assert cli("create", "TB-A", "--title", "墓碑A", "--verify", "true").returncode == 0
    assert cli("create", "TB-B", "--title", "活卡B", "--verify", "true").returncode == 0
    assert cli("cancel", "TB-A", "--reason", "tombstone test").returncode == 0
    assert cli("purge", "TB-A", "--who", "PM", "--reason", "seq=11 出账形复刻").returncode == 0
    ap = os.path.join(STATE, "archive", "TB-A.json")
    evp = os.path.join(STATE, "events.log")
    tail0 = io.open(evp, encoding="utf-8").read()

    rc, out = run_recon()
    if rc == 0 and not alarms_of(out):
        ok("E1 合法出账墓碑=对账绿（rc=0 零 ALARM，尾行 ARCHIVED 豁免生效）")
    else:
        bad("E1 墓碑假红未拔干净", (rc, alarms_of(out)[:3]))

    # E2 墓碑后伪造尾行 → 必红
    with io.open(evp, "a", encoding="utf-8") as f:
        f.write("2026-11-03T07:00:00+0800|TB-A|CANCELED->DONE|forger|伪造复活\n")
    rc2, out2 = run_recon()
    if rc2 == 1 and any("TB-A" in l for l in alarms_of(out2)):
        ok("E2 墓碑卡冒 ARCHIVED 之外的尾行=照红（防拿豁免伪装通过）")
    else:
        bad("E2 红道不通", (rc2, out2[:200]))
    with io.open(evp, "w", encoding="utf-8") as f:
        f.write(tail0)                                   # 复原

    # E3 抹掉 purged_at 戳 → 必红（认戳不认名）
    a_doc = json.load(io.open(ap, encoding="utf-8"))
    stamp = a_doc.pop("purged_at", None)
    json.dump(a_doc, io.open(ap, "w", encoding="utf-8"), ensure_ascii=False)
    rc3, out3 = run_recon()
    if rc3 == 1 and any("TB-A" in l for l in alarms_of(out3)):
        ok("E3 无 purged_at 戳的归档不豁免（认戳不认名）")
    else:
        bad("E3 戳闸不通", (rc3, out3[:200]))
    if stamp:
        a_doc["purged_at"] = stamp
        json.dump(a_doc, io.open(ap, "w", encoding="utf-8"), ensure_ascii=False)

    # E4 复原复绿 + 活卡零误伤
    rc4, out4 = run_recon()
    if rc4 == 0 and not alarms_of(out4) and "TB-B" in io.open(
            os.path.join(STATE, "graph.json"), encoding="utf-8").read():
        ok("E4 复原后复绿，在途活卡在册未被波及")
    else:
        bad("E4 误伤/复绿失败", (rc4, alarms_of(out4)[:3]))

    if fails:
        print("=== TOMBSTONE_RECON_FAIL n=%d ===" % len(fails))
        sys.exit(1)
    print("TOMBSTONE_RECON_PASS")


if __name__ == "__main__":
    main()
