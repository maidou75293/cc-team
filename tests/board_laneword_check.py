#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_laneword_check.py — 道栏文案常驻桩锁（REQ-BOARD-LANEWORD-1，业主 2026-11-02 10:5x
「为什么一个卡有3个道」——属主与候选白名单连排 m1 / m7,m1 易误读，拆 在制道/候补道 两段语义）。

靶子两层：
  ① status-web.py 服务端心跳区 live_html——合成账真跑：在制=owner、候补=lanes 去 owner 带
     「先到先得，领走即出局」括注、无候补不出候补段、表头「在制道·候补道」、判读补语义行；
     红自证=撤候补段/摘剔除逻辑/回退表头，本锁必红。
  ② live.js 客户端（台账+执行详情弹窗）——经 node 跑 board_laneword_dom_check.mjs（独立 boot，
     不碰别道在改的 #69 board_dom_check.mjs），含其自身两路红自证。

跑法: python3 tests/board_laneword_check.py → stdout 末行 BOARD_LANEWORD_PASS n=N 即绿；任何 FAIL → exit 1。
selftest.sh #74 挂载本文件。node 缺失=判红（锁不许空枪）。"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "board", "status-web.py")
JS = os.path.join(HERE, "..", "board", "live.js")
MJS = os.path.join(HERE, "board_laneword_dom_check.mjs")

n_assert = 0
fails = []


def ok(cond, msg):
    global n_assert
    n_assert += 1
    if not cond:
        fails.append(msg)
        print("FAIL: " + msg, file=sys.stderr)
    else:
        print("ok: " + msg, file=sys.stderr)


def load_ns(mutate=None):
    """exec 模块源码取渲染函数（不启动 __main__ 服务）；mutate(源码) 用于红自证，替换未命中必报错。"""
    src = open(SW, encoding="utf-8").read()
    if mutate:
        new = mutate(src)
        if new == src:
            raise RuntimeError("红自证替换未命中（live_html 结构漂移，锁需同步）")
        src = new
    ns = {"__name__": "status_web_under_test", "__file__": os.path.abspath(SW)}
    argv_save = sys.argv
    sys.argv = ["status-web.py"]
    try:
        exec(compile(src, SW, "exec"), ns)
    finally:
        sys.argv = argv_save
    return ns


CARDS = [
    {"id": "LW-A", "status": "CLAIMED", "owner": "m1", "lanes": ["m7", "m1"], "title": "带候补卡",
     "created_at": "2026-11-02T10:00:00+08:00", "first_claimed_at": "2026-11-02T10:00:00+08:00"},
    {"id": "LW-B", "status": "CLAIMED", "owner": "m2", "lanes": ["m2"], "title": "独道无候补",
     "created_at": "2026-11-02T10:00:00+08:00", "first_claimed_at": "2026-11-02T10:00:00+08:00"},
]


def render(mutate=None):
    """合成账真跑 live_html 并按页面出口链 mask+decorate_lanes（角色后缀在此注入）。"""
    ns = load_ns(mutate)
    ns["_ledger_cards"] = lambda T: list(CARDS)
    out = ns["live_html"]({})
    return ns["decorate_lanes"](ns["mask"](out))


# 注：断言按 `>+?` 容忍——历史上 decorate_lanes 有尾缀杂符怪癖（`</b>>`），本卡已修；
#     锁不耦合怪癖有无，只锁文案语义（怪癖回归或再度修复都不该把本锁带红）。
CAND_HAS_OWNER = re.compile(r"候补 (<b>[^<]+</b>+ *)*<b>m1")

h = render()
ok("<th>在制道·候补道</th>" in h, "心跳表头已拆两段语义（不再叫「属主·道」含糊连排）")
ok(re.search(r"候补 <b>m7·[^<]+</b>+?（先到先得，领走即出局）", h), "LW-A 候补段=m7+出局语义（角色后缀经出口装饰在位）")
ok("<b>m1·后端开发</b>" in h, "在制道装饰在位（m1·后端开发）")
ok(not CAND_HAS_OWNER.search(h) and not re.search(r"m1\s*/\s*m7", h) and "m7,m1" not in h,
   "在制道不回现候补段、无 m1 / m7,m1 连排（心跳区）")
ok(h.count("领走即出局）</span>") == 1, "候补段只随真候补出现——LW-B 无候补不显示空壳")
ok("在制道=已领走该卡的道" in h, "判读写死两栏语义（业主可自查，不靠口口相传）")
ok("<b>m2·用户端开发</b>" in h, "LW-B 行在制道仍在位（无候补≠整栏消失）")

# ---- 红自证①：撤候补段文案 → 本锁必红（防止文案被无感回退） ----
try:
    h2 = render(lambda s: s.replace("候补 %s（先到先得，领走即出局）", "%s"))
    ok("候补 <b>" not in h2 and h2.count("领走即出局）</span>") == 0,
       "红自证①候补段撤掉即查得到（判红路径真实存在）")
except RuntimeError as e:
    ok(False, "红自证①替换未命中: %s" % e)

# ---- 红自证②：候补不剔在制道 → 在制道混进候补段（=「一个卡3个道」复发）必须被上面断言抓到 ----
try:
    h3 = render(lambda s: s.replace('if str(x) != ln]', ']'))
    ok(CAND_HAS_OWNER.search(h3), "红自证②剔除逻辑摘掉即复现（『在制道不回现候补段』不是空枪）")
except RuntimeError as e:
    ok(False, "红自证②替换未命中: %s" % e)

# ---- 红自证③：表头回退「属主·道」→ 表头断言必须能红 ----
try:
    h4 = render(lambda s: s.replace("<th>在制道·候补道</th>", "<th>属主·道</th>"))
    ok("<th>属主·道</th>" in h4 and "<th>在制道·候补道</th>" not in h4,
       "红自证③表头回退即查得到（回退旧文案不会静默）")
except RuntimeError as e:
    ok(False, "红自证③替换未命中: %s" % e)

# ---- 台账静态表头（get_body 内常量，出口页源码级锁） ----
sw_src = open(SW, encoding="utf-8").read()
ok("<th>候补道</th>" in sw_src and "<th>属主</th><th>候补道</th>" in sw_src, "台账表头=属主+候补道两段命名")

# ---- ② 客户端层：node 真跑 live.js（台账/弹窗/两路红自证） ----
try:
    p = subprocess.run(["node", MJS, JS], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    tail = (p.stdout or "").strip().splitlines()
    ok(bool(tail) and tail[-1].startswith("BOARD_LW_DOM_PASS"),
       "live.js 客户端锁全绿（%s）" % (tail[-1] if tail else "node 无输出 rc=%s" % p.returncode))
    if tail and not tail[-1].startswith("BOARD_LW_DOM_PASS"):
        for ln in (p.stderr or "").splitlines()[-8:]:
            print("  dom> " + ln, file=sys.stderr)
except FileNotFoundError:
    ok(False, "node 不在 PATH——客户端层没跑=空枪，判红（selftest 宿主机必带 node，#69 同源）")
except subprocess.TimeoutExpired:
    ok(False, "node 客户端锁超时")

print("board_laneword_check: asserts=%d fails=%d" % (n_assert, len(fails)), file=sys.stderr)
if fails:
    print("BOARD_LANEWORD_FAIL")
    sys.exit(1)
print("BOARD_LANEWORD_PASS n=%d" % n_assert)
