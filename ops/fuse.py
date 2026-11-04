#!/usr/bin/env python3
# cc-team/ops/fuse.py — 任务断流保险丝（REQ-FLEET-FUSE-1；业主 2026-11-02 22:5x 定版
#   「核心驱动要机制保证不靠自觉」。首案实锤：当晚 20:40~22:33 全队列停摆 1h50m——两整合卡
#   早已交卷，合并这步只有人肉路径，PM 收轮无人兜底；同晚另有一卡三连死无人叫停。
#   总账见 docs/机制-根因总账-20261102.md 第七节。）
#
# 是什么：五段哨兵，每次调用跑一圈，由 cc-ctrld 常驻进程内 fuse 环每分拉起子进程
#   （业主 2026-11-03 令「定时任务不要用操作系统定时」——原 OS 计划任务 cc-fuse 已注销，
#    收编进环；新进程=改码即时生效的语义不变，「修好≠通电」在常驻件上结构性消失）。
#   每段**先自动救、救不动才逐级叫人**：
#   S1 派发心跳  dispatcher.heartbeat 龄>3min → 重启常驻派发器（白名单件）；重启失败 → 叫 PM。
#       自杀闸门（BUG-FUSE-SELFKILL-1，2026-11-04 四连案）：重启通道打的就是常驻引擎自己
#       （Windows 任务 cc-ctrld）。名册 hb 的 live pid 仍活着 ⇒ 一律 REFUSED 只报警不动手
#       ——旧『Stop;Start 同串』=杀自己爹+孤儿环持组锁+复起被 IgnoreNew 吞=死亡螺旋。
#   S2 工人即死  接已上线的 WORKER-DIED（release+重派归 dispatch/recycle），本器补停毒锁：
#       同卡连续 2 次即死（窗口内且两次死之间无 DONE/VERIFIED 成迹）→ release(如在制)
#       + set HOLD hold=STOPLOSS:fuse（HOLD 不入 ready=不再盲派）+ 红灯 + 叫 PM。
#       防的就是当晚三连死空转。
#       BUG-POOL-SLOTCHURN-1④ 补面（2026-11-03）：recycle 判死放流不落 WORKER-DIED，
#       09:30 轮同卡 12 台连环翻台本段在册=0 全程静默——churn_index 把 events 同卡
#       recycle-release 折进死次账（who 只认 recycle，fuse 自身停毒痕不计入防自触发）。
#   S3 交卷未并  DONE 且 commit 非主干且龄>30min → 火车卡 b/c 判类（git merge-tree 干跑）：
#       b=能合 → 自动 merge --no-ff 进主干（前置三闸：主干工作树 clean / HEAD=主干 / 复验干跑
#       仍净；任一不满足→不赌，报警叫 PM）；c=冲突 → 「待合·撞车」红灯（alerts 账=看板现读）
#       + 叫 PM 人工解冲突。合并后转正仍走 REDAUTO/verdict 既有通道，本器不越权落 VERIFIED。
#       b 类判类后、真合前再加扫（MECH-FUSE-S3-TIPSCAN-1，二轮§五-1 遗留面）：卡面锚只一笔，
#       锚外同 owner（卡交付仓 c["repo"]+交卷支 done_anchor.branch）树尖还有后续 commit 的
#       「多锚卡」按单锚自动并=只并残缺半边（UX-BOARD-COLLAPSE-1 三笔半成品之首实锤）。
#       命中≥1 改判「待合·多锚」不并、叫 PM 人工定锚；树尖探不出=兜底按同卡交卷流水(events
#       CLAIMED->DONE 多笔)判。无归属 commit 按本卡候并算=判不了挡（宁叫人不误并，s3_classify
#       同语义）；只带别卡号=泳道已挪下一单，不算本卡账不误挡。
#   S4 生效锁    常驻件进程起时早于其命脉文件最后盘改时(mtime)=旧进程没通电 → NOT-ENERGIZED
#       红灯 + 白名单件（cc-dispatch/cc-ctrld）自动重启；非白名单只报不动。重启走同 S1 通道，
#       共用节流防分钟级连环重启。口径变更（BUG-FUSE-SELFKILL-1④，00:28:16 错杀案）：判据
#       从 git 提交时改为磁盘 mtime——「先改盘后提交」是 PM 正常工序，提交时间不代表落地时间，
#       旧口径把刚换上当前代码的引擎又处决了一遍。
#   S5 PM 缺位  账上有候补（DONE 未并积压>0 或 空道>2）且 events.log 里 actor=PM 最后动作
#       龄>25min → 经 MECH-PM-WAKE 现成通道叫 PM（与 ops/inbox_watchdog.py 同源：
#       run-cc.ps1 lane=pmmail，零新机制）；叫了 30min 仍无 PM 动作 →
#       挂「业主:需要人工介入」红灯（alerts 键=看板概览·待裁决告警面板现读；
#       今日快照顶行渲染一行归看板 lane——本道不碰看板，交接见证据文档）。
#   S6 报警升级  alerts 无主红灯账龄>30min → 叫 PM 班认领（一级，run_pm_wake 现通道）；
#       仍无人认领>60min → 直达业主信箱 inbox/PM-REPLIES.md [待拍板] 条目（二级，主助理
#       转发微信通道）。分级告警+认领时效+自动升级=PagerDuty on-call 语义
#       （信箱 seq=22 A 项业主拍板：系统保障 PM 常责不立卡）。键级单发防刷屏：每键
#       至多唤一次 PM、至多直达一次；ack 落账即清标回归静默。自触发键（业主头/FUSE-S6 头）
#       不参与，防升级环喂自己。阈值 FUSE_ALERT_ACK1_MIN/FUSE_ALERT_ACK2_MIN。
# 落痕纪律：每段的**处置**统一三面落笔——events.log（actor=fuse，行前缀 FUSE-S<n>）、
#   state/alerts.json（红灯，alerts 同题聚合 count/ack 闭环现成）、logs/fuse.log（本器圈迹）。
#   state/fuse.json=本器水位（last_pass 自心跳 + 唤醒/重启节流 + 停毒/挡车台账）。
# 保守面：缺省 dry 只报不动（零 git 写/零写闸/零起进程/零告警账）；--apply 才真动
#   （同 recycle/ctrld 姿势——手滑裸跑不动账）。
# 红检面：FUSE_NOACT=s1,s2,s3,s4,s5,s6 逗号任取——被摘段的处置整段空转（只报不救），
#   tests/fuse-check.py 反证「撤任一处置必红」；空检查比没有检查更糟，此缝给检查上锁。
# 测试缝（env，生产不设=走现网默认）：
#   CC_TEAM_HOME 台账家 ｜ FUSE_HB 派发心跳文件（缺省=DISPATCH_HB 或 <代码根>/.cc/state/dispatcher.heartbeat，
#   与 ctrld.child_env 同式）｜ FUSE_LOGS WORKER-DIED 扫描文件清单（逗号；缺省 <home>/logs/dispatch.log,
#   <home>/logs/ctrld.log）｜ FUSE_MAIN_REPO / FUSE_MAIN_REF 主干仓库与分支 ｜
#   FUSE_RESTART_CMD 重启命令（shell 串）｜ FUSE_PM_WAKE_CMD 叫 PM 命令（shell 串，reason 走
#   PM_WAKE_REASON env 传入）｜ FUSE_PROBE_START_CMD 进程起时探针（stdout=epoch）｜
#   FUSE_RESIDENTS_JSON 常驻名册覆盖 ｜ 阈值组 FUSE_HB_STALE_MIN=3 / FUSE_DIED_WINDOW_MIN=60 /
#   FUSE_UNMERGED_AGE_MIN=30 / FUSE_PM_SILENT_MIN=25 / FUSE_PM_ESCALATE_MIN=30 /
#   FUSE_WAKE_MIN_INTERVAL_MIN=10 / FUSE_RESTART_THROTTLE_MIN=4 / FUSE_ENERGIZE_GRACE_MIN=2。
import argparse, json, os, re, shutil, subprocess, sys, time
from datetime import datetime
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

OPS = os.path.dirname(os.path.abspath(__file__))
TEAM = os.path.dirname(OPS)
sys.path.insert(0, TEAM)
from ccards import config as _cfg  # REQ-OSS-ENGINE-1：任务名/runner 随配置单入口（env>toml>默认=现网值）


def team_home():
    return os.path.abspath(os.environ.get("CC_TEAM_HOME") or TEAM)


def env_num(name, default):
    try:
        return float(str(os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def env_list(name, default):
    v = os.environ.get(name)
    if v is None:
        v = default
    return [p.strip() for p in v.split(",") if p.strip()]


def parse_ts(s):
    """多源时刻归一到 epoch：events/卡面 ISO+0800、日志 "%F %T" 本地时区、ISO+08:00 都认。"""
    s = (s or "").strip()
    if not s:
        return None
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        d = datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.astimezone()
        return d.timestamp()
    except ValueError:
        pass
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], f).timestamp()
        except ValueError:
            continue
    return None


# ---------------- 名册与账读 ----------------

def registry_lanes(home):
    """与 ops/dispatch.py registry_lanes 同口径的只读复制：projects[].lanes → {lane: {...}}。"""
    try:
        data = json.load(open(os.path.join(home, "registry.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for proj in (data.get("projects") or []):
        for ln in (proj.get("lanes") or []):
            if isinstance(ln, str):
                out[ln] = {}
            elif isinstance(ln, dict) and ln.get("lane"):
                out[str(ln["lane"])] = ln
    return out


def all_cards_safe(home):
    """账读卡集：走 ccards.graph 唯一访问器（看板/引擎同门），账读不出=空账不炸圈。"""
    try:
        sys.path.insert(0, home)
        sys.path.insert(0, TEAM)
        from ccards.graph import all_cards
        return all_cards() or {}
    except Exception:
        try:
            g = json.load(open(os.path.join(home, "state", "graph.json"), encoding="utf-8"))
            return g.get("nodes") or g.get("cards") or {}
        except (OSError, ValueError):
            return {}


def read_events(home, limit_lines=6000):
    p = os.path.join(home, "state", "events.log")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    return lines[-limit_lines:]


# ---------------- 落笔三面 ----------------

def trace(cfg, msg):
    line = "%s %s" % (time.strftime("%F %T"), msg)
    try:
        d = os.path.join(cfg["home"], "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "fuse.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def fuse_event(cfg, cid, old, new, extra):
    """events.log fuse 行（actor=fuse 固定；直接 append 旁证流，状态变更仍走 cards_dag 写闸）。
    dry=只报不落账：只打 DRY-EVENT 到本器圈迹，正账一行不写（否则手滑 dry 也污染台账）。"""
    if not cfg["apply"]:
        trace(cfg, "DRY-EVENT %s|%s->%s|fuse|%s" % (cid, old, new, extra))
        return
    try:
        ts = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        san = lambda s: (str(s) or "").replace("\r", " ").replace("\n", " ")
        p = os.path.join(cfg["home"], "state", "events.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s|%s|%s->%s|fuse|%s\n" % (ts, san(cid), san(old), san(new), san(extra)))
    except OSError as e:
        trace(cfg, "EVENT-WRITE-FAIL %r" % (e,))


FUSE_SELF_CID = os.environ.get("FUSE_SELF_CID") or "REQ-FLEET-FUSE-1"


def seg_event(cfg, extra):
    """段级处置痕（重启/唤醒/断流检测/未通电/业主红）——**必须挂真实卡号**：
    ops/reconcile_events.py 判据一把 events 第二段当卡号对账，伪卡号「FUSE」会被
    对成永久幽灵卡反复红（00:23 真账首圈实锤：ALARM events 有卡 FUSE 但事实图无此卡）。
    故挂保险丝机制自身卡 REQ-FLEET-FUSE-1，old/new 取卡面当前态=痕尾与卡面对齐，
    不触「裸改」红。卡被硬删（不在图也不在归档）时降级只走圈迹，绝不造新幽灵。"""
    st = str((cfg["cards"] or {}).get(FUSE_SELF_CID, {}).get("status") or "")
    if not st:
        trace(cfg, "SEG-EVENT-SKIP(自卡不在账) %s" % extra)
        return
    fuse_event(cfg, FUSE_SELF_CID, st, st, extra)


def alarm(cfg, msg):
    """红灯走 ccards.alerts 同账（看板告警面板/概览·待裁决现读；ack 停铃闭环现成）。
    dry=只报不落账：红灯不写（看板零污染），只在圈迹留 DRY-ALARM。"""
    if not cfg["apply"]:
        trace(cfg, "DRY-ALARM %s" % msg)
        return
    try:
        sys.path.insert(0, TEAM)
        from ccards import alerts
        alerts.record(msg)
    except Exception as e:
        trace(cfg, "ALARM-VIA-CCARDS-FAIL %r → 直写兜底" % (e,))
        try:
            p = os.path.join(cfg["home"], "state", "alerts.json")
            d = {}
            if os.path.isfile(p):
                d = json.load(open(p, encoding="utf-8")) or {}
            a = d.setdefault("alerts", {})
            key = " ".join(str(msg).split())[:200]
            e2 = a.get(key) or {"msg": key, "first": datetime.now().astimezone().isoformat(timespec="seconds"), "count": 0, "ack": None}
            e2["count"] = int(e2.get("count", 0)) + 1
            e2["last"] = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
            a[key] = e2
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"updated": e2["last"], "alerts": a}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except (OSError, ValueError) as e3:
            trace(cfg, "ALARM-DIRECT-FAIL %r" % (e3,))


def load_state(cfg):
    try:
        st = json.load(open(cfg["state_path"], encoding="utf-8"))
        if not isinstance(st, dict):
            st = {}
    except (OSError, ValueError):
        st = {}
    return st


def save_state(cfg, st):
    st["last_pass"] = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    tmp = cfg["state_path"] + ".tmp"
    os.makedirs(os.path.dirname(cfg["state_path"]), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, cfg["state_path"])


def act(cfg, seg, fn, *args, **kw):
    """处置唯一闸口：--apply 才真动；FUSE_NOACT 摘段=该段处置整段空转（红检缝）。"""
    if not cfg["apply"]:
        trace(cfg, "DRY[%s] %s（--apply 才执行）" % (seg, getattr(fn, "__name__", "fn")))
        return "DRY"
    if seg in cfg["noact"]:
        trace(cfg, "NOACT[%s] %s（红检注入：处置被摘）" % (seg, getattr(fn, "__name__", "fn")))
        return "NOACT"
    return fn(*args, **kw)


# ---------------- 共享处置动作 ----------------

def _ps_bin():
    linux = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    if os.path.exists(linux):
        return linux
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _to_win(p):
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p)
    if m:
        return "%s:\\%s" % (m.group(1).upper(), m.group(2).replace("/", "\\"))
    return os.path.abspath(p)


def _pid_alive_ext(pid):
    """进程活否=转 ccards.config.pid_alive（MECH-PIDALIVE-WIN-1 权威形）。
    绝不落 os.kill(pid,0)：Windows sig=0 恰是 CTRL_C_EVENT=广播进程组，探针即处决
    （BUG-FUSE-SELFKILL-1，26004 案 01:38:36 实证）。导不到=保守判活（宁拒不错杀）。"""
    try:
        return _cfg.pid_alive(pid)
    except Exception:
        return True


def _hb_resident(cfg):
    """读名册常驻件心跳首行 → (mode, pid, 龄s)；无件/读不出=(None, None, None)。
    心跳形如 `2026-11-04 01:15:27 ctrld mode=live pid=25752 board=- rings=...`。"""
    for r in cfg["residents"]:
        hb = r.get("hb_abs")
        if not hb or not os.path.isfile(hb):
            continue
        try:
            with open(hb, encoding="utf-8", errors="replace") as f:
                first = f.readline()
        except OSError:
            continue
        m_mode = re.search(r"mode=(\w+)", first)
        m_pid = re.search(r"pid=(\d+)", first)
        if not m_mode or not m_pid:
            continue
        age = None
        try:
            age = time.time() - time.mktime(time.strptime(first[:19], "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            pass
        return m_mode.group(1), int(m_pid.group(1)), age
    return None, None, None


def _swap_task(cfg):
    """cc-ctrld 安全换血（BUG-FUSE-SELFKILL-1②，替代旧『Stop;Start 同串』四连案配方）：
    ①收孤儿环/看板（复用 ctrld._reap_orphans 一把刀，防孤儿持组锁锁死新引擎）
    ②Stop ③等名册旧 pid 死透（≤20s）④Start ⑤验生=心跳 mtime 晚于换血起点 且 pid≠旧 且真活；
    验不上重来，≤3 轮。返回 rc（0=新引擎确生）。"""
    ps = _ps_bin()
    task = (os.environ.get("FUSE_CTRLD_TASK") or _cfg.CC_TASK_CTRLD).replace("'", "''")
    if not ps:
        return 127
    t0 = time.time()
    _, rpid, _ = _hb_resident(cfg)
    try:
        from ctrld import _reap_orphans
        _reap_orphans(cfg["home"])
    except Exception as e:
        trace(cfg, "SWAP-REAP-SKIP %r（不拦换血，孤儿锁候新引擎上岗自扫）" % (e,))

    def _task(action):
        try:
            subprocess.run([ps, "-NoProfile", "-Command",
                            "%s-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue" % (action, task)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30, creationflags=NO_WIN)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    _task("Stop")
    if rpid:
        deadline = time.time() + 20
        while time.time() < deadline and _pid_alive_ext(rpid):
            time.sleep(1)
    for _try in range(3):
        if not _task("Start"):
            continue
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(2)
            mode2, pid2, _ = _hb_resident(cfg)
            if mode2 == "live" and pid2 and pid2 != rpid and _pid_alive_ext(pid2):
                trace(cfg, "SWAP-OK 新引擎 pid=%d 确生（换血耗时%.0fs 第%d轮）" % (pid2, time.time() - t0, _try + 1))
                return 0
    trace(cfg, "SWAP-FAIL 三轮 Start 未见新引擎确生（%.0fs）——旧 Stop;Start 竞窗案的现代形" % (time.time() - t0))
    return 1


def run_restart(cfg, why):
    """重启常驻派发器：env FUSE_RESTART_CMD（shell）> Windows 计划任务 cc-ctrld > systemctl cc-dispatch。
    节流：两次自动重启间隔 ≥ FUSE_RESTART_THROTTLE_MIN（cron 每分钟拉，重启未愈不逐分钟锤）。
    自杀闸门（BUG-FUSE-SELFKILL-1，2026-11-04 四连案定性）：名册引擎 hb=live 且 pid 真活
    ⇒ REFUSED——重启通道打的就是它自己，活着时动手=处决健康引擎+吞复起；只报警留人工。"""
    st = cfg["state"]
    now = time.time()
    last = st.get("last_restart_ts") or 0
    if now - last < cfg["restart_throttle"] * 60:
        trace(cfg, "RESTART-THROTTLED（距上次%.0fs<%.0fs）why=%s" % (now - last, cfg["restart_throttle"] * 60, why))
        return "THROTTLED"
    mode, rpid, rage = _hb_resident(cfg)
    if mode == "live" and rpid and _pid_alive_ext(rpid):
        trace(cfg, "REFUSE-SELFKILL why=%s 名册引擎 pid=%d 仍活着（hb 龄=%s）——"
                   "重启通道=它自己，改只报不动手；断流根因走收孤儿/人工（防今夜四连案复发）" % (
                   why, rpid, ("%.0fs" % rage) if rage is not None else "?"))
        seg_event(cfg, "FUSE-REFUSE-SELFKILL 名册引擎活(%d)拒整任务重启 why=%s" % (rpid, why[:60]))
        if now - (st.get("last_refuse_warn") or 0) >= 600:
            alarm(cfg, "FUSE-REFUSE-SELFKILL %s：常驻引擎活着但自动处置被拒（断流未自愈需人工处置，勿纵孤儿环持锁）" % why)
            st["last_refuse_warn"] = now
        return "REFUSED"
    custom = (os.environ.get("FUSE_RESTART_CMD") or "").strip()
    if custom:
        target, shell = custom, True
    elif sys.platform == "win32":
        rc = _swap_task(cfg)
        st["last_restart_ts"] = now
        tgt_name = "task:" + _cfg.CC_TASK_CTRLD
        if rc == 0:
            seg_event(cfg, "FUSE-RESTART ok target=%s why=%s" % (tgt_name, why))
            trace(cfg, "RESTART-OK target=%s why=%s" % (tgt_name, why))
        else:
            seg_event(cfg, "FUSE-RESTART-FAIL rc=%s target=%s why=%s" % (rc, tgt_name, why))
            trace(cfg, "RESTART-FAIL rc=%s target=%s why=%s" % (rc, tgt_name, why))
        return "OK" if rc == 0 else "FAIL"
    else:
        unit = os.environ.get("FUSE_DISPATCH_UNIT", "cc-dispatch")
        target = ["systemctl", "--user", "restart", unit]
        shell = False
    try:
        p = subprocess.run(target, shell=shell, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=90, creationflags=NO_WIN)
        rc = p.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        trace(cfg, "RESTART-THROW %r" % (e,))
        rc = 127
    st["last_restart_ts"] = now
    tgt_name = (os.environ.get("FUSE_RESTART_CMD") and "env-cmd") or \
               ("task:" + _cfg.CC_TASK_CTRLD if sys.platform == "win32"
                else "systemd:" + os.environ.get("FUSE_DISPATCH_UNIT", "cc-dispatch"))
    if rc == 0:
        seg_event(cfg, "FUSE-RESTART ok target=%s why=%s" % (tgt_name, why))
        trace(cfg, "RESTART-OK target=%s why=%s" % (tgt_name, why))
    else:
        seg_event(cfg, "FUSE-RESTART-FAIL rc=%s target=%s why=%s" % (rc, tgt_name, why))
        trace(cfg, "RESTART-FAIL rc=%s target=%s why=%s" % (rc, tgt_name, why))
    return "OK" if rc == 0 else "FAIL"


def run_pm_wake(cfg, reason, seg):
    """叫 PM：env FUSE_PM_WAKE_CMD（shell，reason 经 PM_WAKE_REASON）> run-cc.ps1 lane=pmmail
    （与 inbox_watchdog.launch 同款通道）。冷却：两次拉起间隔 ≥ FUSE_WAKE_MIN_INTERVAL_MIN。"""
    st = cfg["state"]
    now = time.time()
    last = st.get("last_pm_wake") or 0
    if now - last < cfg["wake_min_interval"] * 60:
        trace(cfg, "PM-WAKE-SKIP-COOLDOWN（距上次%.0fs）reason=%s" % (now - last, reason))
        return "COOLDOWN"
    custom = (os.environ.get("FUSE_PM_WAKE_CMD") or "").strip()
    rc, how = 1, "-"
    if custom:
        env = dict(os.environ)
        env["PM_WAKE_REASON"] = reason
        try:
            rc = subprocess.run(custom, shell=True, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=90, creationflags=NO_WIN).returncode
            how = "env-cmd"
        except (OSError, subprocess.TimeoutExpired):
            how = "env-cmd"
    else:
        ps = _ps_bin()
        runner = os.environ.get("FUSE_PM_RUNNER") or _cfg.CC_RUNNER
        repo = os.path.dirname(cfg["home"])
        if ps and os.path.exists(runner):
            try:
                d = os.path.join(cfg["home"], "logs")
                os.makedirs(d, exist_ok=True)
                fn = os.path.join(d, "brief-pmmail-fuse-%s.txt" % time.strftime("%H%M%S"))
                with open(fn, "w", encoding="utf-8") as f:
                    f.write("你是ccPM。车队断流保险丝（cc-team/ops/fuse.py，REQ-FLEET-FUSE-1）"
                            "报告以下断流，请立即开工一轮处置并回执：\n%s\n" % reason)
                subprocess.Popen([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                  runner, "-Brief", _to_win(fn), "-Workdir", _to_win(repo),
                                  "-Lane", "pmmail"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=NO_WIN)
                rc, how = 0, "run-cc.ps1(pmmail)"
            except OSError:
                how = "run-cc.ps1(pmmail)"
        else:
            how = "无可用唤醒通道(ps=%s runner在否=%s)" % (bool(ps), os.path.exists(runner))
    if rc == 0:
        st["last_pm_wake"] = now
        st["pm_wake_count"] = int(st.get("pm_wake_count") or 0) + 1
        seg_event(cfg, "FUSE-%s-PM-WAKE via=%s reason=%s" % (seg, how, reason))
        trace(cfg, "PM-WAKE via=%s reason=%s" % (how, reason))
    else:
        alarm(cfg, "FUSE-%s-PM-WAKE-FAIL 叫PM通道不可用(%s)：%s" % (seg, how, reason))
        seg_event(cfg, "FUSE-%s-PM-WAKE-FAIL via=%s" % (seg, how))
    return "OK" if rc == 0 else "FAIL"


def gate(cfg, *args, self_trigger=""):
    """写闸调用 cards_dag（正账唯一写入口；CC_TEAM_HOME 已在 fuse 进程环境，子进程同账家）。
    self_trigger="fuse"：本笔 set 是保险丝自触发，痕尾经 env 带 " self=fuse" 显标，
    静默钟按标记剔除（BUG-FUSE-S5-SNIFF：文本嗅探误伤 PM 真人痕=假静默挂业主红条）。"""
    cmd = [sys.executable, "-I", os.path.join(TEAM, "cards_dag.py")] + [str(a) for a in args]
    env = dict(os.environ, CC_SELF_TRIGGER=self_trigger) if self_trigger else None
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WIN, env=env)
    out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip().splitlines()
    return p.returncode, (out[-1] if out else "")


# ---------------- 五段纯判据（fuse-check 单元面直测） ----------------

def s1_decide(hb_age_s, stale_s):
    """心跳缺失=断（没写=没活）；龄>阈值=断。返回 (stale, 显示值)。"""
    if hb_age_s is None:
        return True, "MISS"
    return hb_age_s > stale_s, ("%.0fs" % hb_age_s)


def died_index(log_texts):
    """扫 WORKER-DIED 行 → {cid: [(epoch, lane, 原文)]}（时间升序）。"""
    out = {}
    pat = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})\s+WORKER-DIED\s+([A-Za-z0-9._-]+)@([A-Za-z0-9._-]+)")
    for t in log_texts:
        for ln in t.splitlines():
            m = pat.search(ln)
            if not m:
                continue
            ep = parse_ts(m.group(1))
            if ep is None:
                continue
            out.setdefault(m.group(2), []).append((ep, m.group(3), ln.strip()))
    for v in out.values():
        v.sort()
    return out


def churn_index(event_lines):
    """recycle-release 翻台计数（BUG-POOL-SLOTCHURN-1④ S2 补面）：回收放流走的是
    events 行 `ts|cid|CLAIMED->OPEN|recycle|release`，**不落 WORKER-DIED**——09:30 轮
    12 台连环翻台 S2 在册=0 全程静默（盲区实锤）。本函把同形放流折进 S2 死次账：
    同卡窗内 recycle-release ≥2（中间无 DONE/VERIFIED 成迹，s2_decide 原语义）→ 停毒+红灯。
    道名取紧邻下一行同卡的 HANDOVER-OUT by=<道>（取不到="-"，不编）。
    who 只认 recycle：fuse 自身停毒行（actor=fuse）不计入，防自数自触发。"""
    out = {}
    for i, ln in enumerate(event_lines):
        f = ln.split("|")
        if len(f) < 5 or f[2] != "CLAIMED->OPEN" or f[3] != "recycle" or "release" not in f[4]:
            continue
        ep = parse_ts(f[0])
        if ep is None:
            continue
        lane = "-"
        nxt = event_lines[i + 1].split("|") if i + 1 < len(event_lines) else []
        if len(nxt) >= 5 and nxt[1] == f[1] and "HANDOVER-OUT" in nxt[4]:
            m = re.search(r"by=([A-Za-z0-9_.-]+)", nxt[4])
            lane = m.group(1) if m else "-"
        out.setdefault(f[1], []).append((ep, lane, ln.strip()))
    for v in out.values():
        v.sort()
    return out


def s2_decide(deaths, last_success_ts, now, window_s, need=2):
    """连续即死判停：只看**上次成功(DONE/VERIFIED)之后**的死；窗口内累计≥need 次即停毒。
    返回 (触发?, 窗口内死次, 说明)。"""
    seq = [d for d in deaths if (last_success_ts is None or d[0] > last_success_ts)
           and now - d[0] <= window_s]
    return len(seq) >= need, len(seq), "上次成迹后窗口内死%d次" % len(seq)


def s3_classify(dryrun_rc):
    """火车 b/c 判类（merge-tree 语义）：0=净(b 能合)，1=撞(c 待合)，其余=判不了(按挡处理不硬合)。"""
    if dryrun_rc == 0:
        return "b"
    if dryrun_rc == 1:
        return "c"
    return "?"


# 卡号 token 形态=大写段-连字符段（REQ-X-1 / UX-BOARD 类，commit-msg 钩子强制的 动词(卡号): 携带）。
# 纯 [A-Z]+数字 无连字符（S3/D02）不算——不误吞正文缩写造伪归属。
_CARDID_TOK_RE = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+")


def s3_tipscan_filter(cid, entries):
    """MECH-FUSE-S3-TIPSCAN-1 锚外归属过滤（纯判据，单元面直测）：
    entries=[(h, subj), ...]=卡锚之后至泳道树尖的后续 commit，留下者计入本卡「锚外候并」：
      subject 带本卡号             → 本卡多锚（二轮实锤 UX-BOARD-COLLAPSE-1 三笔半成品之首）
      不带任何卡号 token（无归属）→ 宁红不猜按本卡候并算（判不了=挡，与 s3_classify 同语义）
      只带别卡号                   → 泳道已挪去下一单，不算本卡头的账（不误挡可并卡）
    """
    kept = []
    for h, subj in entries or []:
        toks = _CARDID_TOK_RE.findall(subj or "")
        if not toks or cid in toks:
            kept.append((h, subj))
    return kept


def s3_tipscan_decide(followups, tip_known, done_events_n):
    """MECH-FUSE-S3-TIPSCAN-1 多锚扫描判定（纯判据；返回 (n_suspect, src)）：
      git 面实锤   锚外同卡/无归属 commit ≥1 笔 → (n, "git")=多锚存疑禁单锚自动并
      兜底面       树尖读不出(tip_known=False) 且 同卡交卷流水 >1 笔（events 多次 CLAIMED->DONE
                   =返工/换锚，卡面 commit 未必=域内末笔）→ (n, "events")
      其余         → (0, "") 照常并线路径。
    树尖读得出且锚外零命中=git 面已证单锚完整，账史不再三猜（否则返工过的卡永不可自动并）。"""
    if followups:
        return len(followups), "git"
    if not tip_known and done_events_n > 1:
        return done_events_n, "events"
    return 0, ""


def s4_decide(proc_start, lifelines, grace_s):
    """生效锁：命脉文件最后盘改时(mtime) 晚于 进程起时+宽限 = 旧进程没通电。
    返回滞后件清单 [(文件, 盘改时, 分钟)]。（BUG-FUSE-SELFKILL-1④：提交时口径作废。）"""
    lag = []
    for f, ct in lifelines:
        if proc_start is None or ct is None:
            continue
        if ct > proc_start + grace_s:
            lag.append((f, ct, (ct - proc_start) / 60.0))
    return lag


def last_pm_action_ts(event_lines):
    """events.log 里 actor=PM 的最后动作 epoch。
    fuse 触发 set 的落痕 who 被账本硬编成 "PM"（ccards 通道现状），痕尾带显式标记 " self=fuse"
    （BUG-FUSE-SNIFF 起由 env CC_SELF_TRIGGER 现打，见 ccards/commands.py cmd_set）——
    带标记的自触发痕**不算 PM 动作**（否则保险丝自己把静默钟拨回=自骗，S5 永远不响）。
    旧规则按 extra 含 "STOPLOSS:fuse" 字样剔除，实锤误伤真人：PM 停毒归因轮的 set diff 必然
    引用该字样，2026-11-03 20:35~20:38 归因收尾被整段剔掉=假静默，21:01 假 OWNER-RED 把业主
    红条真挂出去 20 分钟。无标历史旧痕按 PM 数（只放松旧账，不造新自骗）。
    同族潜伏口子如实备案：dispatch.py run_gate("set",...) 改道痕同样 actor=PM——通道已通用，
    要按同法带 self=dispatch 一行即接，本轮不扩面。"""
    best = None
    for ln in event_lines:
        f = ln.split("|")
        if len(f) < 4 or f[3].strip() != "PM":
            continue
        if ("|".join(f[4:]) if len(f) > 4 else "").rstrip().endswith("self=fuse"):
            continue
        ep = parse_ts(f[0])
        if ep is not None and (best is None or ep > best):
            best = ep
    return best


def s5_decide(now, pm_last_ts, backlog_n, idle_n, last_wake_ts, silent_s, escalate_s):
    """PM 缺位：候补在场 且 PM 静默>阈值。返回 (动作, 说明)。
    动作："" / "WAKE" / "WAKE+OWNER-RED"。冷却由 run_pm_wake 统一挡。"""
    cond = (backlog_n > 0 or idle_n > 2)
    pm_age = (now - pm_last_ts) if pm_last_ts else float("inf")
    if not cond or pm_age <= silent_s:
        return "", "候补=%d 空道=%d PM静默=%s（未触发）" % (
            backlog_n, idle_n, ("%.0fs" % pm_age) if pm_last_ts else "从未")
    woke_after_pm = last_wake_ts is not None and (pm_last_ts is None or last_wake_ts > pm_last_ts)
    if not woke_after_pm:
        return "WAKE", "PM静默%.0fmin 且无未决唤醒" % (pm_age / 60)
    if now - last_wake_ts > escalate_s:
        return "WAKE+OWNER-RED", "唤醒后%.0fmin 无 PM 动作" % ((now - last_wake_ts) / 60)
    return "", "已唤醒待响应（距唤醒%.0fmin）" % ((now - last_wake_ts) / 60)


def s6_decide(now, first_ts, acked, ack1_s, ack2_s):
    """报警升级闭环判据（信箱 seq=22 A 项业主拍板：分级告警+认领时效+自动升级=PagerDuty on-call 语义）。
    返回 (动作, 说明)。动作："" / "WAKE"（一级：叫 PM 班认领）/ "OWNER"（二级：直达业主信箱[待拍板]）。
    键级单发/唤醒冷却由 seg_s6 与 run_pm_wake 挡，本函数只出分级判定。"""
    if acked:
        return "", "已认领（ack 在册）"
    if first_ts is None:
        return "", "首响时间不可解析——宁漏不误"
    age = now - first_ts
    if age >= ack2_s:
        return "OWNER", "无人认领%.0fmin≥二级阈" % (age / 60)
    if age >= ack1_s:
        return "WAKE", "无人认领%.0fmin≥一级阈" % (age / 60)
    return "", "龄%.0fmin 未及阈" % (age / 60)


def s6_open(v):
    """S6 判开放=与 ccards.alerts.still_open 同式：无 ack 即开放；ack 后又续报(last>ack)=**复响仍开放**。
    旧口径「ack 字段非空=不升级」=续报掩蔽半边（BUG-ALARM-AUTOACK-MASK-1 附带面）：机器销账后问题复响、
    或同题反复到期被钉住(pinned，不带新 ack)的键都带着旧 ack 值被 S6 一概跳过=叫不醒。"""
    if not isinstance(v, dict):
        return False
    ack = v.get("ack")
    return (not ack) or ((v.get("last") or "") > str(ack))


# ---------------- 六段跑圈（效应面；全部经 act 闸） ----------------

def _read_tail_texts(paths, limit=4000):
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                out.append("\n".join(f.read().splitlines()[-limit:]))
        except OSError:
            continue
    return out


def seg_s1(cfg):
    hb = cfg["hb"]
    age = None
    try:
        age = time.time() - os.path.getmtime(hb)
    except OSError:
        pass
    stale, shown = s1_decide(age, cfg["hb_stale"] * 60)
    if not stale:
        trace(cfg, "S1 派发心跳 龄=%s（阈%.0fmin）OK" % (shown, cfg["hb_stale"]))
        return
    alarm(cfg, "FUSE-S1 派发心跳断流（dispatcher.heartbeat 龄=%s>%.0fmin）" % (shown, cfg["hb_stale"]))
    seg_event(cfg, "FUSE-S1-DETECT hb=%s 龄=%s" % (hb, shown))
    r = act(cfg, "s1", run_restart, cfg, "S1 心跳断流")
    if r == "FAIL":
        alarm(cfg, "FUSE-S1 重启失败——叫 PM 人工拉起派发器")
        act(cfg, "s1", run_pm_wake, cfg, "S1 派发心跳断流(%s)且自动重启失败" % shown, "S1")
    trace(cfg, "S1 心跳断流 龄=%s → 重启=%s" % (shown, r))


def seg_s2(cfg):
    texts = _read_tail_texts(cfg["died_logs"])
    events = read_events(cfg["home"])
    idx = died_index(texts)
    # BUG-POOL-SLOTCHURN-1④ S2 补面：recycle 放流不落 WORKER-DIED（09:30 轮 12 台翻台
    # 在册=0 全程静默实锤）——同卡 recycle-release 折进死次账，同窗同阈同处置。
    for cid, rows in churn_index(events).items():
        idx.setdefault(cid, []).extend(rows)
    for v in idx.values():
        v.sort()
    if not idx:
        trace(cfg, "S2 WORKER-DIED/recycle 翻台在册=0 扫描=%s" % ",".join(os.path.basename(p) for p in cfg["died_logs"]))
        return
    cards = cfg["cards"]
    for cid, deaths in sorted(idx.items()):
        c = cards.get(cid)
        if not c:
            continue
        if c.get("status") in ("DONE", "VERIFIED", "CANCELED", "HOLD"):
            continue  # 已收口/已停毒：不重复触停（幂等）
        if "STOPLOSS" in str(c.get("hold") or ""):
            continue
        last_ok = None
        for ln in events:
            f = ln.split("|")
            if len(f) >= 3 and f[1] == cid and (f[2].endswith("->DONE") or f[2].endswith("->VERIFIED")
                                               or "->DONE" in f[2] or "->VERIFIED" in f[2]):
                ep = parse_ts(f[0])
                if ep and (last_ok is None or ep > last_ok):
                    last_ok = ep
        hit, n, why = s2_decide(deaths, last_ok, time.time(), cfg["died_window"] * 60)
        trace(cfg, "S2 %s 窗口死次=%d(%s) 触发=%s" % (cid, n, why, hit))
        if not hit:
            continue

        def do_stoploss(cid=cid, c=c, n=n, deaths=deaths):
            if c.get("status") == "CLAIMED":
                rc, msg = gate(cfg, "release", cid, "--who", "fuse")
                # 痕尾必须与卡面真实态对账（reconcile 判据一）：放活成功=OPEN，失败仍 CLAIMED
                fuse_event(cfg, cid, "CLAIMED", "OPEN" if rc == 0 else "CLAIMED",
                           "FUSE-S2-STOPLOSS release(rc=%s) %s" % (rc, msg[:120]))
            hold = "STOPLOSS:fuse 连续%d次即死(窗口%.0fmin内 %s)停复派，等 PM 归因——防今晚三连死空转" % (
                n, cfg["died_window"], deaths[-1][2][:60])
            rc, msg = gate(cfg, "set", cid, "--status", "HOLD", "--hold", hold, self_trigger="fuse")
            if rc != 0:
                alarm(cfg, "FUSE-S2 停毒写闸被拒 %s：%s" % (cid, msg[:140]))
                return "GATE-REJECT"
            cfg["state"].setdefault("died_stoploss", {})[cid] = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
            alarm(cfg, "STOPLOSS(FUSE-S2) %s 连续%d次即死——已挂起停盲派" % (cid, n))
            fuse_event(cfg, cid, "OPEN", "HOLD", "FUSE-S2-STOPLOSS %s" % why)
            run_pm_wake(cfg, "S2 停毒：%s 连续%d次即死，已 release+HOLD(STOPLOSS)，需人工归因死因" % (cid, n), "S2")
            return "STOPPED"
        trace(cfg, "S2 %s 处置=%s" % (cid, act(cfg, "s2", do_stoploss)))


def _repo_git(cfg, repo, *args):
    try:
        p = subprocess.run([cfg["git"], "-C", repo] + list(args),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, creationflags=NO_WIN)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, "git 不可达/超时"


def _git(cfg, *args):
    return _repo_git(cfg, cfg["main_repo"], *args)


def _main_ancestor(cfg, commit):
    """-1=commit 对象在本仓不存在（挡车叫 PM，绝不当「非主干」硬合）；0=已在主干；1=未并。"""
    rc, _ = _git(cfg, "cat-file", "-e", commit + "^{commit}")
    if rc != 0:
        return -1
    rc, _ = _git(cfg, "merge-base", "--is-ancestor", commit, cfg["main_ref"])
    return 0 if rc == 0 else 1


def _done_events_count(cfg, cid):
    """events 里本卡真实交卷笔数（多锚兜底面素材）：只数 old→DONE 迁移痕（CLAIMED->DONE 等）；
    fuse 自身 DONE->DONE 处置/挡车痕 old==new 不计——否则兜底面自喂连环（S6 自触发键同纪律）。"""
    n = 0
    for l in read_events(cfg["home"]):
        f = l.split("|")
        if len(f) >= 3 and f[1].strip() == cid:
            old, sep, new = f[2].strip().partition("->")
            if sep and new == "DONE" and old != "DONE":
                n += 1
    return n


def _s3_tipscan_scan(cfg, c):
    """MECH-FUSE-S3-TIPSCAN-1 锚→树尖后续 commit 扫描（纯探测，判义在 s3_tipscan_decide）。
    「同 owner」扫描域=卡面交付仓 c["repo"]（REQ-MERGE-GATE-1 由 cmd_done 落账）里的交卷支
    done_anchor.branch；支名不可解/记录缺失=退该仓 HEAD。交付仓路径失踪（泳道已回收）=
    在主干仓复核同名支（worktree 共享对象库）。两仓都探不出树尖才 tip_known=False 走兜底判义。
    返回 (tip_known, [(h, subj), ...])；tip==锚 → (True, [])=正常单锚形。"""
    commit = str(c.get("commit") or "").strip()
    if not commit:
        return True, []
    anchor = c.get("done_anchor") or {}
    branch = str(anchor.get("branch") or "")
    if branch in ("", "HEAD", "detached") or branch == cfg["main_ref"]:
        branch = ""
    repos = []
    repo = str(c.get("repo") or "").strip()
    if repo and os.path.isdir(repo):
        repos.append(repo)
    if cfg["main_repo"] not in [os.path.abspath(x) for x in repos]:
        repos.append(cfg["main_repo"])
    for rp in repos:
        rc, full = _repo_git(cfg, rp, "rev-parse", "--verify", commit + "^{commit}")
        full = (full or "").strip()
        if rc != 0 or not re.fullmatch(r"[0-9a-f]{40}", full):
            continue
        for ref in ([branch] if branch else []) + ["HEAD"]:
            rc2, t2 = _repo_git(cfg, rp, "rev-parse", "--verify", ref + "^{commit}")
            t2 = (t2 or "").strip()
            if rc2 != 0 or not re.fullmatch(r"[0-9a-f]{40}", t2):
                continue
            if t2 == full:
                return True, []
            rc3, _ = _repo_git(cfg, rp, "merge-base", "--is-ancestor", full, t2)
            if rc3 == 0:
                rc4, out = _repo_git(cfg, rp, "log", "--max-count=200",
                                     "--pretty=format:%h\x09%s", "%s..%s" % (full, t2))
                if rc4 != 0:
                    return False, []
                entries = []
                for ln in (out or "").splitlines():
                    h, _, subj = ln.partition("\t")
                    if h.strip():
                        entries.append((h.strip(), subj.strip()))
                return True, entries
    return False, []


def seg_s3(cfg):
    now = time.time()
    backlog = 0
    for cid, c in sorted((cfg["cards"] or {}).items()):
        if c.get("status") != "DONE" or not c.get("commit"):
            continue
        anc = _main_ancestor(cfg, str(c["commit"]))
        if anc == 0:
            continue
        backlog += 1
        if anc == -1:
            alarm(cfg, "待合·缺锚(FUSE-S3) %s commit %s 不在主干仓 %s——需人工（泳道仓未共享对象/外部仓）" % (
                cid, str(c["commit"])[:12], cfg["main_repo"]))
            if cfg["state"].setdefault("s3_missing", {}).get(cid) is None:
                cfg["state"]["s3_missing"][cid] = now
                fuse_event(cfg, cid, "DONE", "DONE", "FUSE-S3-MISSING commit不在主干仓")
                act(cfg, "s3", run_pm_wake, cfg, "S3 交卷未并但主干仓查无 commit：%s @%s" % (cid, str(c["commit"])[:12]), "S3")
            continue
        done_ts = parse_ts(str(c.get("updated_at") or ""))
        age_min = ((now - done_ts) / 60.0) if done_ts else float("inf")
        if age_min <= cfg["unmerged_age"]:
            trace(cfg, "S3 %s 未并 %.0fmin（<阈%.0f，正常候合窗口）" % (cid, age_min, cfg["unmerged_age"]))
            continue
        dry_rc, dry_out = _git(cfg, "merge-tree", "--write-tree", cfg["main_ref"], str(c["commit"]))
        klass = s3_classify(dry_rc)
        if klass == "b":
            # MECH-FUSE-S3-TIPSCAN-1（二轮§五-1 遗留面；三轮§五-2 实锤「done 锚记 tip」之外仍漏后笔）：
            # 净=能合 ≠ 完整。卡锚只一笔、锚外同 owner 树尖还有候并 commit 的多锚半成品卡，
            # 按单锚自动并=残缺半边进主干（并折叠壳不并填行形）。判类后、真合前扫，命中≥1
            # 改判「待合·多锚」不自动并、叫 PM 人工定锚——宁叫人不误并。
            tip_known, followups = _s3_tipscan_scan(cfg, c)
            kept = s3_tipscan_filter(cid, followups)
            n_sus, ts_src = s3_tipscan_decide(kept, tip_known,
                                              _done_events_count(cfg, cid) if not tip_known else 0)
            if n_sus > 0:
                alarm(cfg, "待合·多锚(FUSE-S3) %s 卡面锚外另有 %d 笔候并 commit(%s面)——多锚卡禁单锚自动并，PM 请人工定锚" % (
                    cid, n_sus, ts_src))
                first = (cfg["state"].get("s3_tipscan") or {}).get(cid) is None
                def do_tipscan(cid=cid, c=c, n_sus=n_sus, ts_src=ts_src, kept=kept):
                    cfg["state"].setdefault("s3_tipscan", {})[cid] = now
                    head_h, head_s = (kept or [("-", "树尖读不出；同卡交卷流水多笔")])[0]
                    fuse_event(cfg, cid, "DONE", "DONE",
                               "FUSE-S3-TIPSCAN 多锚存疑(%s面) 锚外%d笔 首=%s %s 锚=%s" % (
                                   ts_src, n_sus, head_h, head_s[:60], str(c.get("commit"))[:12]))
                    run_pm_wake(cfg, "S3 多锚存疑：%s 卡面锚 %s 之外同 owner 另有 %d 笔候并 commit(%s面)——"
                                     "禁单锚自动并，请扫泳道树尖人工定锚（REQ-FLEET-FUSE-1 S3·二轮§五-1）" % (
                                     cid, str(c.get("commit"))[:12], n_sus, ts_src), "S3")
                    return "BLOCKED"
                res = act(cfg, "s3", do_tipscan) if first else "SEEN"
                trace(cfg, "S3 %s 多锚存疑(%s面 %d笔) 不自动并·候PM定锚 首报处置=%s" % (
                    cid, ts_src, n_sus, res))
                continue
            def do_merge(cid=cid, c=c, age_min=age_min):
                # 只查**被跟踪文件**的在途改动：真账仓常年有未跟踪杂物(tmp/、logs/、数据集)，
                # 把它们当「脏」=自动并线永动不了；而在途人工改动(如看板未提交)必须避让——叫 PM。
                rc, out = _git(cfg, "status", "--porcelain", "--untracked-files=no")
                if rc != 0 or out:
                    alarm(cfg, "FUSE-S3 主干工作树有在途人工改动/状态不可读，暂缓自动并线（%s）——叫 PM" % (out.splitlines() or ["?"])[0][:120])
                    fuse_event(cfg, cid, "DONE", "DONE", "FUSE-S3-MERGE-SKIP-DIRTY")
                    run_pm_wake(cfg, "S3 %s 能合但主干工作树脏，自动并线暂缓" % cid, "S3")
                    return "SKIP-DIRTY"
                rc, head = _git(cfg, "rev-parse", "--abbrev-ref", "HEAD")
                if rc != 0 or head.strip() != cfg["main_ref"]:
                    alarm(cfg, "FUSE-S3 主干仓 HEAD=%s≠%s，不在并线位——叫 PM" % (head[:40], cfg["main_ref"]))
                    return "SKIP-HEAD"
                rc2, _ = _git(cfg, "merge-tree", "--write-tree", cfg["main_ref"], str(c["commit"]))
                if s3_classify(rc2) != "b":
                    return "RACE-CONFLICT"  # 判类后主干又动了：本轮按撞处理，下圈重判
                msg = "merge(%s): fuse 自动并线（交卷未并%.0fmin·干跑净·REQ-FLEET-FUSE-1 S3）" % (cid, age_min)
                rc3, out3 = _git(cfg, "-c", "user.name=fuse-bot", "-c", "user.email=fuse@cc.local",
                                 "merge", "--no-ff", str(c["commit"]), "-m", msg)
                if rc3 != 0:
                    _git(cfg, "merge", "--abort")
                    alarm(cfg, "FUSE-S3 真合失败(已 abort) %s：%s——叫 PM" % (cid, out3.splitlines()[-1][:140] if out3 else "?"))
                    run_pm_wake(cfg, "S3 %s 干跑净但真合失败，主干零污染(abort)，需人工" % cid, "S3")
                    return "MERGE-FAIL"
                cfg["state"].setdefault("s3_merged", {})[cid] = now
                fuse_event(cfg, cid, "DONE", "DONE", "FUSE-S3-MERGED %.0fmin → %s" % (age_min, msg[:150]))
                trace(cfg, "S3 %s 自动并线 OK（age=%.0fmin）" % (cid, age_min))
                return "MERGED"
            res = act(cfg, "s3", do_merge)
            trace(cfg, "S3 %s b类(净) age=%.0fmin 处置=%s" % (cid, age_min, res))
        else:
            first = cfg["state"].setdefault("s3_blocked", {}).get(cid) is None
            if first:
                cfg["state"]["s3_blocked"][cid] = now
                fuse_event(cfg, cid, "DONE", "DONE", "FUSE-S3-BLOCKED c类撞车 age=%.0fmin" % age_min)
            alarm(cfg, "待合·撞车(FUSE-S3) %s 龄%.0fmin merge-tree 冲突——红灯挂起等 PM 人工解冲突，零force零改哈希" % (cid, age_min))
            if klass == "?":
                alarm(cfg, "待合·判类失败(FUSE-S3) %s merge-tree rc=%s：%s" % (cid, dry_rc, dry_out.splitlines()[-1][:120] if dry_out else "?"))
            act(cfg, "s3", run_pm_wake, cfg, "S3 %s 交卷未并且冲突(待合红灯已挂)，需人工合流" % cid, "S3")
            trace(cfg, "S3 %s c类(撞) age=%.0fmin 亮待合红灯" % (cid, age_min))
    cfg["backlog_unmerged"] = backlog


def seg_s4(cfg):
    for r in cfg["residents"]:
        name = r.get("name") or "?"
        hb = r.get("hb_abs")
        if not hb or not os.path.isfile(hb):
            trace(cfg, "S4 %s 无心跳件(%s)=不在岗，跳过" % (name, hb))
            continue
        try:
            first = open(hb, encoding="utf-8").readline()
        except OSError:
            continue
        m = re.search(r"pid=(\d+)", first)
        if not m:
            trace(cfg, "S4 %s 心跳无 pid（%s）跳过" % (name, first.strip()[:80]))
            continue
        pid = int(m.group(1))
        custom = (os.environ.get("FUSE_PROBE_START_CMD") or "").strip()
        if custom:
            env = dict(os.environ)
            env["FUSE_PROBE_PID"] = str(pid)
            try:
                p = subprocess.run(custom, shell=True, env=env, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60, creationflags=NO_WIN)
                proc_start = float(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else None
            except (OSError, ValueError, subprocess.TimeoutExpired):
                proc_start = None
        elif sys.platform == "win32":
            ps = _ps_bin()
            proc_start = None
            if ps:
                try:
                    q = subprocess.run([ps, "-NoProfile", "-Command",
                                        "[DateTimeOffset]::new((Get-Process -Id %d).StartTime).ToUnixTimeSeconds()" % pid],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=NO_WIN)
                    proc_start = float(q.stdout.strip()) if q.returncode == 0 and q.stdout.strip() else None
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    proc_start = None
        else:
            try:
                proc_start = os.stat("/proc/%d" % pid).st_mtime
            except OSError:
                proc_start = None
        if proc_start is None:
            trace(cfg, "S4 %s pid=%s 起时探不到（进程已死？归 S1 通道）" % (name, pid))
            continue
        lifelines = []
        rel_root = cfg["main_repo"]
        for f in (r.get("lifelines") or []):
            absf = os.path.join(cfg["home"], f) if not os.path.isabs(f) else f
            if not os.path.isfile(absf):
                continue
            try:
                lifelines.append((f, os.path.getmtime(absf)))   # 盘改时（旧口径 git 提交时=00:28 错杀案）
            except OSError:
                continue
        lag = s4_decide(proc_start, lifelines, cfg["energize_grace"] * 60)
        if not lag:
            trace(cfg, "S4 %s 生效锁绿（进程起=%s 命脉%d件已核对）" % (
                name, time.strftime("%F %T", time.localtime(proc_start)), len(lifelines)))
            continue
        files_s = ",".join("%s(+%.0fmin)" % (f, min_) for f, _, min_ in lag)
        alarm(cfg, "NOT-ENERGIZED(FUSE-S4) %s 进程起时早于命脉最后盘改=%s——修复≠生效" % (name, files_s))
        seg_event(cfg, "FUSE-S4-NOT-ENERGIZED %s %s" % (name, files_s))
        if not r.get("whitelist"):
            trace(cfg, "S4 %s 非白名单，只报警不自愈" % name)
            continue

        def do_energize(name=name, files_s=files_s):
            res = run_restart(cfg, "S4 %s 未通电(%s)" % (name, files_s))
            if res == "FAIL":
                alarm(cfg, "FUSE-S4 %s 自动重启失败——叫 PM" % name)
                run_pm_wake(cfg, "S4 %s 未通电且自动重启失败(%s)" % (name, files_s), "S4")
            return res
        trace(cfg, "S4 %s 未通电 %s → 重启=%s" % (name, files_s, act(cfg, "s4", do_energize)))


def seg_s5(cfg):
    now = time.time()
    cards = cfg["cards"] or {}
    evl = read_events(cfg["home"])
    pm_last = last_pm_action_ts(evl)
    idle = 0
    lanes = set(registry_lanes(cfg["home"]))
    busy = {str(c.get("owner")) for c in cards.values() if c.get("status") == "CLAIMED"}
    idle = len([l for l in lanes if l not in busy]) if lanes else 0
    backlog = int(cfg.get("backlog_unmerged", 0))
    st = cfg["state"]
    action, why = s5_decide(now, pm_last, backlog, idle, st.get("last_pm_wake"),
                            cfg["pm_silent"] * 60, cfg["pm_escalate"] * 60)
    trace(cfg, "S5 候补=%d 空道=%d PM静默=%s → %s（%s）" % (
        backlog, idle, ("%.0fmin" % ((now - pm_last) / 60)) if pm_last else "从未", action or "OK", why))
    # 产出脉搏（业主 2026-11-03 01:4x「系统检测……没有任何作用」「先跑通主流程」）：旧 S5 只盯
    # 「人有没有出事」，从不看「线有没有出活」——11-03 凌晨 14 道全空、候补断供 5 张，每圈仍打 OK。
    # 本行**纯观测不动账**（不新增处置支——规则已多）：吞吐退化先让数字每分钟在场，判断归 PM/看板。
    d60 = v60 = 0
    for ln in evl[-3000:]:   # 1h 真迁移可能少于窗口；火车重跑行按 X->X 不计（见下注释）
        f = ln.split("|")
        if len(f) < 3 or "->" not in f[2]:
            continue
        ep = parse_ts(f[0])
        if ep is None or now - ep > 3600:
            continue
        parts = f[2].split("->")
        frm, to = parts[0].strip(), parts[1].strip()
        # 只数真迁移（X->DONE）：火车轮询的 DONE->DONE 重跑行不是新交卷（02:2x 实锤
        # 15 张卡待验收刷屏把「交卷」冲到 61/时=虚高假数，观测行自己先失真比没有更糟）
        if to == "DONE" and frm != to:
            d60 += 1
        elif to == "VERIFIED" and frm != to:
            v60 += 1
    # 无牌=OPEN 但 lanes 空且非 ANY 池：调度器按道牌/池名单找人，这类卡对它是隐形的
    # （02:2x 实锤：三张 00:3x 发的机制卡 lanes=[] 全站空闲 9 分钟无人点破，
    #  「停止工作」不是机器死是卡没门牌——数字必须每分钟在场，别等业主来问）。
    nolist = sum(1 for c in cards.values() if c.get("status") == "OPEN"
                 and not [str(x).strip() for x in (c.get("lanes") or [])])
    trace(cfg, "S5 产出 1h 交卷=%d 收口=%d 在制=%d 候补=%d 无牌=%d（观测不动账）" % (
        d60, v60, sum(1 for c in cards.values() if c.get("status") == "CLAIMED"),
        sum(1 for c in cards.values() if c.get("status") == "OPEN"), nolist))
    if not action:
        # PM 在被唤后有真动作=缺位episode自然闭合（告警账自身停铃靠 ack/复响语义）
        if st.get("owner_red_open") and pm_last and st.get("last_pm_wake") and pm_last > st["last_pm_wake"]:
            st.pop("owner_red_open", None)
        return
    if action.startswith("WAKE"):
        act(cfg, "s5", run_pm_wake, cfg, "S5 PM 缺位：%s（候补%d/空道%d）" % (why, backlog, idle), "S5")
    if action.endswith("OWNER-RED"):
        def do_red(why=why, backlog=backlog, idle=idle):
            msg = "业主:需要人工介入 FUSE-S5 PM被唤后%.0fmin无响应（候补%d·空道%d）" % (
                cfg["pm_escalate"], backlog, idle)
            alarm(cfg, msg)
            seg_event(cfg, "FUSE-S5-OWNER-RED %s" % why)
            try:
                p = os.path.join(cfg["home"], "state", "fuse-owner-red.json")
                cur = {}
                if os.path.isfile(p):
                    cur = json.load(open(p, encoding="utf-8")) or {}
                cur.update({"since": cur.get("since") or datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "updated": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "msg": msg, "count": int(cur.get("count") or 0) + 1})
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cur, f, ensure_ascii=False, indent=1)
                os.replace(tmp, p)
            except OSError as e:
                trace(cfg, "OWNER-RED 落盘失败 %r" % (e,))
            st["owner_red_open"] = True
            return "RED"
        trace(cfg, "S5 升级=%s" % act(cfg, "s5", do_red))


def seg_s6(cfg):
    """S6 报警→处置升级闭环（信箱 seq=22 A 项，业主拍板=系统保障 PM 常责不立卡）：
    alerts 无主红灯 龄≥alert_ack1(30min) → 叫 PM 班认领；仍无主 龄≥alert_ack2(60min) →
    直达业主信箱 PM-REPLIES [待拍板]（主助理转发通道，append-only）。键级单发：每键至多
    唤一次、直达一次，ack/退账即清标。自触发键（「业主」头或「FUSE-S6」头）不参与——防升级环
    自己喂自己连环。账读不出=本圈跳过不假报（同 S3 保守口径）。
    续报口径(BUG-ALARM-AUTOACK-MASK-1 附带面)：开放判据=alerts.still_open 同式（ack 后又续报
    last>ack=复响仍开放），不再拿「ack 字段非空」当已处置——旧口径把机器销账后复响/被钉住的键
    一并跳过=叫不醒；被钉住键的唤语文案带「同题一夜自动销账 N 次」点名。"""
    st = cfg["state"]
    s6 = st.setdefault("s6", {"woken": {}, "owner": {}})
    p = os.path.join(cfg["home"], "state", "alerts.json")
    try:
        rec = (json.load(open(p, encoding="utf-8")) or {}).get("alerts") or {}
    except (OSError, ValueError) as e:
        trace(cfg, "S6 报警账读不出，本圈跳过（不假报）%r" % (e,))
        return
    now = time.time()
    live = set()
    fired = 0
    for key in sorted(rec):
        v = rec.get(key)
        if not isinstance(v, dict):
            continue
        ep = parse_ts(v.get("first"))
        if not s6_open(v):
            s6["woken"].pop(key, None)
            s6["owner"].pop(key, None)
            continue
        if ep is None:
            continue  # 开放但首响不可解析：s6_decide 本就宁漏不误，这里不提前清标（键还活着）
        live.add(key)
        # 自触发排除：业主头（升级直达自挂的红）与 FUSE-S6 头（本器唤醒失败报警）不转发——
        # 只认这两类稳定前缀，别的键含「S6」字样照常升级（测试键 S6-TEST 即此类）。
        if key.startswith("业主") or key.startswith("FUSE-S6"):
            continue
        # 钉住键点名带上机器销账次数（报警面=唤醒文案里也要看得见「一夜自动销账 N 次」）
        pin_tag = ("（同题一夜自动销账%s次·反复到期钉住点名）" % v.get("pin_acks_day")
                   if v.get("pinned") else "")
        action, why = s6_decide(now, ep, False, cfg["alert_ack1"] * 60, cfg["alert_ack2"] * 60)
        if action == "WAKE" and key not in s6["woken"]:
            r = act(cfg, "s6", run_pm_wake,
                    cfg, "S6 报警%.0fmin 无人认领：%s%s —— PM 班请认领（cards_dag ack 或入账处置）" % (
                        (now - ep) / 60, key[:120], pin_tag),
                    "S6")
            if r == "OK":
                s6["woken"][key] = int(now)
                fired += 1
        elif action == "OWNER" and key not in s6["owner"]:
            def do_owner(key=key, ep=ep, v=v, why=why):
                ts = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
                block = ("\n## [待拍板] fuse-S6 报警升级直达（机件自动 %s）\n"
                         "报警键：%s\n首次：%s，已 %.0fmin 无人认领；一级(%.0fmin)已叫 PM 班，二级(%.0fmin)直达本信箱。\n"
                         "%s"
                         "处置口径：cards_dag ack <键> 停铃，或入账处置后回邮。\n" % (
                             ts, key, v.get("first") or "?", (now - ep) / 60,
                             cfg["alert_ack1"], cfg["alert_ack2"],
                             (("反复到期：同题一夜自动销账 %s 次，已被钉住（不再静默销账）。\n"
                               % v.get("pin_acks_day")) if v.get("pinned") else "")))
                ip = os.path.join(cfg["home"], "inbox", "PM-REPLIES.md")
                try:
                    os.makedirs(os.path.dirname(ip), exist_ok=True)
                    with open(ip, "a", encoding="utf-8") as f:
                        f.write(block)
                except OSError as e:
                    trace(cfg, "S6 直达业主信箱落盘失败 %r" % (e,))
                    return "FAIL"
                alarm(cfg, "业主:S6 报警无主超时升级直达：%s" % key[:120])
                return "RED"
            r = act(cfg, "s6", do_owner)
            if r == "RED":
                s6["owner"][key] = int(now)
                seg_event(cfg, "FUSE-S6-OWNER key=%s" % key[:120])
                fired += 1
            elif r == "FAIL":
                continue
        # WAKE 一级的 FUSE-S6-PM-WAKE 痕由 run_pm_wake 内部 seg_event 落（自带 via/reason），此处不重复
    # 退账/认领键清标（防同名新报警被旧标静默）——上面的 pop 已按 ack 处理，这里收退账键
    for m in ("woken", "owner"):
        for k2 in [x for x in s6[m] if x not in live]:
            s6[m].pop(k2, None)
    trace(cfg, "S6 报警升级 无主键=%d 本圈升级=%d" % (len(live), fired))


# ---------------- 跑圈装配 ----------------

# 命脉=**进程内 import 链**（引擎重启才有新的东西），不是"这个岗管得着的脚本"。
# 旧全列（dispatch/recycle/pool/memgate/cards_dag）=结构假阳：这些脚本由 ctrld 各环
# **每拍子进程**拉起（ctrld.py:175-183 `[py,-I,ops/dispatch.py --once]` 等），盘改即效，
# 拿它们的 mtime 去比引擎进程起时，只要引擎比脚本旧就恒红——2026-11-04 05:1x 实锤
# NOT-ENERGIZED x21 连拍全属此族（dispatch.py(+194min) 早已被下一拍子进程吃到）。
# ctrld 进程内只 import ccards.config（ccards/__init__.py 无码），其余全 stdlib 惰性导入。
DEFAULT_RESIDENTS = [
    {"name": "cc-dispatch", "hb": "logs/ctrld.heartbeat", "whitelist": True,
     "lifelines": ["ops/ctrld.py", "ccards/config.py", "ccards/__init__.py"]},
]


def build_cfg(apply_mode, home=None, repo=None):
    # home/repo 显式参数 > env > 脚本自身仓回退。计划任务环境**没有** CC_TEAM_HOME
    # （00:20:22 首发实锤：回退到工作树仓=空账本，PM 静默误判"从未"错发一次唤醒），
    # 所以 --install 必须把当次解析出的绝对路径烘进任务参数（见 install_task）。
    home = os.path.abspath(home) if home else team_home()
    # 把解析结果烘回本进程环境：ccards 访问器与 cards_dag 子进程(gate)都按 env 定位账家。
    # 不烘=计划任务(无 CC_TEAM_HOME)读脚本所在仓的空账——00:20/00:23 首发双实锤。
    os.environ["CC_TEAM_HOME"] = home
    hb = (os.environ.get("FUSE_HB") or os.environ.get("DISPATCH_HB")
          or os.path.join(os.path.dirname(os.path.dirname(home)), ".cc", "state", "dispatcher.heartbeat"))
    residents = DEFAULT_RESIDENTS
    try:
        if os.environ.get("FUSE_RESIDENTS_JSON"):
            residents = json.loads(os.environ["FUSE_RESIDENTS_JSON"])
    except ValueError:
        pass
    for r in residents:
        h = r.get("hb")
        r["hb_abs"] = os.path.join(home, h) if h and not os.path.isabs(h) else (h or None)
    died_logs = env_list("FUSE_LOGS", os.path.join(home, "logs", "dispatch.log") + ","
                         + os.path.join(home, "logs", "ctrld.log"))
    cfg = {
        "home": home,
        "hb": hb,
        "died_logs": died_logs,
        "main_repo": os.path.abspath(repo or os.environ.get("FUSE_MAIN_REPO") or os.path.dirname(home)),
        "main_ref": os.environ.get("FUSE_MAIN_REF") or "main",
        "git": os.environ.get("FUSE_GIT") or "git",
        "state_path": os.path.join(home, "state", "fuse.json"),
        "apply": apply_mode,
        "noact": set(x.strip().lower() for x in (os.environ.get("FUSE_NOACT") or "").split(",")) - {""},
        "hb_stale": env_num("FUSE_HB_STALE_MIN", 3),
        "died_window": env_num("FUSE_DIED_WINDOW_MIN", 60),
        "unmerged_age": env_num("FUSE_UNMERGED_AGE_MIN", 30),
        "pm_silent": env_num("FUSE_PM_SILENT_MIN", 25),
        "pm_escalate": env_num("FUSE_PM_ESCALATE_MIN", 30),
        "wake_min_interval": env_num("FUSE_WAKE_MIN_INTERVAL_MIN", 10),
        "alert_ack1": env_num("FUSE_ALERT_ACK1_MIN", 30),
        "alert_ack2": env_num("FUSE_ALERT_ACK2_MIN", 60),
        "restart_throttle": env_num("FUSE_RESTART_THROTTLE_MIN", 4),
        "energize_grace": env_num("FUSE_ENERGIZE_GRACE_MIN", 2),
        "residents": residents,
    }
    cfg["state"] = load_state(cfg)
    cfg["cards"] = all_cards_safe(home)
    return cfg


def run_pass(apply_mode, home=None, repo=None):
    cfg = build_cfg(apply_mode, home=home, repo=repo)
    trace(cfg, "FUSE 起圈 mode=%s home=%s repo=%s noact=%s" % (
        "APPLY" if apply_mode else "dry", cfg["home"], cfg["main_repo"],
        ",".join(sorted(cfg["noact"])) or "-"))
    for seg in (seg_s1, seg_s2, seg_s3, seg_s4, seg_s5, seg_s6):
        try:
            seg(cfg)
        except Exception as e:
            # 单段崩≠整圈死（dispatch TICK-ERR 同纪律）：响亮留痕，下圈自愈重试
            trace(cfg, "SEG-ERR %s %r" % (seg.__name__, e))
            alarm(cfg, "FUSE-SELF 段崩(%s): %r" % (seg.__name__, str(e)[:120]))
    cfg["state"]["pass_count"] = int(cfg["state"].get("pass_count") or 0) + 1
    try:
        save_state(cfg, cfg["state"])
    except OSError as e:
        trace(cfg, "STATE-WRITE-FAIL %r" % (e,))
    return 0


# ---------------- 巡逻通道接线（应急备用：OS 计划任务 cc-fuse） ----------------
# 2026-11-03 业主令周期任务一律收进常驻进程（cc-ctrld 内 fuse 环每分拉起），本 OS 任务
# 已注销；--install/--uninstall 仅留作「ctrld 本身挂了要外挂哨兵」时的手动应急口。

def _ps_run(script):
    ps = shutil.which("powershell.exe") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    import base64
    b64 = base64.b64encode(script.encode("utf-16-le")).decode()
    p = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", b64],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WIN)
    out = ((p.stdout or "") + (p.stderr or "")).replace("\r", "")
    return p.returncode, out


def install_task(live=True, home=None, repo=None):
    """注册 cc-fuse：每分拉起 `python -I fuse.py --apply`（live）或只报（dry）一圈。
    每圈=新进程，改 fuse.py 即时生效——S4 抓的「修好≠通电」对本器自身结构性不存在。
    动作指向**运行本命令的仓**里的 fuse.py，并把当次解析出的 --home/--repo 绝对路径
    烘进任务参数（计划任务环境无 CC_TEAM_HOME，不烘=读错账本，00:20 首发实锤过）。
    主干合入后请到主干仓重跑 --install 重钉（幂等：先 Unregister 再 Register）。
    IgnoreNew=分钟圈不叠影；PT0S=合并不限时。"""
    task = _cfg.setting("tasks.fuse", "FUSE_TASK_NAME", "cc-fuse")
    py = sys.executable
    fuse_path = os.path.join(OPS, "fuse.py").replace("/", os.sep)
    cfg_probe = build_cfg(False, home=home, repo=repo)
    args = '"%s"%s --home "%s" --repo "%s"' % (
        fuse_path, " --apply" if live else "",
        cfg_probe["home"], cfg_probe["main_repo"])
    desc = ("%s — REQ-FLEET-FUSE-1 断流保险丝五段巡逻（S1派发心跳/S2即死停毒/S3交卷自动并线/"
            "S4生效锁/S5PM缺位升级），每分一圈%s。勿随手删：删了=「哪段停了哪段自动救」机制失效"
            "（首案 2026-11-02 全队列停摆1h50m）。重注册: python -I cc-team/ops/fuse.py --install%s；"
            "注销: --uninstall；水位: --status") % (
        task, "真处置(--apply)" if live else "只报(dry)", "" if live else " --apply")
    script = """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$name = '@TASK@'
$action = New-ScheduledTaskAction -Execute '@PY@' -Argument '@ARGS@' -WorkingDirectory '@WD@'
$trig = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -Hidden -StartWhenAvailable `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$settings.ExecutionTimeLimit = 'PT0S'
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trig -Settings $settings -Principal $principal -Description '@DESC@' | Out-Null
Write-Output "registered=$name"
(Get-ScheduledTask -TaskName $name).Actions | ForEach-Object { "action=" + $_.Execute + ' ' + $_.Arguments }
"next=" + (Get-ScheduledTaskInfo -TaskName $name).NextRunTime
"""
    script = (script.replace("@TASK@", task).replace("@PY@", py)
              .replace("@ARGS@", args.replace("'", "''")).replace("@WD@", os.path.dirname(TEAM))
              .replace("@DESC@", desc.replace("'", "''")))
    rc, out = _ps_run(script)
    print(out.strip())
    if rc != 0 or ("registered=%s" % task) not in out:
        print("INSTALL=FAIL rc=%s" % rc)
        return 1
    print("INSTALL=PASS task=%s mode=%s 每分一圈（重钉=到目标仓重跑 --install）" % (
        task, "live" if live else "dry"))
    return 0


def uninstall_task():
    task = _cfg.setting("tasks.fuse", "FUSE_TASK_NAME", "cc-fuse")
    script = ("$ErrorActionPreference='SilentlyContinue'\n"
              "Unregister-ScheduledTask -TaskName '%s' -Confirm:$false\n"
              "if (Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue) "
              "{ \"still=\" + '%s' } else { \"unregistered=%s\" }\n" % (task, task, task, task))
    rc, out = _ps_run(script)
    print(out.strip())
    return 0 if rc == 0 and "unregistered=" in out else 1


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="fuse.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="真处置（缺省 dry 只报不动）")
    ap.add_argument("--status", action="store_true", help="读本器水位（state/fuse.json 摘要）")
    ap.add_argument("--install", action="store_true",
                    help="注册巡逻通道 cc-fuse（每分一圈；配合 --apply=live）")
    ap.add_argument("--uninstall", action="store_true", help="注销 cc-fuse 计划任务")
    ap.add_argument("--home", metavar="PATH",
                    help="台账家（缺省=CC_TEAM_HOME 或脚本自身仓；--install 会把解析值烘进任务）")
    ap.add_argument("--repo", metavar="PATH", help="主干仓库（缺省=home 的上一层；同上烘参）")
    a = ap.parse_args()
    if a.install:
        return install_task(live=a.apply, home=a.home, repo=a.repo)
    if a.uninstall:
        return uninstall_task()
    if a.status:
        st = load_state(build_cfg(False, home=a.home, repo=a.repo))
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return 0
    return run_pass(a.apply, home=a.home, repo=a.repo)


if __name__ == "__main__":
    sys.exit(main())
