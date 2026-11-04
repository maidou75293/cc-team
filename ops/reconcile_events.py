#!/usr/bin/env python3
# reconcile_events.py — 对账器（只读，不重封不写卡；告警进聚合账）。
# MECH-PURE1（2026-10-31 业主令「不允许留下一行旧代码」）后三判据：
#  一：每卡在 events.log 的最后一条 "->新状态" == 事实图 card 的 status（裸改/漏记/崩残可检）；
#      归档账（state/archive/*.json，MECH-PURE1 自 tasks/archive 迁来）同判。
#  二：graph 完整性——seal 不符=写口外偷改事实（旧「闸门被换」盲区收编），digest 不符=视图未重算。
#  三：退役台账 REMOVAL-READY（MECH-RET1 机制保留；WATCH 已清零=当前无观察对象，
#      新兼容件必须去 ccards/retire.py WATCH 挂号，否则本判据看不见它）。
# 旧判据「镜像对拍」已随 tasks/ 双写镜像一起退役（原判据三整段删除处）。
# 迁移卡（DL-* 等）events 里无出生记录 → 只 WARN 不 ALARM（历史账不强求）。
# 用法: python3 ops/reconcile_events.py [--quiet]   # --quiet 只打 ALARM/汇总
import json, os, subprocess, sys
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost
sys.dont_write_bytecode = True  # REQ-OSSPATH-1 配套：入口关 pyc 写，防宿主路径进 pyc→判据 grep 假红

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
TEAM = os.environ.get("CC_TEAM_HOME") or ROOT
EVENTS = os.path.join(TEAM, "state", "events.log")
ARCHIVE = os.path.join(TEAM, "state", "archive")

from ccards import graph  # 事实唯一访问器；hook 侧同库——读法只此一家


def main():
    quiet = "--quiet" in sys.argv
    try:
        from ccards import alerts as _al
    except Exception:
        _al = None

    def alarm(msg):
        """ALARM 既打 stdout(人/cron 日志)也进聚合账(MECH-AUD1: 可数开放项、可 ack 闭环)。"""
        print(msg)
        if _al:
            _al.record(msg)
    last = {}       # cid -> 事件流水里的最终状态
    malformed = 0
    n_lines = 0
    if os.path.exists(EVENTS):
        with open(EVENTS, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.rstrip("\n")
                if not ln.strip():
                    continue
                n_lines += 1
                parts = ln.split("|")
                if len(parts) < 4 or "->" not in parts[2]:
                    malformed += 1
                    continue
                # REQ-VERIFY-DRYRUN-1 试跑闸痕（extra 前缀 TRIAL-，非状态迁移）：被拒发卡的卡号
                # 根本不入图，计入 last 会被判据一/幽灵扫成「永久幽灵卡反复红」——fuse 伪卡号
                # 「FUSE」同案（本文件 187 行在册教训）。只阅不记账。
                if len(parts) > 4 and parts[4].startswith("TRIAL-"):
                    continue
                cid, _, new = parts[2].partition("->")
                last[parts[1]] = new.strip()
    fact = graph.all_cards()                      # 唯一事实（活卡状态只认图；归档补名字见下）
    cards = {cid: c.get("status") for cid, c in fact.items()}
    purged = set()  # 出账墓碑（seq=11 purge：卡从 graph 除名、state/archive/<id>.json 盖 purged_at）
    for fn in sorted(os.listdir(ARCHIVE)) if os.path.isdir(ARCHIVE) else []:  # 归档账（历史侧写）
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(ARCHIVE, fn), encoding="utf-8") as f:
                c = json.load(f)
            acn = c.get("id") or fn[:-5]
            if c.get("purged_at") and acn not in fact:
                purged.add(acn)  # 出账形态合法尾=ARCHIVED，判据一单独放行（见下）
            if acn not in cards:  # MECH-RTY2：活图优先——归档旧态不得覆盖活卡（20261101 夜 DL-F5 假红实锤：
                cards[acn] = c.get("status")  # 旧账 VERIFIED 盖住新账 DONE 判成裸改）。归档只补活图没有的名字。
        except (ValueError, OSError):
            cards[fn[:-5]] = None

    alarms = 0
    # 判据一：事件流水 vs 事实（含归档）。
    for cid, st in sorted(cards.items()):
        if st is None:
            alarm("ALARM: 卡JSON损坏无法对账: %s" % cid)
            alarms += 1
            continue
        if cid not in last:
            if not quiet:
                print("WARN: %s 在 events.log 无出生记录(迁移卡?)" % cid)
            continue
        if last[cid] != st:
            # 出账墓碑例外（seq=11 purge 2026-11-03 实锤 14 条假红）：graph 除名+归档盖
            # purged_at+流水尾=ARCHIVED 是**设计形态**——卡面停笔在 CANCELED、尾行多一笔
            # ARCHIVED，不是裸改。只豁免这一种尾；墓碑卡若冒出 ARCHIVED 之外的新尾行=
            # 有人在出账后动过账（伪造/复活企图），照红不误（红绿锁 E3 钉死）。
            if cid in purged and last[cid] == "ARCHIVED":
                if not quiet:
                    print("OK: %s 出账墓碑（purge 合法形，不计裸改）" % cid)
                continue
            alarm("ALARM: %s 卡面=%s 但事件流水尾部=%s->%s(裸改/漏记/崩溃残渣)" % (cid, st, cid, last[cid]))
            alarms += 1
    ghosts = sorted(set(last) - set(cards))
    for cid in ghosts:
        alarm("ALARM: events 有卡 %s 但事实图无此卡" % cid)
        alarms += 1
    # 判据二：事实图完整性——seal 防写口外改动；digest 防视图未随账重算（崩残）。只读，绝不重封。
    try:
        okc, msg = graph.check()
        if not okc:
            alarm("ALARM: graph.json 不可信(%s)——核对后跑 cards_dag.py dag 以账重封重算" % msg)
            alarms += 1
    except Exception as exc:
        alarm("ALARM: graph 完整性检查不可用(%r)" % exc)
        alarms += 1
    # 判据三（MECH-RET1）：兼容层退役台账（机制在、WATCH 空=无对象；只读）。
    ready_cnt = 0
    try:
        from ccards import retire
        retire.bootstrap()
        _entries = _al.entries() if _al else {}
        for it in retire.assess(ROOT):
            if not it["ready"]:
                continue
            ready_cnt += 1
            msg = "REMOVAL-READY: %s 满窗零命中候退役(确认改线后删，或 ack 留档)" % it["name"]
            ack = (_entries.get(_al._key(msg)) or {}).get("ack") if _al else None
            at = ack and retire._ts(ack)
            if at and not (it["last"] and it["last"] > at):
                continue  # 已留档且无新命中
            alarm(msg)
            alarms += 1
    except Exception as exc:
        alarm("ALARM: 退役台账检查不可用(%r)" % exc)
        alarms += 1
    # 判据四（BUG-BOARD-SELF-AUDIT-1）：看板自审计数值锁——对活体页面(:8790)逐项数值断言
    # （龄单调/完成列形态/两页同指标/状态词单点/泳道在册），红=按稳定键进聚合账：
    # 同一问题跨轮只 count++ 不刷屏；键里没有浮动数值，详情只进本行 stdout（cron 日志可查）。
    # 脚本自带双抓取时窗（>页面批算 TTL=60s）；C3 v2（REJECT 轮）另走「静止窗+strike 二窗」——
    # 账本活跃时每轮多 1 次重试 + 1 次复核（各 >TTL），最坏一轮可近 6 分钟；超时/异常都记键不静默。
    sa = os.environ.get("BOARD_SELFAUDIT_PATH") or os.path.join(
        os.path.dirname(ROOT), "scripts", "e2e", "board-selfaudit-test.py")
    if os.path.exists(sa):
        try:
            sr = subprocess.run([sys.executable, "-I", sa],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600, creationflags=NO_WIN)
            out = (sr.stdout or b"").decode("utf-8", "replace")
            keys = [ln[len("SELFAUDIT-RED "):].split(" :: ")[0].strip()
                    for ln in out.splitlines() if ln.startswith("SELFAUDIT-RED ")]
            if sr.returncode not in (0, 1):
                keys.append("abnormal-rc%d" % sr.returncode)
            elif sr.returncode == 1 and not keys:
                keys.append("red-without-key")
            for k in keys:
                alarm("ALARM: 看板自审计[%s]" % k)
            alarms += len(keys)
        except Exception as exc:
            alarm("ALARM: 看板自审计不可用(%r)" % type(exc).__name__)
            alarms += 1
    else:
        alarm("ALARM: 看板自审计[missing]")
        alarms += 1
    if malformed:
        print("WARN: events.log 畸形行 %d(不计入 ALARM，人查)" % malformed)
    print("reconcile: cards=%d events=%d lines=%d alarms=%d retire_ready=%d" % (
        len(cards), len(last), n_lines, alarms, ready_cnt))
    sys.exit(1 if alarms else 0)


if __name__ == "__main__":
    main()
