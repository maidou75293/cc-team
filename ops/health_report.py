#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cc-team/ops/health_report.py — REQ-HEALTH-WEEKLY-1 系统健康度量日报
（一页推业主微信：六项口径，全部本地台账聚合，零新增模型调用）。

需求口径=业主信箱 seq=25（02:53 采纳+02:55 token 追加列），六项逐字对齐：
  1) 吞吐：当日 VERIFIED 数/派发数/在飞数
  2) 返工率：REJECT 数 ÷ 交卡片数（按周趋势）
  3) 漂移事故：僵尸双工/误杀/烂尾 DONE 事件计数
  4) 告警响应：alerts 未掐最老时长、报警→处置时长
  5) 夜间自动化：产出与误动作率（夜班环 rc 分布）
  6) token 成本归因：每卡 done 全程 token 估算、周报/巡检/心跳占比、
     REJECT 返工烧掉的 token
验收口径=数字与台账抽样核对一致（state/events.log + graph.json + verdict 落章 +
.cc/ledger token 账）；首版一天可出（脚本聚合即可，不建新系统）。

结构纪律：
- 定时=零 OS 定时器铁律：本脚本由 cc-ctrld `health` 每日环调用（at 对时+T6 锁
  惯例，REQ-FLEET-CTRLD-1 体系），本脚本自身不注册任何操作系统任务；
  AST 自检含系统定时任务字样扫描（下方词根拼接，防自匹配），引回 OS 定时器=自检红。
- 影子/live 闸同 recycle/fuse 姿势：缺省 dry 只报（stdout，零写文件零寄信），
  --apply 才落 reports/health-<day>.md 并追加推送块到 inbox/PM-REPLIES.md
  （主助理微信中转通道，fuse-S6 直达同通道）。
- 日级幂等：PM-REPLIES 已存在「## [健康日报] 机件自动 <day>」标记不再重发，
  --force-push 例外（键级单发防刷屏=S6 同款纪律）。
- 零模型调用双锁：①启动先 AST 自检——导入根必 ⊆ 纯本地白名单、禁
  os.system/popen/exec*/spawn* 系、禁网络库根；②同输入+同时钟（HEALTH_NOW
  注入）跑两遍字节一致——模型输出不可能逐字节复现，字节等=未走模型的实证。
测试缝（缺省=现网路径，供 scripts/e2e/health-weekly-test.py 沙箱）：
  CC_TEAM_HOME 台账家 ｜ HEALTH_LEDGER_DIR token 账目录（缺省=<代码根>/.cc/ledger）
  HEALTH_OUT 报告输出目录（缺省=<TEAM>/reports）
  HEALTH_INBOX 推送目标文件（缺省=<TEAM>/inbox/PM-REPLIES.md）
  HEALTH_CTRLD_LOG 环日志（缺省=<TEAM>/logs/ctrld.log）
  HEALTH_NOW 固定时钟（epoch 秒 或 ISO "+0800"）｜ HEALTH_NIGHT 夜间窗
  "HH:MM-HH:MM"（缺省 23:00-06:00：聚合日当晚 23:00 → 次日环收点 06:00）
"""
import argparse
import ast
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# ---------- 零模型调用自检（AST 面） ----------

ALLOWED_IMPORT_ROOTS = {"argparse", "ast", "collections", "datetime",
                        "hashlib", "json", "os", "re", "sys", "time"}
BANNED_IMPORT_ROOTS = {"subprocess", "socket", "ssl", "urllib", "http",
                       "requests", "anthropic", "openai", "ftplib", "smtplib",
                       "httpx", "grpc"}
BANNED_OS_CALLS = {("os", "system"), ("os", "popen")} | {
    ("os", n) for n in dir(os) if n.startswith("exec") or n.startswith("spawn")}
# 引回 OS 定时器=违零 OS 定时器铁律（业主 2026-11-03 令），文本面即红。
# 词根**拼接**构造（教训=记忆 fffd-literal-stripped 同族：扫描词的字面量若直接写在
# 本源码里，自检会把自己扫红——运行时才成词，源码永不自匹配）。
_BANNED_WORD_PIECES = (("sch", "tasks"), ("cron", "tab"),
                       ("Register-Scheduled", "Task"), ("New-Scheduled", "Task"))
BANNED_SOURCE_WORDS = tuple(x + y for x, y in _BANNED_WORD_PIECES)


def audit_source(text):
    """返回违规清单（空=干净）。给本器自检，也给红绿锁喂反面教材。"""
    viol = []
    for root in sorted(BANNED_IMPORT_ROOTS):
        if re.search(r"^\s*(?:import|from)\s+%s\b" % root, text, re.M):
            viol.append("BANNED-IMPORT %s" % root)
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        viol.append("PARSE-FAIL L%s" % e.lineno)
        tree = None
    for node in (ast.walk(tree) if tree is not None else []):
        if isinstance(node, ast.Import):
            for a in node.names:
                r = a.name.split(".")[0]
                if r not in ALLOWED_IMPORT_ROOTS:
                    viol.append("IMPORT-ROOT %s (L%d)" % (r, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            r = (node.module or "").split(".")[0]
            if r and r not in ALLOWED_IMPORT_ROOTS:
                viol.append("IMPORT-FROM %s (L%d)" % (r, node.lineno))
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id == "os" and (f.value.id, f.attr) in BANNED_OS_CALLS:
                viol.append("OS-CALL os.%s (L%d)" % (f.attr, node.lineno))
    for w in BANNED_SOURCE_WORDS:
        if w in text:
            viol.append("SOURCE-WORD %s" % w)
    return sorted(set(viol))


# ---------- 台账读取 ----------

def parse_ep(ts):
    """ISO "+0800" 或 "YYYY-MM-DD HH:MM:SS"（本地）→ epoch；解不出=None。"""
    for fmt, aware in (("%Y-%m-%dT%H:%M:%S%z", True), ("%Y-%m-%d %H:%M:%S", False)):
        try:
            dt = datetime.strptime(ts.strip(), fmt)
            return dt.timestamp() if aware else time.mktime(dt.timetuple())
        except ValueError:
            continue
    return None


def ep_day(ep):
    return datetime.fromtimestamp(ep).strftime("%Y-%m-%d")


def load_events(team):
    p = os.path.join(team, "state", "events.log")
    rows = []
    if os.path.isfile(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                q = line.rstrip("\n").split("|", 4)
                if len(q) < 5:
                    continue
                ts, cid, trans, actor, note = q
                ep = parse_ep(ts)
                if ep is None or "->" not in trans:
                    continue
                frm, _, to = trans.partition("->")
                rows.append({"ep": ep, "day": ts[:10], "cid": cid, "trans": trans,
                             "frm": frm, "to": to, "actor": actor, "note": note})
    return rows, p


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_ledger_rows(ledger_dir, day):
    rows = []
    p = os.path.join(ledger_dir, "ledger-%s.jsonl" % day)
    if os.path.isfile(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("day") == day and isinstance(r.get("ts"), (int, float)):
                    rows.append(r)
    return rows, p


# ---------- 六项口径（规则即注释，数字必可抽账复算） ----------

# 僵尸双工形：死道占卡/偷道回收/旧任期续拍（spawn-death、lane-steal、EPOCH-STALE）
def is_zombie(r):
    return r["actor"] in ("spawn-death", "lane-steal") or "EPOCH-STALE" in r["note"] \
        or "僵尸" in r["note"]


# 误杀形：生效锁错杀/拒自杀环/自动重启（FUSE-S4 族）与明写「误杀」
def is_misfire(r):
    return any(k in r["note"] for k in ("NOT-ENERGIZED", "REFUSE-SELFKILL",
                                        "FUSE-RESTART", "误杀"))


REJECT_TRANS = ("DONE->OPEN", "VERIFIED->OPEN")   # 交卷后被驳回（=返工）
MACHINE_ACTORS = {"verifier", "recycle", "mergetrain", "fuse", "dispatcher",
                  "inbox", "guard", "ctrld", "reconcile", "-"}
# 协调/维护道（巡检唤醒的 PM/ops 会话所在 lane，token 占比口径用；机件本身=0）
META_LANES = {"dataworks-ai", "ops", "pm", "pmmail", "pm-live", "wops"}
LANE_ALIAS = {"PM": "dataworks-ai", "pm": "dataworks-ai", "pm-live": "dataworks-ai",
              "wops": "ops", "dispatcher": "ops"}


def median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def metric_throughput(rows, graph, day):
    c = Counter(r["trans"] for r in rows if r["day"] == day)
    inflight = None
    if isinstance(graph, dict):
        cards = graph.get("cards") or {}
        inflight = sum(1 for v in cards.values() if v.get("status") == "CLAIMED")
    return {"verified": c["DONE->VERIFIED"], "dispatched": c["OPEN->CLAIMED"],
            "submitted": c["CLAIMED->DONE"], "inflight": inflight}


def metric_rework(rows, day, window_days=7):
    base = datetime.strptime(day, "%Y-%m-%d")
    trend = []
    for i in range(window_days - 1, -1, -1):
        d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
        sub = sum(1 for r in rows if r["day"] == d and r["trans"] == "CLAIMED->DONE")
        rej = sum(1 for r in rows if r["day"] == d and r["trans"] in REJECT_TRANS)
        trend.append({"day": d, "submitted": sub, "reject": rej})
    today = trend[-1]
    return {"reject": today["reject"], "submitted": today["submitted"],
            "trend": trend}


def metric_drift(rows, day):
    inday = [r for r in rows if r["day"] == day]
    zombie = [r for r in inday if is_zombie(r)]
    misfire = [r for r in inday if is_misfire(r)]
    stale_rows = [r for r in inday if (r["trans"] == "DONE->DONE" and "验收" in r["note"])
                  or "REDCARD-STALE" in r["note"]]
    stale_cards = sorted({r["cid"] for r in stale_rows})
    return {"zombie": len(zombie), "misfire": len(misfire),
            "stale_cards": len(stale_cards), "stale_events": len(stale_rows)}


def metric_alerts(alerts_obj, day, now_ep):
    out = {"new": 0, "acked": 0, "median_min": None, "max_min": None,
           "open": 0, "oldest_open_min": None}
    if not isinstance(alerts_obj, dict):
        return out
    durs = []
    firsts_open = []
    for _k, a in (alerts_obj.get("alerts") or {}).items():
        f_ep = parse_ep(a.get("first") or "")
        if f_ep is None:
            continue
        if ep_day(f_ep) == day:
            out["new"] += 1
        ack_raw = a.get("ack") or ""
        a_ep = parse_ep(ack_raw) if ack_raw else None
        if a_ep is not None:
            if ep_day(a_ep) == day:
                out["acked"] += 1
                durs.append((a_ep - f_ep) / 60.0)
        else:
            out["open"] += 1
            firsts_open.append(f_ep)
    if durs:
        out["median_min"] = round(median(durs), 1)
        out["max_min"] = round(max(durs), 1)
    if firsts_open and now_ep is not None:
        out["oldest_open_min"] = round((now_ep - min(firsts_open)) / 60.0, 1)
    return out


def night_window(day, spec="23:00-06:00"):
    lo_s, hi_s = spec.split("-")
    lo = time.mktime(time.strptime("%s %s:00" % (day, lo_s), "%Y-%m-%d %H:%M:%S"))
    nxt = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    hi = time.mktime(time.strptime("%s %s:00" % (nxt, hi_s), "%Y-%m-%d %H:%M:%S"))
    return lo, hi


def metric_night(rows, ctrld_log, day, spec):
    lo, hi = night_window(day, spec)
    inwin = [r for r in rows if lo <= r["ep"] < hi]
    mach = [r for r in inwin if r["actor"] in MACHINE_ACTORS]
    merged = sum(1 for r in inwin if r["note"].startswith("FUSE-S3-MERGED"))
    rc = Counter()
    if os.path.isfile(ctrld_log):
        with open(ctrld_log, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) RING \S+ rc=(-?\d+)", line)
                if not m:
                    continue
                ep = parse_ep(m.group(1))
                if ep is not None and lo <= ep < hi:
                    rc[int(m.group(2))] += 1
    total = sum(rc.values())
    mis = sum(v for k, v in rc.items() if k != 0)
    return {"events": len(inwin), "machine_events": len(mach),
            "verified": sum(1 for r in inwin if r["trans"] == "DONE->VERIFIED"),
            "merged": merged, "ring_total": total,
            "ring_rc": {str(k): v for k, v in sorted(rc.items())},
            "misfire_rate": (round(100.0 * mis / total, 1) if total else None)}


def _sum_lane(lane_rows, a, b):
    return sum(r.get("total", 0) for r in lane_rows if a <= r["ts"] < b)


def metric_token(rows, ledger_rows, day, top_n=5):
    by_lane = defaultdict(list)
    for r in ledger_rows:
        by_lane[r.get("lane") or "unknown"].append(r)
    total = sum(r.get("total", 0) for r in ledger_rows)
    meta = sum(r.get("total", 0) for r in ledger_rows
               if (r.get("lane") or "") in META_LANES)
    claims = defaultdict(list)
    submits = defaultdict(list)
    rejects = defaultdict(list)
    for r in rows:
        if r["trans"] == "OPEN->CLAIMED":
            claims[r["cid"]].append(r)
        elif r["trans"] == "CLAIMED->DONE":
            submits[r["cid"]].append(r)
        elif r["trans"] in REJECT_TRANS:
            rejects[r["cid"]].append(r)
    cards = []
    for cid, subs in submits.items():
        for s in subs:
            if s["day"] != day:
                continue
            prior = [c for c in claims[cid] if c["ep"] <= s["ep"]]
            est, lane = None, LANE_ALIAS.get(s["actor"], s["actor"])
            if prior:
                lo = max(c["ep"] for c in prior)
                est = _sum_lane(by_lane.get(lane, []), lo, s["ep"])
            cards.append({"cid": cid, "lane": lane, "est": est})
    cards.sort(key=lambda x: (-(x["est"] if x["est"] is not None else -1), x["cid"]))
    reject_burn = 0
    rejectable = 0
    for cid, rj in rejects.items():
        for x in rj:
            if x["day"] != day:
                continue
            sub = [s for s in submits[cid] if s["ep"] <= x["ep"]]
            prior = [c for c in claims[cid] if c["ep"] <= sub[-1]["ep"]] if sub else []
            if not (sub and prior):
                rejectable += 1
                continue
            lane = LANE_ALIAS.get(sub[-1]["actor"], sub[-1]["actor"])
            reject_burn += _sum_lane(by_lane.get(lane, []), max(c["ep"] for c in prior), x["ep"])
    return {"total": total, "lines": len(ledger_rows),
            "top_cards": cards[:top_n],
            "reject_burn": reject_burn, "reject_unestimable": rejectable,
            "meta_share": (round(100.0 * meta / total, 1) if total else None),
            "meta_tokens": meta}


# ---------- 渲染（一页） ----------

def fmt_pct(v):
    return "—" if v is None else ("%.1f%%" % v)


def render(day, now_ep, m, srcs):
    t, rw, dr, al, ni, tk = m["throughput"], m["rework"], m["drift"], m["alerts"], m["night"], m["token"]
    L = []
    L.append("# cc-team 健康日报 %s" % day)
    L.append("生成 %s ｜ 聚合器 ops/health_report.py（纯本地台账，零模型调用）"
             % datetime.fromtimestamp(now_ep).strftime("%Y-%m-%d %H:%M:%S"))
    L.append("")
    L.append("## 1) 吞吐")
    L.append("- 当日 VERIFIED=**%d** ｜ 派发(OPEN→CLAIMED)=**%d** ｜ 交卷(CLAIMED→DONE)=**%d**"
             % (t["verified"], t["dispatched"], t["submitted"]))
    L.append("- 在飞(CLAIMED，生成时刻 graph.json 快照)=**%s**"
             % ("—" if t["inflight"] is None else t["inflight"]))
    L.append("")
    L.append("## 2) 返工率")
    rate = (100.0 * rw["reject"] / rw["submitted"]) if rw["submitted"] else None
    L.append("- 当日 REJECT=**%d** ÷ 交卷=**%d** = **%s**" % (rw["reject"], rw["submitted"], fmt_pct(rate)))
    tr = "，".join("%s:%d/%d" % (x["day"][5:], x["reject"], x["submitted"]) for x in rw["trend"])
    L.append("- 近 7 日趋势(返工/交卷)：%s" % tr)
    L.append("")
    L.append("## 3) 漂移事故")
    L.append("- 僵尸双工=**%d** ｜ 误杀=**%d** ｜ 烂尾 DONE=**%d** 卡（%d 行）"
             % (dr["zombie"], dr["misfire"], dr["stale_cards"], dr["stale_events"]))
    L.append("  口径：僵尸=spawn-death/lane-steal/EPOCH-STALE 行；误杀=NOT-ENERGIZED/"
             "REFUSE-SELFKILL/FUSE-RESTART 行；烂尾=当日 DONE 态验收重试/REDCARD-STALE 去重卡数")
    L.append("")
    L.append("## 4) 告警响应")
    L.append("- 新增=**%d** ｜ 当日已掐=**%d**（处置时长 中位 **%s** min / 最长 **%s** min）"
             % (al["new"], al["acked"],
                "—" if al["median_min"] is None else al["median_min"],
                "—" if al["max_min"] is None else al["max_min"]))
    L.append("- 未掐=**%d** ｜ 未掐最老=**%s** min" % (al["open"],
             "—" if al["oldest_open_min"] is None else al["oldest_open_min"]))
    L.append("")
    L.append("## 5) 夜间自动化（%s）" % m["night_spec"])
    L.append("- 机件事件=**%d** ｜ 夜间 VERIFIED=**%d** ｜ 自动并线(S3-MERGED)=**%d**"
             % (ni["machine_events"], ni["verified"], ni["merged"]))
    L.append("- 夜班环 rc 分布=**%s**（共 %d 次）｜ 误动作率=**%s**"
             % (json.dumps(ni["ring_rc"], ensure_ascii=False), ni["ring_total"],
                "无样本" if ni["misfire_rate"] is None else fmt_pct(ni["misfire_rate"])))
    L.append("")
    L.append("## 6) token 成本归因（.cc/ledger 当日账）")
    L.append("- 当日总 token=**%s**（%d 行增量账）" % (format(tk["total"], ","), tk["lines"]))
    if tk["top_cards"]:
        seg = "，".join("%s=%s" % (c["cid"], "不可估" if c["est"] is None
                                     else format(c["est"], ",")) for c in tk["top_cards"])
        L.append("- 每卡 done 全程估算 top%d（认领窗→交卷窗内该道 token 和）：%s"
                 % (len(tk["top_cards"]), seg))
    L.append("- REJECT 返工烧掉 token≈**%s**%s"
             % (format(tk["reject_burn"], ","),
                ("（另有 %d 次返工不可估）" % tk["reject_unestimable"])
                if tk["reject_unestimable"] else ""))
    L.append("- 协调/维护面（巡检唤醒的 PM·ops 会话 lane）占比≈**%s**（%s token）；"
             "**本报与推送本身=0 token（零模型调用，AST+双跑字节一致双锁）**"
             % (fmt_pct(tk["meta_share"]), format(tk["meta_tokens"], ",")))
    L.append("")
    L.append("---")
    miss = [k for k, v in srcs.items() if not v]
    L.append("数据源：%s%s" % ("、".join(sorted(srcs)),
                              "；缺=%s" % "、".join(miss) if miss else ""))
    L.append("metrics-digest=%s" % hashlib.sha256(
        json.dumps(m, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:16])
    return "\n".join(L) + "\n"


def headline(m):
    t, rw, dr, al, ni, tk = m["throughput"], m["rework"], m["drift"], m["alerts"], m["night"], m["token"]
    rate = (100.0 * rw["reject"] / rw["submitted"]) if rw["submitted"] else None
    return ("吞吐 VERIFIED=%d/派发=%d/在飞=%s；返工率=%s(REJECT %d÷交卷 %d)；漂移 僵尸%d/误杀%d/烂尾%d；"
            "告警 未掐%d(最老%s min) 处置中位%s min；夜间 机件%d环次%d 误动作%s；"
            "token 总%s 返工烧%s 协调占比%s"
            % (t["verified"], t["dispatched"],
               "—" if t["inflight"] is None else t["inflight"],
               fmt_pct(rate), rw["reject"], rw["submitted"],
               dr["zombie"], dr["misfire"], dr["stale_cards"],
               al["open"], "—" if al["oldest_open_min"] is None else al["oldest_open_min"],
               "—" if al["median_min"] is None else al["median_min"],
               ni["machine_events"], ni["ring_total"],
               "—" if ni["misfire_rate"] is None else fmt_pct(ni["misfire_rate"]),
               format(tk["total"], ","), format(tk["reject_burn"], ","),
               fmt_pct(tk["meta_share"])))


# ---------- 主程 ----------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="health_report.py",
                                 description="REQ-HEALTH-WEEKLY-1 健康日报聚合器（零模型调用）")
    ap.add_argument("--day", default=None, help="聚合日 YYYY-MM-DD（缺省=昨天，早间出报口径）")
    ap.add_argument("--apply", action="store_true", help="落报告文件+追加微信推送块（缺省 dry 只报）")
    ap.add_argument("--force-push", action="store_true", help="当日块已在信箱也不查重发（默认幂等跳过）")
    a = ap.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, os.path.basename(__file__)), encoding="utf-8") as f:
        src = f.read()
    viol = audit_source(src)
    if viol:
        print("AUDIT=FAIL 零模型调用自检违规：%s" % "; ".join(viol))
        return 3

    now_ep = None
    hv = os.environ.get("HEALTH_NOW")
    if hv:
        now_ep = parse_ep(hv) if not hv.isdigit() else float(hv)
    if now_ep is None:
        now_ep = time.time()
    day = a.day or (datetime.fromtimestamp(now_ep) - timedelta(days=1)).strftime("%Y-%m-%d")

    team = os.environ.get("CC_TEAM_HOME") or os.path.dirname(os.path.dirname(here))
    code_root = os.path.dirname(os.path.dirname(team))
    ledger_dir = os.environ.get("HEALTH_LEDGER_DIR") or os.path.join(code_root, ".cc", "ledger")
    out_dir = os.environ.get("HEALTH_OUT") or os.path.join(team, "reports")
    inbox_p = os.environ.get("HEALTH_INBOX") or os.path.join(team, "inbox", "PM-REPLIES.md")
    ctrld_log = os.environ.get("HEALTH_CTRLD_LOG") or os.path.join(team, "logs", "ctrld.log")
    night_spec = os.environ.get("HEALTH_NIGHT", "23:00-06:00")

    rows, events_p = load_events(team)
    graph = load_json(os.path.join(team, "state", "graph.json"))
    alerts = load_json(os.path.join(team, "state", "alerts.json"))
    ledger_rows, ledger_p = load_ledger_rows(ledger_dir, day)

    m = {
        "throughput": metric_throughput(rows, graph, day),
        "rework": metric_rework(rows, day),
        "drift": metric_drift(rows, day),
        "alerts": metric_alerts(alerts, day, now_ep),
        "night": metric_night(rows, ctrld_log, day, night_spec),
        "night_spec": night_spec,
        "token": metric_token(rows, ledger_rows, day),
    }
    srcs = {"events.log": os.path.isfile(events_p), "graph.json": graph is not None,
            "alerts.json": alerts is not None, os.path.basename(ledger_p): bool(ledger_rows),
            "ctrld.log": os.path.isfile(ctrld_log)}
    text = render(day, now_ep, m, srcs)
    print(text, end="")

    if not a.apply:
        print("HEALTH_DRY day=%s（未落盘未寄信；--apply 才动）" % day)
        return 0

    os.makedirs(out_dir, exist_ok=True)
    out_p = os.path.join(out_dir, "health-%s.md" % day)
    with open(out_p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    marker = "## [健康日报] 机件自动 %s" % day
    pushed = False
    old = ""
    if os.path.isfile(inbox_p):
        with open(inbox_p, encoding="utf-8") as f:
            old = f.read()
    if a.force_push or marker not in old:
        block = ("\n%s（REQ-HEALTH-WEEKLY-1 · 主助理请转微信）\n%s\n全文：%s（metrics 可抽账复算）\n"
                 % (marker, headline(m), os.path.relpath(out_p, team)))
        with open(inbox_p, "a", encoding="utf-8", newline="\n") as f:
            f.write(block)
        pushed = True
    print("HEALTH_REPORT day=%s out=%s push=%s digest=%s"
          % (day, out_p, "1" if pushed else "0(幂等跳过)",
             hashlib.sha256(json.dumps(m, sort_keys=True, ensure_ascii=False,
                                       default=str).encode("utf-8")).hexdigest()[:16]))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
