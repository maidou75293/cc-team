#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REQ-NIGHT-LEARN-1 岗位夜间学习引擎（cc-ctrld 夜环 postlearn 的唯一入口）。

口径（PRD《夜间学习-草案-20261104》C 段 + 业主信 seq=23③/seq=24④c，卡面 src 同源）：
- **岗位=标注项目**（1 标注项目=1 岗位）。逐岗在自身夜间窗（默认 00:00–06:00）自动跑**一圈** FlywheelRun，
  复用现成七步编排（TABLE→ANNOTATE→TRAIN→EVAL→DEPLOY→BADCASE→RELABEL），不另造状态机。
- **闸门夜间强制 AUTO**（=全自动+晨复核）：
  - ANNOTATE 闸：直接放行（夜间没人，等同 AUTO）；
  - EVAL 闸：按**岗位配置** `eval_min_score` 判分——分够放行、分不够**不放行**，停在闸上等晨复核
    （AUTO≠无条件放行，与后端 OrchestratorSettings 口径一致）。
  分从哪来：run 的 EVAL GATE 事件 metrics.score；后端已升 night 语义时自己就会按岗位线放行/停闸，
  本环读同一事件做的是**同一判据**（两侧同式，老后端没升也不会把达标的圈误判成异常）。
- **晨报**：每岗一份「夜间学习报告」=圈数/通过数/badcase TOP/新模型版本清单，**异常圈单列**
  （评测不达标、训练/圈失败、超窗未终、开窗未起圈、配置非法、接口不可达）。
  本机落 `晨报-<date>.md` + `night-<date>.json` 台账；平台侧经 `POST /api/flywheel/night/report`
  进主页聚合（点进详情=平台报告行）。
- **经验→SOP 提炼夜环**：同一次 tick 顺拍（**共用调度**，不另立定时器），草案进
  `agent-service/playbooks-draft/`，过 `playbook.validate` 防腐锁才落盘；现役 `playbooks/` 本环永不写。
- **零 OS 定时器**（业主 2026-11-03 令）：本脚本不自带定时器，由 cc-ctrld 时间轮每小时一拍
  （环名 `postlearn`，`at` 对时到 00:05 起）。窗外空转=一行 tail，不开圈不出报。
- **幂等**：每岗每夜只开一圈、每夜每岗只出一份报、每夜只提炼一次——状态账
  `state/night-learn/night-<YYYYMMDD>.json` 是唯一事实，重启/补拍都不会重复开圈。

命名：与看板⑱「学习监督夜巡」（旧 `night-learn` 系统定时行，管的是**泳道**学习）不是一回事——
本环管**业务岗位**（标注项目）夜学，故环名 `postlearn`、文件名 `night_post_learn.py`，避免串名。

用法：
    python -I cc-team/ops/night_post_learn.py --apply [--now 2026-11-04T02:00:00] [--json]
    python -I cc-team/ops/night_post_learn.py --dry          # 影子：只点名，不开圈不写账
环境变量：
    NIGHT_LEARN_BASE   平台入口（优先于账本根 config.toml [platform] api_base；两者皆缺=内置默认
                       =本机开发形态占位。现网部署地址是定制项，只落 main 仓 cc-team/config.toml，
                       不入发布包——单一入口见 ccards/config.py CC_PLATFORM_BASE）
    NIGHT_LEARN_TOKEN  平台 Bearer 口令；缺省则用 DW_USER/DW_PASS 走 /api/auth/login 现取
    NIGHT_LEARN_STATE  状态根（默认 <CC_TEAM_HOME|cc-team>/state/night-learn）
    NIGHT_LEARN_NOW    注入时钟（ISO 本地时间，判据/演练用；生产不设）
    NIGHT_LEARN_NO_SOP=1  跳过提炼段（只跑圈与晨报）
    AGENT_EXPERIENCE_DIR / SOP_PLAYBOOK_DIR  交 agent-service 侧默认口径（本环不覆写生产目录）
退出码：0=本拍正常（含窗外空转）｜1=有异常圈或异常岗（已如实入报，不是崩溃）｜2=缺凭证/平台不可达（未出报）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

try:                                    # 控制台GBK打不出中文=降级重打安全版，绝不断环
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))          # cc-team/ops
ROOT = os.path.dirname(HERE)                                # cc-team 根
sys.path.insert(0, ROOT)                                    # 让 ccards 可导入（同 idle_pulse 口径）

from ccards import config as _cfg               # 单一配置入口（REQ-OSS-ENGINE-1：平台入口走配置，发布集不落内网字面量）
DEFAULT_BASE = _cfg.CC_PLATFORM_BASE            # env NIGHT_LEARN_BASE > 账本根 config.toml [platform] api_base > 内置默认（现网值在 main 仓 config.toml，不入包）
DEF_WINDOW = ("00:00", "06:00")                             # PRD 默认夜间窗
GATE_RESCUE = "ANNOTATE"                                   # 夜间直接放行的闸
ABNORMAL_SLAGS = ("EVAL_BELOW", "RUN_FAILED", "OVER_WINDOW", "NO_RUN",
                  "CONFIG_INVALID", "API_UNREACHABLE")      # 异常类稳定键（同题一键，防打地鼠）


# ---------------- 小工具 ----------------

def hhmm_to_min(s, default=None):
    """'HH:MM'→当日分钟数；非法返回 default（不猜）。"""
    try:
        h, m = str(s).strip().split(":")[:2]
        h, m = int(h), int(m)
    except Exception:
        return default
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return default
    return h * 60 + m


def in_window(now_dt, start_s, end_s):
    """夜间窗判定：start<end=当日段；start>end=跨零点段；相等=全天（不鼓励，口径明示）。"""
    a = hhmm_to_min(start_s, DEF_WINDOW[0])
    b = hhmm_to_min(end_s, DEF_WINDOW[1])
    t = now_dt.hour * 60 + now_dt.minute
    if a == b:
        return True
    return a <= t < b if a < b else (t >= a or t < b)


def window_start_dt(now_dt, start_s, end_s):
    """本次夜窗的**起点时刻**（观察窗下界=badcase TOP 的 since）：
    当日段=今天 start；跨零点段且现在已过零点=昨天 start，否则今天 start。"""
    a = hhmm_to_min(start_s, DEF_WINDOW[0])
    today = now_dt.replace(hour=a // 60, minute=a % 60, second=0, microsecond=0)
    b = hhmm_to_min(end_s, DEF_WINDOW[1])
    t = now_dt.hour * 60 + now_dt.minute
    if a > b and t < a:                      # 跨零点：00:30 属于昨天 22:00 开的那扇窗
        return today - timedelta(days=1)
    return today


def night_key(now_dt, start_s, end_s):
    """夜归属日=开窗那天的日期（跨零点窗在凌晨拍到时归属昨天，账才不会裂成两夜）。"""
    return window_start_dt(now_dt, start_s, end_s).strftime("%Y-%m-%d")


def window_ended(now_dt, start_s, end_s):
    """窗是否已过点（过点才出报，窗内只记不发）。"""
    a, b = hhmm_to_min(start_s, DEF_WINDOW[0]), hhmm_to_min(end_s, DEF_WINDOW[1])
    t = now_dt.hour * 60 + now_dt.minute
    if a == b:
        return False                          # 全天窗没有「过点」一说，等环龄兜底
    w0 = window_start_dt(now_dt, start_s, end_s)
    end = w0.replace(hour=b // 60, minute=b % 60, second=0, microsecond=0)
    if end <= w0:
        end = end + timedelta(days=1)
    return now_dt >= end


def num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def slug(s):
    keep = [c if (c.isalnum() or c in "._-") else "_" for c in str(s or "").strip()[:60]]
    return "".join(keep).strip("_") or "na"


# ---------------- 平台接口客户端（薄，可注入替身） ----------------

class PlatformApi:
    """只认 /api/flywheel/** 现成面 + 本卡新立的 /api/flywheel/night/** 与 /badcase/top。

    任何一次调用失败都抛 ApiError（HTTP 码+原文），由引擎按岗记异常圈，**不静默吞**。
    """

    def __init__(self, base=DEFAULT_BASE, token=None, user=None, pwd=None, opener=None):
        self.base = (base or DEFAULT_BASE).rstrip("/")
        self.token = token
        self.user, self.pwd = user, pwd
        self._opener = opener                  # 测试注入：fn(method, path, body, headers)→(status, json)

    def _call(self, method, path, body=None):
        if self._opener is not None:
            return self._opener(method, path, body)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw
        except Exception as e:                  # 连不上/超时=可识别异常，不许当成 200 空转
            raise ApiError(0, "%r" % (e,))

    def login(self):
        st, body = self._call("POST", "/api/auth/login", {"username": self.user, "password": self.pwd})
        if st != 200 or not isinstance(body, dict):
            raise ApiError(st, "登录失败 HTTP %s" % st)
        self.token = body.get("token") or (body.get("data") or {}).get("token")
        if not self.token:
            raise ApiError(st, "登录响应没有 token")

    def ensure_auth(self):
        if not self.token and self.user and self.pwd:
            self.login()
        if not self.token:
            raise AuthMissing("缺 NIGHT_LEARN_TOKEN 或 DW_USER/DW_PASS")

    # ---- 岗位夜学配置面（本卡新立） ----
    def night_posts(self):
        st, body = self._call("GET", "/api/flywheel/night/posts")
        if st != 200 or not isinstance(body, list):
            raise ApiError(st, "取岗位清单失败 HTTP %s" % st)
        return [p for p in body if isinstance(p, dict) and p.get("enabled")]

    def all_night_posts(self):
        st, body = self._call("GET", "/api/flywheel/night/posts")
        if st != 200 or not isinstance(body, list):
            raise ApiError(st, "取岗位清单失败 HTTP %s" % st)
        return [p for p in body if isinstance(p, dict)]

    def write_report(self, payload):
        st, body = self._call("POST", "/api/flywheel/night/report", payload)
        if st != 200:
            raise ApiError(st, "晨报写入失败 HTTP %s %s" % (st, body))
        return body

    def badcase_top(self, since_ms, limit=10, project_id=None):
        path = "/api/flywheel/badcase/top?sinceMs=%d&limit=%d" % (since_ms, limit)
        if project_id not in (None, ""):
            path += "&projectId=%s" % project_id
        st, body = self._call("GET", path)
        if st == 404:                           # 老后端没这个面：明说，不假装查到 0 条
            return None
        if st != 200 or not isinstance(body, list):
            raise ApiError(st, "badcase TOP 取数失败 HTTP %s" % st)
        return [{"question": r.get("question"), "count": r.get("count")} for r in body]

    # ---- 圈面（REQ-FW-4 现成） ----
    def start_run(self, spec):
        st, body = self._call("POST", "/api/flywheel/runs", spec)
        if st not in (200, 201) or not isinstance(body, dict) or body.get("id") is None:
            raise ApiError(st, "开圈失败 HTTP %s %s" % (st, body))
        return body

    def run_detail(self, run_id):
        st, body = self._call("GET", "/api/flywheel/runs/%s" % run_id)
        if st != 200 or not isinstance(body, dict):
            raise ApiError(st, "看圈失败 HTTP %s" % st)
        return body

    def decide_gate(self, run_id, gate, decision, reason=None):
        st, body = self._call("POST", "/api/flywheel/runs/%s/gate" % run_id,
                              {"gate": gate, "decision": decision, "reason": reason})
        if st != 200 or not isinstance(body, dict):
            raise ApiError(st, "过闸失败 HTTP %s" % st)
        return body


class ApiError(Exception):
    def __init__(self, status, msg):
        Exception.__init__(self, msg)
        self.status = status


class AuthMissing(Exception):
    pass


# ---------------- 状态账 ----------------

class NightState:
    """一夜一本账：`night-<YYYYMMDD>.json`。岗位圈次、闸痕、出报标记、提炼标记全在这里。"""

    def __init__(self, state_dir):
        self.dir = state_dir
        os.makedirs(self.dir, exist_ok=True)

    def path(self, night_date):
        return os.path.join(self.dir, "night-%s.json" % night_date.replace("-", ""))

    def load(self, night_date):
        p = self.path(night_date)
        if not os.path.exists(p):
            return {"nightDate": night_date, "posts": {}, "reported": [], "distilled": False}
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:                     # 账坏了不能连累夜环：响亮重建（旧账改名留证）
            try:
                os.replace(p, p + ".bad")
            except OSError:
                pass
            return {"nightDate": night_date, "posts": {}, "reported": [], "distilled": False,
                    "_recovered": "旧账不可解析（%r）已留 %s.bad" % (e, os.path.basename(p))}
        d.setdefault("nightDate", night_date)
        d.setdefault("posts", {})
        d.setdefault("reported", [])
        d.setdefault("distilled", False)
        return d

    def save(self, d):
        p = self.path(d["nightDate"])
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, p)                          # 原子落盘：半截账比没账更坏


# ---------------- 岗位配置校验 ----------------

def check_post_cfg(post):
    """返回错误串列表；空=配置可用。非法配置=该岗本夜不开圈，进异常单列（CONFIG_INVALID）。"""
    errs = []
    pid = post.get("labelProjectId")
    if pid is None:
        errs.append("缺 labelProjectId")
    a = hhmm_to_min(post.get("windowStart"), None)
    b = hhmm_to_min(post.get("windowEnd"), None)
    if a is None:
        errs.append("windowStart 非法（要 HH:MM）：" + repr(post.get("windowStart")))
    if b is None:
        errs.append("windowEnd 非法（要 HH:MM）：" + repr(post.get("windowEnd")))
    ms = num(post.get("evalMinScore"), None)
    if ms is None or ms < 0 or ms > 10:            # 后端 OrchestratorSettings 同口径 0–10
        errs.append("evalMinScore 允许 0–10（收到：" + repr(post.get("evalMinScore")) + "）")
    sql = (post.get("featureSql") or "").strip()
    if not sql:
        errs.append("featureSql 为空——岗位没有特征数据入环，夜学无米下锅")
    else:
        if not (post.get("qColumn") or "").strip() or not (post.get("aColumn") or "").strip():
            errs.append("sql 模式必须带 qColumn/aColumn（后端 startRun 同口径）")
    rows = num(post.get("maxRows"), 200)
    if rows is None or rows < 1 or rows > 500:     # 分页契约 size 1–500 同量级
        errs.append("maxRows 允许 1–500（收到：" + repr(post.get("maxRows")) + "）")
    return errs


def run_spec(post):
    """开圈请求体：七步链现成入参 + 本卡两项夜学语义（night / evalMinScore 岗位配置）。"""
    spec = {
        "sql": (post.get("featureSql") or "").strip(),
        "qColumn": (post.get("qColumn") or "").strip(),
        "aColumn": (post.get("aColumn") or "").strip(),
        "maxRows": int(num(post.get("maxRows"), 200) or 200),
        "night": True,                              # 后端 FlywheelOrchestrator 夜窗段读它
        "evalMinScore": num(post.get("evalMinScore"), 7.0),
        "labelProjectId": post.get("labelProjectId"),
    }
    if (post.get("refColumn") or "").strip():
        spec["refColumn"] = post["refColumn"].strip()
    if post.get("platformProjectId") not in (None, ""):
        spec["projectId"] = post["platformProjectId"]
    return spec


def eval_score_of(detail):
    """从 run 事件流水里取评测分（后端 doEval 停闸/放行都写 metrics.score）。"""
    best = None
    for ev in (detail.get("events") or []):
        if ev.get("step") != "EVAL":
            continue
        metrics = ev.get("metrics")
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = None
        s = num((metrics or {}).get("score"), None)
        if s is not None:
            best = s
    return best


# ---------------- 单岗一夜的圈机 ----------------

def advance_post(api, post, rec, now_iso, dry):
    """把某岗本夜的圈往前推一格（幂等）。返回 (状态, 异常原因或 None)。

    状态：WAITING=还在跑/没到点｜CLOSED=闭环｜ABNORMAL=异常单列项。
    """
    pid = str(post.get("labelProjectId"))
    win_start, win_end = post.get("windowStart") or DEF_WINDOW[0], post.get("windowEnd") or DEF_WINDOW[1]
    now_dt = datetime.fromisoformat(now_iso)
    errs = check_post_cfg(post)
    if errs:
        rec["state"], rec["abnormal"] = "ABNORMAL", "CONFIG_INVALID：" + "；".join(errs)
        return "ABNORMAL", rec["abnormal"]

    if rec.get("state") in ("CLOSED", "ABNORMAL"):
        return rec["state"], rec.get("abnormal")     # 已定局不再轮询/不再报警（同夜一岗只此一笔）

    if rec.get("runId") is None:
        if not in_window(now_dt, win_start, win_end):
            return "WAITING", None
        if dry:
            rec["state"] = "DRY"
            return "WAITING", None
        try:
            run = api.start_run(run_spec(post))
        except ApiError as e:
            rec["state"], rec["abnormal"] = "ABNORMAL", "RUN_FAILED：开圈失败 HTTP %s %s" % (e.status, e)
            return "ABNORMAL", rec["abnormal"]
        rec.update({"runId": run.get("id"), "state": "RUNNING",
                    "startedAt": now_iso, "gates": [], "seen": {}})
        return "RUNNING", None

    try:
        detail = api.run_detail(rec["runId"])
    except ApiError as e:
        rec["state"], rec["abnormal"] = "ABNORMAL", "API_UNREACHABLE：看圈失败 HTTP %s %s" % (e.status, e)
        return "ABNORMAL", rec["abnormal"]

    status = detail.get("status")
    rec["lastStatus"], rec["lastStep"] = status, detail.get("currentStep")
    if status == "CLOSED":
        rec["state"] = "CLOSED"
        rec["model"] = {"runId": rec["runId"], "modelName": detail.get("modelName"),
                        "modelExtId": detail.get("modelExtId"), "datasetVersion": detail.get("datasetVersion"),
                        "deployRef": detail.get("deployRef"), "closedAt": now_iso}
        return "CLOSED", None
    if status == "FAILED":
        rec["state"], rec["abnormal"] = "ABNORMAL", "RUN_FAILED：" + str(detail.get("lastError") or
                                                                        (detail.get("currentStep") or "?") + " 段失败")
        return "ABNORMAL", rec["abnormal"]

    if status == "WAIT_GATE":
        gate = detail.get("gateWait")
        if gate == GATE_RESCUE:                     # 标注完成闸：夜间=全自动，直接放行
            if not dry:
                try:
                    detail = api.decide_gate(rec["runId"], GATE_RESCUE, "approve",
                                             "夜间岗位学习自动放行（REQ-NIGHT-LEARN-1）")
                except ApiError as e:
                    rec["state"], rec["abnormal"] = "ABNORMAL", "API_UNREACHABLE：过闸失败 HTTP %s" % e.status
                    return "ABNORMAL", rec["abnormal"]
            rec.setdefault("gates", []).append({"gate": GATE_RESCUE, "at": now_iso, "by": "night-ring"})
            rec["state"] = "RUNNING"
            return "RUNNING", None
        if gate == "EVAL":                          # 评测准入闸：按岗位线判，分不够=停闸转晨复核
            score = eval_score_of(detail)
            line = num(post.get("evalMinScore"), 7.0)
            if score is not None and line is not None and score >= line:
                if not dry:
                    try:
                        api.decide_gate(rec["runId"], "EVAL", "approve",
                                        "夜间全自动：评测 %s ≥ 岗位线 %s" % (score, line))
                    except ApiError as e:
                        rec["state"], rec["abnormal"] = "ABNORMAL", "API_UNREACHABLE：过闸失败 HTTP %s" % e.status
                        return "ABNORMAL", rec["abnormal"]
                rec.setdefault("gates", []).append({"gate": "EVAL", "at": now_iso, "by": "night-ring",
                                                    "score": score, "line": line})
                rec["state"] = "RUNNING"
                return "RUNNING", None
            rec["state"] = "ABNORMAL"
            rec["abnormal"] = ("EVAL_BELOW：评测 %s 低于岗位准入线 %s，停闸待晨复核" % (score, line))
            return "ABNORMAL", rec["abnormal"]
        rec["state"] = "WAIT_GATE_OTHER"
        return "RUNNING", None

    # RUNNING：窗已过点仍未终态=超窗未终（异常单列），窗内等下一拍
    if window_ended(now_dt, win_start, win_end):
        rec["state"], rec["abnormal"] = "ABNORMAL", ("OVER_WINDOW：%s 段未闭环（窗 %s–%s 已过点）"
                                                     % (detail.get("currentStep"), win_start, win_end))
        return "ABNORMAL", rec["abnormal"]
    rec["state"] = "RUNNING"
    return "RUNNING", None


# ---------------- 晨报 ----------------

def build_report(api, post, rec, night_date, now_iso, dry):
    """一岗一夜一份报告。圈数/通过数/badcase TOP/新模型版本清单 + 异常单列（PRD 四项一个不少）。"""
    win_start, win_end = post.get("windowStart") or DEF_WINDOW[0], post.get("windowEnd") or DEF_WINDOW[1]
    w0 = window_start_dt(datetime.fromisoformat(now_iso), win_start, win_end)
    passed = 1 if rec.get("state") == "CLOSED" else 0
    circles = [] if rec.get("runId") is None else [rec.get("runId")]
    abnormal = []
    if rec.get("abnormal"):
        abnormal.append({"runId": rec.get("runId"), "reason": rec["abnormal"],
                         "kind": str(rec["abnormal"]).split("：")[0], "step": rec.get("lastStep")})
    if not circles:                                  # 开窗未起圈也是异常（环没赶上/凭证断/窗外补报）
        abnormal.append({"runId": None, "kind": "NO_RUN",
                         "reason": "NO_RUN：本夜未起圈（开窗时环不在/配置迟到）"})
    top = None
    if not dry:
        try:
            top = api.badcase_top(int(time.mktime(w0.timetuple())) * 1000,
                                  project_id=post.get("platformProjectId"))
        except ApiError:
            top = None
    models = [rec["model"]] if rec.get("model") else []
    return {
        "nightDate": night_date,
        "labelProjectId": post.get("labelProjectId"),
        "labelProjectTitle": post.get("labelProjectTitle"),
        "window": [win_start, win_end],
        "runs": len(circles),
        "passed": passed,
        "abnormal": len(abnormal),
        "abnormalRuns": abnormal,
        "badcaseTop": top if top is not None else [],
        "newModels": models,
        "gates": rec.get("gates") or [],
        "source": "cc-ctrld/postlearn",
        "generatedAt": now_iso,
        "detail": {"state": rec.get("state"), "lastStatus": rec.get("lastStatus"),
                   "badcaseTopAvailable": top is not None},
    }


def report_md(reports, distill_summary):
    """人读晨报：全岗汇总 + 异常单列（不混在正常行里）。"""
    lines = ["# 夜间学习晨报 %s" % (reports[0]["nightDate"] if reports else ""),
             "",
             "> 由 cc-ctrld 夜环 postlearn 自动生成（REQ-NIGHT-LEARN-1）。全自动跑圈 + 晨报复核。",
             "",
             "## 一、逐岗结果",
             "",
             "| 岗位（标注项目） | 窗 | 圈数 | 通过 | 异常 | 新模型 | badcase TOP |",
             "|---|---|---|---|---|---|---|"]
    for r in reports:
        models = "、".join(str(m.get("modelName") or m.get("modelExtId") or "-") for m in r["newModels"]) or "-"
        tops = "、".join("%s×%s" % ((t.get("question") or "?")[:18], t.get("count")) for t in r["badcaseTop"][:3]) or "-"
        lines.append("| %s(#%s) | %s–%s | %d | %d | %d | %s | %s |" % (
            r.get("labelProjectTitle") or "-", r.get("labelProjectId"),
            r["window"][0], r["window"][1], r["runs"], r["passed"], r["abnormal"], models, tops))
    bad = [(r, a) for r in reports for a in r["abnormalRuns"]]
    lines += ["", "## 二、异常圈（单列，晨复核入口）", ""]
    if not bad:
        lines.append("- 无")
    for r, a in bad:
        lines.append("- 岗位 #%s（%s）run=%s｜%s｜%s" % (
            r.get("labelProjectId"), (r["window"][0], r["window"][1])[0], a.get("runId"), a.get("kind"), a.get("reason")))
    if distill_summary is not None:
        lines += ["", "## 三、经验→SOP 提炼（草案待过目）", "",
                  "- 落草案 %d 份 → agent-service/playbooks-draft/（现役 playbooks/ 未动）" % distill_summary["written"],
                  "- 拒写 %d 份（过不了防腐锁/无路由模块/支持度不足）" % len(distill_summary["rejected"]),
                  "- 本夜无新经验可提炼=%s" % ("是" if distill_summary["nothing"] else "否")]
    lines.append("")
    return "\n".join(lines)


# ---------------- 主流程：一拍 ----------------

def tick(now_iso=None, base=None, token=None, user=None, pwd=None, state_dir=None,
         dry=False, api=None, distiller=None, root=None, log=None, no_sop=False):
    """一拍：逐岗推圈 → 到点出报（平台+本机）→ 提炼 SOP 草案。返回汇总 dict（rc 由调用方判）。"""
    now_iso = now_iso or os.environ.get("NIGHT_LEARN_NOW") or \
        datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    now_dt = datetime.fromisoformat(now_iso)
    root = root or os.path.dirname(ROOT)
    log = log or (lambda m: print(m, flush=True))
    api = api or PlatformApi(base or DEFAULT_BASE,   # env/file/内置三层优先级已在 CC_PLATFORM_BASE 内解析（REQ-OSS-ENGINE-1）
                             token if token is not None else os.environ.get("NIGHT_LEARN_TOKEN"),
                             user or os.environ.get("DW_USER"), pwd or os.environ.get("DW_PASS"))
    if state_dir is None:
        state_dir = os.environ.get("NIGHT_LEARN_STATE") or default_state_dir()
    store = NightState(state_dir)

    try:
        api.ensure_auth()
    except AuthMissing as e:
        log("POSTLEARN-SKIP %s" % e)
        return {"rc": 2, "reason": str(e), "skipped": "auth"}

    try:
        posts = api.all_night_posts()
    except ApiError as e:
        log("POSTLEARN-RED 取岗位清单失败 HTTP %s %s" % (e.status, e))
        return {"rc": 2, "reason": "posts-unreachable"}
    except Exception as e:
        log("POSTLEARN-RED 取岗位清单异常 %r" % (e,))
        return {"rc": 2, "reason": "posts-error"}

    enabled = [p for p in posts if p.get("enabled")]
    per_night = {}                                     # night_date → [report]
    moved, abnormal_total = 0, 0
    for post in enabled:
        nd = night_key(now_dt, post.get("windowStart") or DEF_WINDOW[0],
                       post.get("windowEnd") or DEF_WINDOW[1])
        d = store.load(nd)
        rec = d["posts"].setdefault(str(post.get("labelProjectId")), {})
        st, _ = advance_post(api, post, rec, now_iso, dry)
        store.save(d)
        moved += 1
        if st == "ABNORMAL":
            abnormal_total += 1
            if not rec.get("alerted"):            # 同夜同岗同一异常只报一笔（报警折叠由稳定键兜第二层）
                rec["alerted"] = True
                kind = str(rec.get("abnormal", "")).split("：")[0].strip()
                emit_alert("POST-LEARN-ABNORMAL kind=%s post=%s" % (kind, post.get("labelProjectId")), log)
                store.save(d)
        per_night.setdefault(nd, []).append((post, rec, d))

    reports_written = []
    if not dry:
        for nd, rows in per_night.items():
            d = store.load(nd)
            for post, rec, _ in rows:
                pid = str(post.get("labelProjectId"))
                if pid in d["reported"]:
                    continue
                ended = window_ended(now_dt, post.get("windowStart") or DEF_WINDOW[0],
                                     post.get("windowEnd") or DEF_WINDOW[1])
                settled = rec.get("state") in ("CLOSED", "ABNORMAL")
                if not (ended or (settled and not in_window(now_dt, post.get("windowStart") or DEF_WINDOW[0],
                                                             post.get("windowEnd") or DEF_WINDOW[1]))):
                    continue                            # 窗内且未终：不提前出报
                rep = build_report(api, post, rec, nd, now_iso, dry)
                try:
                    api.write_report(rep)
                except ApiError as e:
                    rep["detail"]["reportWriteError"] = "HTTP %s %s" % (e.status, e)
                    emit_alert("POST-LEARN-REPORT-WRITE-FAILED post=%s" % pid, log)
                d["reported"].append(pid)
                d["posts"][pid] = rec
                store.save(d)
                reports_written.append(rep)

        # 本机晨报 md（一夜一份，重拍覆盖=同一夜只此一份事实）
        if reports_written:
            d0 = store.load(reports_written[0]["nightDate"])
            distill_sum = None
            if not no_sop and not os.environ.get("NIGHT_LEARN_NO_SOP") and not d0.get("distilled"):
                distill_sum = run_distill(reports_written, root, state_dir, log, distiller)
                for nd in {r["nightDate"] for r in reports_written}:
                    dd = store.load(nd)
                    dd["distilled"] = True
                    store.save(dd)
            md_path = os.path.join(state_dir, "晨报-%s.md" % reports_written[0]["nightDate"].replace("-", ""))
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(report_md(reports_written, distill_sum))
            log("POSTLEARN-REPORT 写 %d 份晨报 → %s" % (len(reports_written), md_path))

    rep_abnormal = sum(int(r.get("abnormal") or 0) for r in reports_written)
    rc = 0 if (abnormal_total == 0 and rep_abnormal == 0) else 1
    summary = {"rc": rc, "now": now_iso, "posts": len(enabled), "advanced": moved,
               "abnormal": abnormal_total, "reportedAbnormal": rep_abnormal,
               "reports": len(reports_written), "dry": dry}
    log("POSTLEARN-%s %s" % ("GREEN" if rc == 0 else "WARN", json.dumps(summary, ensure_ascii=False)))
    return summary


def run_distill(reports, root, state_dir, log, distiller=None):
    """经验→SOP 提炼段（共用调度=本环顺拍，不另起定时器）。"""
    distiller = distiller or _load_distiller(root, log)
    if distiller is None:
        return {"written": 0, "rejected": [{"id": "-", "reason": "提炼器不可用"}], "nothing": False}
    exp = collect_experience(root, reports)
    try:
        out = distiller.distill(reports, exp, root=root,
                                draft_dir=os.path.join(root, "agent-service", "playbooks-draft"))
    except Exception as e:
        emit_alert("POST-LEARN-SOP-DISTILL-ERROR", log)
        log("POSTLEARN-SOP 提炼异常 %r" % (e,))
        return {"written": 0, "rejected": [{"id": "-", "reason": repr(e)}], "nothing": False}
    log("POSTLEARN-SOP 草案 %d 份/拒 %d 份 → agent-service/playbooks-draft"
        % (out["written"], len(out["rejected"])))
    for r in out["rejected"][:5]:
        log("  REJECT %s：%s" % (r.get("id"), r.get("reason")))
    return {"written": out["written"], "rejected": out["rejected"], "nothing": out.get("nothing", False)}


def _load_distiller(root, log):
    """提炼器在 agent-service 同侧还是 ops 侧都可跑：优先 ops 侧（本环自带），失败响亮跳过。"""
    try:
        sys.path.insert(0, os.path.join(root, "agent-service"))
        import sop_distill                                # noqa: E402
        return sop_distill
    except Exception:
        pass
    try:
        import sop_distill                                # noqa: E402  （cc-team/ops 同目录）
        return sop_distill
    except Exception as e:
        log("POSTLEARN-SOP 提炼模块缺失 %r" % (e,))
        return None


def collect_experience(root, reports):
    """经验源=本机 agent 经验流水（experience.py 落 jsonl）+ 本夜异常圈事实。
    只读，不改写；文件不存在=空集（首夜正常现象，不报错）。"""
    rows = []
    data_dir = os.environ.get("AGENT_EXPERIENCE_DIR") or os.path.join(root, "data")
    if os.path.isdir(data_dir):
        for name in sorted(os.listdir(data_dir)):
            if not (name.startswith("experience-") and name.endswith(".jsonl")):
                continue
            try:
                with open(os.path.join(data_dir, name), encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            continue
            except OSError:
                continue
    return rows


def emit_alert(msg, log=None):
    """报警走 ccards.alerts 正账（同题一键，去龄/组销按 MECH-ALARM-DEAGE-1 现成口径）。"""
    try:
        from ccards import alerts
        alerts.record(msg)
    except Exception as e:
        if log:
            log("POSTLEARN-ALARM-WRITE-FAIL %r" % (e,))


def default_state_dir():
    try:
        from ccards import config
        return os.path.join(config.STATE, "night-learn")
    except Exception:
        return os.path.join(ROOT, "state", "night-learn")


def main(argv=None):
    ap = argparse.ArgumentParser(description="REQ-NIGHT-LEARN-1 岗位夜间学习夜环（cc-ctrld postlearn）")
    ap.add_argument("--apply", action="store_true", help="正式模式（ctrld 环内带）")
    ap.add_argument("--dry", action="store_true", help="影子模式：只点名不动账/不开圈")
    ap.add_argument("--now", help="注入时钟 ISO 本地时间（演练/判据用）")
    ap.add_argument("--base", help="平台入口（缺省=配置单入口 CC_PLATFORM_BASE：env NIGHT_LEARN_BASE > 账本根 config.toml [platform] api_base > 内置默认）")
    ap.add_argument("--state-dir", dest="state_dir", help="状态根，默认 env NIGHT_LEARN_STATE")
    ap.add_argument("--no-sop", action="store_true", help="跳过经验→SOP 提炼段")
    ap.add_argument("--json", action="store_true", help="打印汇总 JSON")
    a = ap.parse_args(argv)
    dry = a.dry or not a.apply                            # 不带 --apply 一律只读（环外手动跑也安全）
    out = tick(now_iso=a.now, base=a.base, dry=dry, state_dir=a.state_dir, no_sop=a.no_sop)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    return int(out.get("rc", 2))


if __name__ == "__main__":
    time_now = time.time                                  # （保留引用，测试不打桩本模块时钟）
    sys.exit(main())
