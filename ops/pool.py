#!/usr/bin/env python3
# cc-team/ops/pool.py — 弹性工位池（REQ-POOL-ELASTIC-1，业主 2026-11-02 18:5x 定版
#   「要多少是业务需求，性能控制并发就行了」；类型=需求：泳道从「固定名册」改「动态工位池」）。
#
# 三支柱（卡面①②③，全部实现进 ctrld 新本体一环=dispatch.py 引用的 ops 件，不在旧躯壳二次动工）：
#  ① lanes=ANY 新语义（默认推荐）：卡进**全局 FIFO**（发卡时刻序、同刻卡号序；同卡号跨道天然去重
#     ——账本卡号唯一 + 在制卡不再入派单名单），有空位即派。具体道值只保留一种用途：
#     「同文件族必须串行」的**显式互斥声明**（parity/board 集群卡靠它）；lanes 混写 ANY+具体道时
#     具体道赢（互斥优先，防一卡两桌；要用池就单写 ANY）。判定唯一出处=本文件 is_pool_card。
#  ② 工位=动态工作树 slot-<自增号>：派发即建桌（git worktree add，秒级），交卷转正（VERIFIED/
#     CANCELED）后自动收桌（worktree remove+台账留痕——progress 旁证笔由调用方落）；
#     号只进不退（留痕，不重号）。「躺平桌」（桌龄超阈且对应卡已回 OPEN/查无此卡）：净桌收，
#     脏桌**不毁**留点名（无声销毁在制品=禁；PM 处置）。
#  ③ 并发唯一闸门=资源闸自动化：放行新增工人条件 = 宿主空闲内存 ≥ 单工实测成本
#     （成本=滚动中位数**自学习**：无人在制记空闲基线，在岗时 (基线-空闲)/在岗数 采样；
#     起标 SEED_COST_MB=2560MB=2.5GB）且 在岗数 < DISPATCH_CAP。帽此刻只是「最大不超过」的
#     保险丝，不是配额（缺省 2→6，现网 systemd 显式值不受影响；MEMGATE_MIN_FREE_MB 固定地板
#     在派发侧**退役**——它是配额时代产物，弹性语义下由成本闸接管；recycle 侧仍用它报警不拦事）。
#
# 三本操作账（state/，事实账仍=graph 正账，这里只是运行旁证，看板现读零写）：
#   pool.json          桌账 {seq, desks:{slot:{card,dir,branch,opened,status}}, history:[留痕]}
#   pool-gate.json     本圈放行判定值快照（free/cost/on_duty/cap/allow + 点名 blocked）
#   worker-cost.json   成本学习账（baseline/samples/cost_mb）
#
# 注入锚（沙箱/自测；生产不设=原语义）：
#   POOL_HOME=<dir>   三本账挪位（沙箱必设，防碰正账）
#   POOL_COST_MB=<n>  钉死单工成本（缺省走自学习；红绿锁用）
#   POOL_REPO / POOL_BASE_REF / DISPATCH_CODE_ROOT  建桌仓锚（缺省=车队根下 dataworks-ai，主干 main）
#   MEMGATE_FORCE_MB  伪水位（与 dispatch.py --selftest 同族；伪读数**不进**成本学习）
import json, os, statistics, subprocess, sys, time
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

ANY = "ANY"
SLOT_PREFIX = "slot-"
WF_PREFIX = "wf-"
SEED_COST_MB = 2560.0                    # 起标 2.5GB（卡面：成本自学习，起标定这）
COST_WINDOW = 30                         # 滚动中位数样本窗
SAMPLE_MIN_MB, SAMPLE_MAX_MB = 64.0, 32768.0   # 样本合理区间（窗外=噪声，不学）
IDLE_GRACE_H = 24.0                      # 躺平桌候收窗（小时）
COLLECTABLE = ("VERIFIED", "CANCELED")   # 交卷转正态：到点即收（force 允许，交付有主干闸兜底）


# ---------- 路径锚 ----------

def home_dir():
    h = os.environ.get("POOL_HOME")
    if h:
        return h
    try:
        sys.path.insert(0, os.path.dirname(HERE := os.path.dirname(os.path.abspath(__file__))))
        from ccards import config as _cfg
        return _cfg.STATE
    except Exception:
        return os.path.join(os.path.dirname(HERE), "state")


def _acct(name):
    return os.path.join(home_dir(), name)


def _read_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else default
    except (OSError, ValueError):
        return default


def _write_json(p, obj):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


# ---------- ① 全局 FIFO（纯函数，红绿锁直测点） ----------

def is_pool_card(c):
    """lanes 恰为 ["ANY"]（唯一 ANY）才进池；混写具体道=显式互斥声明优先，留在原道串行。"""
    if not isinstance(c, dict):
        return False
    lanes = [str(x).strip() for x in (c.get("lanes") or [])]
    return bool(lanes) and ANY in lanes and len(lanes) == 1


def pool_order(cards):
    """池内全卡 FIFO：发卡时刻序、同刻按卡号（与 engine._lane_order 同口径）。
    同卡号跨道去重=账本结构唯一+排序稳定，这里保证**一卡一位**；
    在制/收口卡由调用侧（engine 资格过滤/dispatch 名单剔除）不再入派单名单。"""
    ids = [cid for cid, c in cards.items() if isinstance(c, dict) and is_pool_card(c)
           and c.get("status") == "OPEN"]
    ids.sort(key=lambda cid: (str(cards[cid].get("created_at") or ""), cid))
    return ids


# ---------- ③ 资源闸（纯函数 + 自学习成本） ----------

def cost_now():
    """当前单工成本 MB：POOL_COST_MB 钉死 > 已学习中位数 > 起标 2560。"""
    ov = os.environ.get("POOL_COST_MB")
    if ov not in (None, ""):
        try:
            v = float(ov)
            if v > 0:
                return v
        except ValueError:
            pass
    st = _read_json(_acct("worker-cost.json"), {})
    c = st.get("cost_mb")
    try:
        if c is not None and float(c) > 0:
            return float(c)
    except (TypeError, ValueError):
        pass
    return SEED_COST_MB


def learn(free_mb, inflight_n):
    """一圈成本观察：空闲无在制→刷基线；有在制→(基线-空闲)/在岗数 采样（正窗内才收），
    滚动中位=新成本。伪读数(MEMGATE_FORCE_MB)/测不到(None)不学不写账。返回 cost_now()。"""
    if free_mb is None or os.environ.get("MEMGATE_FORCE_MB") not in (None, ""):
        return cost_now()
    st = _read_json(_acct("worker-cost.json"),
                    {"baseline_free_mb": None, "samples": [], "cost_mb": None})
    fm = float(free_mb)
    n = int(inflight_n)
    if n <= 0:
        st["baseline_free_mb"] = fm
    else:
        base = st.get("baseline_free_mb")
        if isinstance(base, (int, float)) and base >= fm:
            s = (float(base) - fm) / n
            if SAMPLE_MIN_MB <= s <= SAMPLE_MAX_MB:
                smp = (st.get("samples") or [])[-(COST_WINDOW - 1):] + [round(s, 1)]
                st["samples"] = smp
                st["cost_mb"] = float(statistics.median(smp))
    st["updated"] = time.strftime("%F %T")
    _write_json(_acct("worker-cost.json"), st)
    return cost_now()


def admission(free_mb, cost_mb, inflight_n, cap, pending):
    """并发唯一闸门（纯函数）：放行新增工人条件=空闲内存≥单工实测成本 且 在岗数<帽。
    pending=FIFO 候派卡号 list。返回 {"allow":int, "blocked":[(cid, why)], "note":str}。
    测不到水位=fail-open（按帽余量放行+PROBE-FAIL 点名）——探针炸≠水位高，
    把车队闸成静默比放行更糟（MECH-MEMGATE-1 同纪律）。"""
    pending = list(pending or [])
    cost = max(1.0, float(cost_mb))
    fuse_head = max(0, int(cap) - int(inflight_n))
    if free_mb is None:
        mem_head = fuse_head
        mem_note = "PROBE-FAIL fail-open"
    else:
        mem_head = int(float(free_mb) // cost)      # 边际预算：每开一桌须净罩一个单工成本
        mem_note = ""
    allow = max(0, min(fuse_head, mem_head, len(pending)))
    blocked = []
    for i, cid in enumerate(pending):
        if i < allow:
            continue
        if fuse_head == 0:
            why = "保险丝帽：在岗 %d 已达 DISPATCH_CAP=%d（帽只当最大不超过，配额已废）" % (int(inflight_n), int(cap))
        elif i >= fuse_head:
            why = "保险丝帽：本圈放 %d 桌到顶（在岗 %d+新 %d=DISPATCH_CAP=%d）" % (allow, int(inflight_n), i + 1, int(cap))
        else:
            why = "资源闸：空闲 %sMB 罩不住第 %d 桌（单工实测成本 %.0fMB，滚动中位数自学习）" % (
                ("测不到" if free_mb is None else "%.0f" % float(free_mb)), i - max(fuse_head - mem_head, 0) + 1, cost)
        blocked.append((cid, why))
    note = "free=%s cost=%.0f on_duty=%d cap=%d allow=%d pending=%d%s" % (
        "None" if free_mb is None else "%.0f" % float(free_mb), cost, int(inflight_n), int(cap),
        allow, len(pending), (" " + mem_note) if mem_note else "")
    return {"allow": allow, "blocked": blocked, "note": note}


# ---------- ② 工位桌账与建/收（真 git worktree，秒级） ----------

def desks():
    return _read_json(_acct("pool.json"), {"seq": 0, "desks": {}, "history": []})


def save_desks(st):
    _write_json(_acct("pool.json"), st)


def next_slot(st):
    st["seq"] = int(st.get("seq", 0)) + 1
    return "%s%d" % (SLOT_PREFIX, st["seq"])


def desk_dir(code_root, slot):
    return os.path.join(code_root, "%s%s" % (WF_PREFIX, slot))


def repo_root():
    r = os.environ.get("POOL_REPO")
    if r:
        return r
    code = os.environ.get("DISPATCH_CODE_ROOT") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(code, "dataworks-ai")


def code_root():
    return os.environ.get("DISPATCH_CODE_ROOT") or os.path.dirname(repo_root())


def base_ref():
    return os.environ.get("POOL_BASE_REF", "main")


def run_git(repo, *args):
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WIN)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 124, repr(e)


def open_desk(slot, code=None, repo=None, base=None):
    """建桌=git worktree add（秒级）。撞号（目录/残分支在）返回 False+原因，
    号只进不退（留痕纪律）——调用方取下一号重试，不回收撞号。"""
    code = code or code_root()
    repo = repo or repo_root()
    base = base or base_ref()
    d = desk_dir(code, slot)
    if os.path.isdir(d):
        return False, d, "桌目录已存在 %s（疑残桌，先 prune 或换号）" % d
    rc, out = run_git(repo, "worktree", "add", d, "-b", slot, base)
    if rc != 0:
        return False, d, "worktree add rc=%d tail=[%s]" % (rc, " ".join(out.split())[-200:])
    return True, d, ""


def remove_desk(path, code=None, repo=None, force=False):
    """收桌=git worktree remove。返回 (how, err)：removed/forced/failed。
    force 仅用于「交卷转正」桌（交付经主干闸在案，残渣即垃圾）；躺平桌 force=False，
    脏=failed 不毁（点名交 PM）。"""
    code = code or code_root()
    repo = repo or repo_root()
    rc, out = run_git(repo, "worktree", "remove", path)
    if rc == 0:
        return "removed", ""
    if os.path.isdir(path):
        if force:
            rc2, out2 = run_git(repo, "worktree", "remove", "--force", path)
            if rc2 == 0:
                return "forced", "dirty-force（转正桌残料强收）"
            return "failed", "rc=%d tail=[%s]" % (rc2, " ".join((out2 or out).split())[-200:])
        return "failed", "脏桌拒收（不毁在制品）：rc=%d tail=[%s]" % (rc, " ".join(out.split())[-200:])
    return "removed", "目录已不在=视作收净"


def collect_round(cards, apply_mode, code=None, repo=None, now=None, grace_h=None, log=None):
    """收桌判据（sweep 环每拍调用；纯决策可注入=红绿锁直测）：
      交卷转正（VERIFIED/CANCELED）→ 收（force 允许）；
      查无此卡/卡回 OPEN（被 release 重派走了）且桌龄≥grace → 净桌收、脏桌点名不毁；
      CLAIMED/DONE → 不动（在制与待验桌都有用）。
    返回 (n_removed, n_forced, n_dirty, collected_lines, wait_n)；
    collected_lines=[(slot, cid, why)] 供调用方落台账留痕（progress 旁证笔）。"""
    log = log or (lambda m: None)
    code = code or code_root()
    repo = repo or repo_root()
    now = now if now is not None else time.time()
    grace_h = IDLE_GRACE_H if grace_h is None else float(grace_h)
    st = desks()
    n_r = n_f = n_d = n_w = 0
    done, lines = [], []
    for slot in sorted(st.get("desks") or {}):
        rec = st["desks"][slot]
        if rec.get("status") == "collected":
            continue
        cid = rec.get("card") or ""
        c = cards.get(cid)
        stt = (c or {}).get("status") or "无卡"
        path = rec.get("dir") or desk_dir(code, slot)
        age_h = (now - float(rec.get("opened") or now)) / 3600.0
        why, force = None, False
        if stt in COLLECTABLE:
            why, force = "交卷转正(%s)" % stt, True
        elif stt == "无卡" or stt == "OPEN":
            if age_h >= grace_h:
                why = "躺平桌（卡=%s 桌闲 %.1fh≥%.0fh）" % (stt, age_h, grace_h)
        if why is None:
            n_w += 1
            continue
        if not apply_mode:
            log("POOL-DRY %s(%s)：%s → 会收桌 %s" % (slot, cid, why, path))
            done.append((slot, why, "dry"))
            continue
        how, err = remove_desk(path, code=code, repo=repo, force=force)
        if how == "failed":
            n_d += 1
            log("POOL-DIRTY %s(%s)：%s 收桌被拒——脏桌不毁留点名，PM 处置｜%s" % (slot, cid, why, err))
            continue
        if how == "forced":
            n_f += 1
        else:
            n_r += 1
        rec["status"] = "collected"
        rec["collected"] = time.strftime("%F %T")
        rec["collect_why"] = why + (("｜" + err) if err else "")
        st.setdefault("history", []).append({"slot": slot, "card": cid, "why": why,
                                             "how": how, "at": rec["collected"]})
        st["history"] = st["history"][-200:]
        log("POOL-COLLECT %s(%s)：%s → %s（桌账留痕）" % (slot, cid, why, how))
        done.append((slot, cid, why))
        lines.append((slot, cid, why))
    if apply_mode and done:
        save_desks(st)
    return n_r, n_f, n_d, lines, n_w


def mark_desk_opened(slot, cid, path, branch=None):
    st = desks()
    st["desks"][slot] = {"card": cid, "dir": path, "branch": branch or slot,
                         "opened": time.time(),
                         "opened_at": time.strftime("%F %T"), "status": "occupied"}
    save_desks(st)


def write_gate(adm, inflight_n, cap, cost, mode="live"):
    """本圈放行判定值快照（看板「工位池」小节唯一数字出处=此账，前端零自造）。
    mode=dry 也照写（影子圈同样是「本圈放行判定值」的如实反映；旁证账非账本，
    与 idle-streak.json 同纪律，不触 WRITERS 写锁集合）。"""
    _write_json(_acct("pool-gate.json"),
                {"ts": time.strftime("%F %T"), "mode": mode, "note": adm["note"],
                 "allow": adm["allow"],
                 "pending": len(adm["blocked"]) + adm["allow"], "cost_mb": cost,
                 "on_duty": int(inflight_n), "cap": int(cap),
                 "blocked": [[c, w] for c, w in adm["blocked"]][-30:]})


def board_readout():
    """看板现读（status-web.py /api/ops 消费；零子进程零写）：
    在岗/在建桌数 + 本圈放行判定值 + 桌明细 + 成本学习账尾。"""
    out = {"desks": [], "n_occupied": 0, "n_collected": 0, "seq": 0,
           "gate": None, "cost": None}
    st = desks()
    out["seq"] = int(st.get("seq", 0))
    now = time.time()
    for slot in sorted(st.get("desks") or {}):
        rec = st["desks"][slot]
        age_min = round((now - float(rec.get("opened") or now)) / 60.0, 1)
        out["desks"].append({"slot": slot, "card": rec.get("card") or "-",
                             "status": rec.get("status") or "occupied",
                             "opened_at": rec.get("opened_at") or "-",
                             "age_min": age_min,
                             "why": (rec.get("collect_why") or "")[:120]})
        if rec.get("status") == "collected":
            out["n_collected"] += 1
        else:
            out["n_occupied"] += 1
    out["desks"] = out["desks"][-40:]
    out["gate"] = _read_json(_acct("pool-gate.json"), None)
    wc = _read_json(_acct("worker-cost.json"), {})
    out["cost"] = {"cost_mb": wc.get("cost_mb"), "samples": len(wc.get("samples") or []),
                   "seed_mb": SEED_COST_MB, "updated": wc.get("updated")}
    return out
