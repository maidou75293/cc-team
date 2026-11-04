#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_trend_check.py — 心跳区 24h 逐时趋势图常驻桩锁（MECH-BOARD-TREND-1，业主 2026-11-02 规格）。

靶子=cc-team/board/status-web.py 的服务端渲染层（hourly_trend_html + live_html 接线）。
与 #69 board_dom_check.mjs 同一纪律：新渲染层落地当轮上常驻锁；必带红自证（撤接线确认本锁红得起来）。
合成账对抗点：坏时间串、恰在 24 格边界外一格、verdict.at 对 updated_at 的优先级、
开工/收口落不同小时桶的逐格对账（从 hover title 反读计数，不数 SVG 笔画——笔画会重构、桶义不会）。

跑法: python3 tests/board_trend_check.py   → stdout 末行 BOARD_TREND_PASS n=N 即绿；任何 FAIL → exit 1。
selftest.sh #71 挂载本文件。"""
import datetime
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "..", "board", "status-web.py")
BJ = datetime.timezone(datetime.timedelta(hours=8))

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


def load_ns(path, mutate=None):
    """exec 模块源码取函数（不启动 __main__ 服务）；mutate(源码) 用于红自证。"""
    src = open(path, encoding="utf-8").read()
    if mutate:
        new = mutate(src)
        if new == src:
            raise RuntimeError("红自证替换未命中（live_html/hourly_trend_html 结构漂移，锁需同步）")
        src = new
    ns = {"__name__": "status_web_under_test", "__file__": os.path.abspath(path)}
    argv_save = sys.argv
    sys.argv = ["status-web.py"]                      # PORT 读 argv[1]——钉死避免被测试参数带崩
    try:
        exec(compile(src, path, "exec"), ns)
    finally:
        sys.argv = argv_save
    return ns


def iso(ep):
    return datetime.datetime.fromtimestamp(ep, BJ).isoformat()


# ---- 合成账：now 钉在本小时中段，24 格=[h0-23 … h0] ----
ns_mod = load_ns(SW)
h0 = int(time.time()) // 3600
CARDS = [
    {"id": "T-A", "status": "CLAIMED", "first_claimed_at": iso(h0 * 3600 + 60)},                 # 开工→最后一格
    {"id": "T-B", "status": "DONE", "first_claimed_at": iso((h0 - 5) * 3600),
     "output": {"done_at": iso((h0 - 1) * 3600 + 30)}},                                          # 开工→h0-5 格, 收口→h0-1 格
    {"id": "T-C", "status": "VERIFIED", "first_claimed_at": iso((h0 - 30) * 3600),              # 开工在窗外(30h前)
     "output": {"done_at": iso(h0 * 3600 - 23 * 3600)}},                                         # 收口→最左格 h0-23
    {"id": "T-D", "status": "VERIFIED", "first_claimed_at": "x",                                 # 坏串=不落桶不炸页
     "updated_at": iso((h0 - 2) * 3600)},                                                        # 收口兜底 updated_at→h0-2 格
    {"id": "T-E", "status": "CLAIMED", "claimed_at": iso((h0 - 24) * 3600)},                     # 恰在窗外一格(24h边界)
    {"id": "T-F", "status": "VERIFIED", "verdict": {"at": iso((h0 - 3) * 3600 + 9).replace("+08:00", "+0800")},  # 紧凑 +0800 解析 + verdict.at 优先
     "updated_at": iso((h0 - 100) * 3600)},
]
EXP_S = {23: 1, 18: 1}                                        # 逐格开工
EXP_D = {0: 1, 20: 1, 21: 1, 22: 1}                           # 逐格收口

# ---- 1) 共用口径函数（同函数同口径的「函数」本体不许被悄悄改坏） ----
ok(ns_mod["_claimstr"]({"first_claimed_at": "A", "claimed_at": "B"}) == "A", "_claimstr: first_claimed_at 优先")
ok(ns_mod["_claimstr"]({"claimed_at": "B"}) == "B", "_claimstr: 缺 first 落 claimed_at")
ok(ns_mod["_donestr"]({"verdict": {"at": "V"}, "output": {"done_at": "O"}, "status": "VERIFIED", "updated_at": "U"}) == "V",
   "_donestr: verdict.at 优先")
ok(ns_mod["_donestr"]({"output": {"done_at": "O"}, "status": "VERIFIED", "updated_at": "U"}) == "O", "_donestr: done_at 次之")
ok(ns_mod["_donestr"]({"status": "DONE", "updated_at": "U"}) == "U", "_donestr: DONE 兜底 updated_at")
ok(ns_mod["_donestr"]({"status": "CLAIMED", "updated_at": "U"}) == "", "_donestr: 未收口=空串不落桶")

# ---- 2) 逐时图：24 格、右端=现在、逐格计数与合成账一致（从 hover title 反读） ----
out = ns_mod["hourly_trend_html"](CARDS, now=h0 * 3600 + 1800)
titles = re.findall(r"(\d\d-\d\d) (\d+)时 · 开工 (\d+) · 收口 (\d+)", out)
ok(len(titles) == 24, "图在场且横轴恰 24 格（hover 槽=24）got=%d" % len(titles))
got_s, got_d = {}, {}
for i, (_d, _h, s, dn) in enumerate(titles):
    if int(s):
        got_s[i] = int(s)
    if int(dn):
        got_d[i] = int(dn)
ok(got_s == EXP_S, "开工逐桶对账 %s got=%s" % (sorted(EXP_S.items()), sorted(got_s.items())))
ok(got_d == EXP_D, "收口逐桶对账 %s got=%s" % (sorted(EXP_D.items()), sorted(got_d.items())))
ok(out.rstrip().endswith("</div>") and "<svg" in out, "内联 SVG 闭合")
ok(not re.search(r"src=|href=|https?://", out), "零外链（离线主机禁 CDN）")
ok("现在" in out, "右端标注=现在")
ok(out.count("<polyline") == 1, "收口=单折线（双色柱+线形状冗余编码）")
pts = re.search(r"<polyline points='([^']+)'", out)
ok(pts and len(pts.group(1).split()) == 24, "折线 24 点=24 小时不缺格")

# ---- 3) 空账诚实态：图在场、文案如实，不炸 ----
empty = ns_mod["hourly_trend_html"]([], now=h0 * 3600 + 1)
ok("近 24 小时账本无领取/已验收入账" in empty and "data-trend24" in empty, "空账=图在场+如实空态（比没有检查强）")

# ---- 4) live_html 接线：SNAP 正下方、表格未动、无在制卡也出图 ----
SW_NS = ns_mod
SW_NS["_ledger_cards"] = lambda T: CARDS
SW_NS["PROJ"] = "/nonexistent-test-proj"                          # 会话 jsonl 找不到→心跳列走「无会话」，不触真实文件
page = SW_NS["live_html"]({})
i_snap, i_trend, i_tbl = page.find("今日快照"), page.find("data-trend24"), page.find("<table")
ok(i_snap >= 0, "接线：页顶今日快照仍在")
ok(i_trend > i_snap, "接线：趋势图位于 SNAP 正下方")
ok(i_tbl < 0 or i_trend < i_tbl, "接线：趋势图在现有表格之前（表格本体未动）")
ok(page.count("data-trend24") == 1, "接线：一页一幅 24h 图（不重复渲染）")

# ---- 5) 红自证：撤掉接线 → 本锁必红（锁不是空枪，#69 血泪规矩） ----
try:
    ns2 = load_ns(SW, mutate=lambda s: s.replace("TREND = hourly_trend_html(cards_all, now)", 'TREND = ""'))
    ns2["_ledger_cards"] = lambda T: CARDS
    ns2["PROJ"] = "/nonexistent-test-proj"
    page2 = ns2["live_html"]({})
    ok("data-trend24" not in page2, "红自证：撤 hourly_trend_html 接线后页面无图（本锁会红）")
except RuntimeError as e:
    ok(False, "红自证替换命中检查: %s" % e)

print("board_trend_check: asserts=%d fails=%d" % (n_assert, len(fails)), file=sys.stderr)
if fails:
    print("BOARD_TREND_FAIL", )
    sys.exit(1)
print("BOARD_TREND_PASS n=%d" % n_assert)
