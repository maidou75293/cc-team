#!/usr/bin/env python3
# cc-team/tests/alarm_scope_check.py — BUG-REAPER-SCOPE-1 运行时断言（不是 grep 字样检查）。
# 现读 engine.tick() 的 alarms_open（与看板/派发同一本账），逐条检：
#   看护环格式「ALARM <cid>: …」（ops/recycle.py 升 Alarm 的独有格式）的开放项，
#   涉及卡当前状态必须= CLAIMED——属主表(seq4)定死 CLAIMED 才是回收看护的对象；
#   DONE=校验环节、VERIFIED/CANCELED=终态不再照看、无卡=更不该有警，任一即红（退出码 1）。
# 台账一致性格式「ALARM: …」（dag 点名无泳道映射/CANCELED 卡死、reconcile 裸改对账、
# 缺字段闸）合法涉及 OPEN/DONE 卡，不判红；但其中提到 VERIFIED/CANCELED 终态卡的开放项
# 打印 WARN 供 PM 复核（不该有任何环照看终态卡）——只点名，不误伤别的环的语义。
# 跑法（CC_TEAM_HOME 指哪本账就检哪本；缺省=本仓 cc-team，selftest #81/#87 靠它做红绿断言）：
#   python -I -X utf8 cc-team/tests/alarm_scope_check.py
#   红=输出逐条违规 + SCOPE_FAIL + 退出码 1；绿=SCOPE_OK + 0。账本读不出=2（宁红不盲检）。
# 话术并集（2026-11-02 整合修）：主干消费者（#81、卡面判据）只吃退出码与 SCOPE_OK/SCOPE_FAIL；
#   wm7 来的 #87 逐条 grep「VIOLATION 看护族「<键>」」与绿尾 ALARM_SCOPE_PASS。两路语义同源
#   （同一 red 列表、同一 rc），故输出侧做字样超集、消费侧一字不动——混合交付下改产出比改
#   五处断言少碰测试语义，红检（MUT*）不受影响。
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cc-team 根（自锚，宿主无关）
sys.path.insert(0, ROOT)
from ccards import engine, graph, statemachine   # noqa: E402

WATCH_RE = re.compile(r"^ALARM (\S+): ")
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def main():
    try:
        alarms = engine.tick().get("alarms_open") or []
        cards = graph.all_cards()
    except Exception as exc:
        print("SCOPE-CHECK-ERR: 账本/引擎读取失败（宁红不盲检）: %r" % exc, file=sys.stderr)
        return 2
    red, info, watch_seen = [], [], 0
    terminal = set(statemachine.TERMINAL)
    for msg in alarms:
        m = WATCH_RE.match(msg)
        if m:
            watch_seen += 1
            cid = m.group(1)
            st = (cards.get(cid) or {}).get("status") or "无卡"
            if st != "CLAIMED":
                # 「VIOLATION 看护族「<键>」」字样 = wm7 #87 红面逐条点名的 grep 契约；
                # msg[:60] 必含键首段（"ALARM <cid>: "），截断不影响前缀匹配。
                red.append("看护越界 VIOLATION 看护族「%s」涉及卡 %s 状态=%s（只 CLAIMED 该有看护警）"
                           % (msg[:60], cid, st))
            continue
        for tok in TOKEN_RE.findall(msg):
            node = cards.get(tok)
            if node and node.get("status") in terminal:
                info.append("台账类警提到终态卡(信息不判红): %s 状态=%s 键=%s"
                            % (tok, node.get("status"), msg[:100]))
                break
    for line in red:
        print("RED: " + line)
    for line in info:
        print("WARN: " + line)
    print("alarm_scope_check: alarms_open=%d 看护面=%d 违规=%d 信息=%d"
          % (len(alarms), watch_seen, len(red), len(info)))
    if red:
        print("SCOPE_FAIL")
        return 1
    print("SCOPE_OK")
    # ALARM_SCOPE_PASS = wm7 #87 绿面 grep 契约（与 SCOPE_OK 同义同刻，双字样并打）
    print("ALARM_SCOPE_PASS alarms_open=%d 看护面=%d 违规=0" % (len(alarms), watch_seen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
