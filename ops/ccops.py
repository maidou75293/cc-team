#!/usr/bin/env python3
# ccops.py — dispatcher 的单 tick（MECH-OPS1）。
# 这就是账本对外的「底层动作面」：任何上层面（人/REST/LangGraph/任何 agent 框架）编排最终
# 都必须落到这套带闸 CLI 指令——判据与原子性全在 cli 闸+双锁里，门面层加不出任何正确性。
# 分工定版：本 tick 做 thaw(解冻)+recycle(回收)+plan(报可派)；**不代 claim**——claim 必须带
# worker 真实 pid（README 硬约定：缺 pid=不可回收），pid 只有起会话的宿主知道，
# 所以本工具吐「claim 指令+起会话 brief」给宿主，由起会话方 claim 后把 brief 喂进会话。
# 默认 DRY 零写账；--apply 才落解冻/回收（仍旧走闸）。
import argparse, json, os, sys
sys.dont_write_bytecode = True  # REQ-OSSPATH-1 配套：入口关 pyc 写，防宿主路径进 pyc→判据 grep 假红

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from ccards import cli, engine  # 机制内进程调库（MECH-STD1 通信定版）


def _cli(argv):
    """经全闸入口跑一条写命令；吞 SystemExit（die 语义）如实返 rc。"""
    try:
        cli.run(argv)
        return 0
    except SystemExit as e:
        return int(e.code or 0)


def _child(argv):
    """recycle.main 读全局 argv（脚本出身）——临时换 argv 调它，调完还原。"""
    sys.path.insert(0, HERE)
    import recycle
    old = sys.argv
    sys.argv = ["ccops:recycle"] + argv
    try:
        recycle.main()
        return 0
    except SystemExit as e:
        return int(e.code or 0)
    finally:
        sys.argv = old


def tick(apply):
    snap = engine.tick()          # 只读面（#62 锁：不写账）
    print("ccops tick: ready=%d claimable=%s claimed=%d run_complete=%s alarms_open=%d retire_ready=%d" % (
        len(snap["ready"]),
        ",".join("%s:%s" % (l, c or "-") for l, c in sorted(snap["claimable"].items())) or "-",
        len(snap["claimed"]), snap["run_complete"],
        len(snap["alarms_open"]), len(snap["retire_ready"])))
    for m in snap["alarms_open"]:
        print("  ALARM(候处置): " + m)
    for n in snap["retire_ready"]:
        print("  REMOVAL-READY(候删): " + n)
    if apply:
        rc1 = _cli(["thaw"])
        rc2 = _cli(["reindex"])
        rc3 = _child(["--apply"])
        print("  apply: thaw=%d reindex=%d recycle=%d（全走闸，失败如实报）" % (rc1, rc2, rc3))
    else:
        print("  DRY：未解冻/未回收（--apply 才动账；plan/告警均只读）")
    for lane, cid in sorted(snap["claimable"].items()):
        if not cid:
            continue
        brief = os.path.join(ROOT, "briefs", "brief-%s.txt" % cid)
        print("DISPATCH lane=%s card=%s claim: python3 %s/cards_dag.py claim %s --owner %s --pid <worker真实pid>"
              " | brief=%s" % (lane, cid, ROOT, cid, lane, brief if os.path.exists(brief) else "(无)"))
    return snap


def main():
    ap = argparse.ArgumentParser(prog="ccops.py", description="dispatcher 单 tick（默认 DRY）")
    ap.add_argument("--apply", action="store_true", help="真的解冻/回收（走闸）；派工指令照吐")
    ap.add_argument("--json", action="store_true", help="尾行附 tick 快照 JSON（机器消费）")
    a = ap.parse_args()
    snap = tick(a.apply)
    if a.json:
        print("PLAN_JSON=" + json.dumps(snap, ensure_ascii=False))


if __name__ == "__main__":
    main()
