#!/usr/bin/env python3
# slot-churn-test.py — BUG-POOL-SLOTCHURN-1 保护形常驻红绿锁（PM 直管代落最小形）。
# 病灶（2026-11-03 09:30 轮实锤，docs/机制-保险丝S5处置-20261103-0930轮.md）：
#   beat 在账 → recycle.lane_alive 全权（MECH-INT-BEAT13A-1），而 L297 plateau>=2 硬编 +
#   brief 未教频度规 ⇒ worker 首拍后无续拍、转录/工作树明明在续写仍被放流
#   （活体 slot-25：首拍33s、移交 n=13 真增量，仍整 3min0s 被杀；12 槽/小时零产出）。
# 修复形：ops/recycle.py beatzombie_exempt —— 放流前问第三信号（转录 mtime / 工作树
#   mtime 任一在 RECYCLE_TRANSCRIPT_GRACE_MIN(默认15，2026-11-03 误杀归因轮由 5 上调，
#   与 CARD_STALE_MIN 同轨；证据 docs/机制-车队保险丝S2归因-20261103-openclaw误杀.md) 窗内续写 → 保活不放流），
#   挂点①ZOMBIE(beat-stale 含「零上报 age=∞」)/②legacy-noepoch。
# 纪律（守卫要验过它红得起来）：绿=闸在救命；红=同场景闸关(grace=0)必杀、双源真冻必杀
#   ——空检查比没有检查更糟，故每挂点都带「撤修复必红」自证。
# 跑法: python -I tests/slot-churn-test.py   （exit 0 + 尾行 SLOT_CHURN_LOCK_PASS = 全绿）
# 沙箱独立账家（CC_SLOTCHURN_HOME 可移，火车/并跑防互踩），不碰真实台账；
# 转录/工作树夹具经 DISPATCH_PROJ/DISPATCH_CODE_ROOT 测试锚钉进沙箱（与 recycle.py 同字）。
# ③④ 段（slot-29 续做，卡面验收A）：G/R/F/L/W 五段=PM 代落保护形①②的锁，原样保留；
#   后段钉 ③放流同轮杀旧会话防幽灵（X/Y/Z/E + R③进程内撤修复必红）、
#   ④fuse S2 补面 recycle-release 折进死次账（K/N + R④撤合并必红）、
#   U churn_index 解析单元面、B brief 频度规静态面、S 单锁不并存纪律面、
#   P plateau 可配面（env LANE_PLATEAU_DEATH 升阈救命/同形不设 env 必杀/下限2 回退）。
#   ③④ 处置本体断言=撤即红（NOACT/源变异两式），全程沙箱桩，零真 CIM、零真唤醒。
import json, os, shutil, subprocess, sys, tempfile, time, types

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # cc-team 根
HOME = os.environ.get("CC_SLOTCHURN_HOME") or os.path.join(
    tempfile.gettempdir(), "cc-slotchurn")                                  # 系统临时区（Win/WSL 双活，不落仓不污染 git status）
PROJ = os.path.join(HOME, "proj")
CODE = os.path.join(HOME, "code")
# 终跑 E/R④ 双红教训（时序针，非生产码问题）：ccards.config.TEAM=env CC_TEAM_HOME 在
# **import 时一次定值**（config.py:10），而 recycle.py 顶层 L120/125 就 from ccards import——
# P 款 _exec_ns(recycle) 若先于 env 设置跑，TEAM 被钉到真实账家：进程内 card_of→peek 查无卡
# lane=None（E 红），seg_s2 cards.get 缺→静默 continue 零 trace 零 HOLD（R④ 红）。
# 沙箱 env 必须前置到本行位置=任何 in-process import 之前。子进程面走 env() 不受影响。
os.environ["CC_TEAM_HOME"] = HOME
fails = []


def ok(name, extra=""):
    print("PASS:", name, extra)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, detail)


def env(extra=None):
    e = dict(os.environ)
    e.update({"CC_TEAM_HOME": HOME, "DISPATCH_PROJ": PROJ, "DISPATCH_CODE_ROOT": CODE,
              "PYTHONIOENCODING": "utf-8"})
    e.update(extra or {})
    return e


def sh(args, extra_env=None, timeout=120):
    r = subprocess.run([sys.executable, "-I"] + args, cwd=SRC, env=env(extra_env),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def card(args, extra_env=None):
    return sh([os.path.join(SRC, "cards_dag.py")] + args.split(), extra_env)


def recycle(args):
    return sh([os.path.join(SRC, "ops", "recycle.py")] + args.split())


def gcard(cid):
    g = json.load(open(os.path.join(HOME, "state", "graph.json"), encoding="utf-8"))
    return g["cards"].get(cid, {})


def utime(path, minutes_ago):
    t = time.time() - minutes_ago * 60
    os.utime(path, (t, t))


def _tp_dir(owner):
    # REQ-OSS-ENGINE-1：转录目录=与引擎 recycle.transcript_age_min 同一组合式现算
    # （DISPATCH_CODE_ROOT 锚与 env() 传的 CODE 同值；禁拼宿主字面量——oss-release-check A 判据盯此）。
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    from ccards import config
    return os.path.join(PROJ, config.proj_dir_name(CODE, owner))


def fresh_transcript(owner, age_min=0.0):
    d = _tp_dir(owner)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "sess.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"content": "在干活"}}) + "\n")
    utime(p, age_min)
    return p


def seed_beat(owner, epoch, ring, cid, plateau_prev):
    """复刻现场：state/lane-<owner>.beat 在账（本任期已有一拍）；
    recycle-beat.json 观察账 seeded 到 plateau_prev/ring —— 下一次评估
    lane_alive 即算出 plateau_prev+1（圈号持平）。plateau_prev=1 ⇒ 恰杀（≥2）。"""
    now = time.time()
    with open(os.path.join(HOME, "state", "lane-%s.beat" % owner), "w", encoding="utf-8") as f:
        json.dump({"lane": owner, "epoch": epoch, "ring": ring, "cid": cid,
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                   "t": now}, f)
    with open(os.path.join(HOME, "state", "recycle-beat.json"), "w", encoding="utf-8") as f:
        json.dump({owner: {"epoch": epoch, "ring": ring, "plateau": plateau_prev, "cid": cid}}, f)


def mk_claim(cid, owner):
    shutil.rmtree(HOME, ignore_errors=True)
    os.makedirs(os.path.join(HOME, "state"), exist_ok=True)
    rc, out = card("create %s --title 池槽误杀夹具 --verify true" % cid)
    assert rc == 0, out
    rc, out = card("claim %s --owner %s --pid 999999" % (cid, owner))
    assert rc == 0, out
    return gcard(cid)


PY = sys.executable
STUBS = os.path.join(tempfile.gettempdir(), "cc-slotchurn-stubs")   # 持久桩目录（HOME 每次被夹具清零）


def readf(p):
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def ensure_stubs():
    """三桩全落临时区（fuse-check 同款引号形，cmd shell 兼容）：
    pids.py=固定双 pid 快照源；kill.py=argv pid 落 KILLMARK；wake.py=PM_WAKE_REASON 落 WAKEMARK。
    生产进程不设 RECYCLE_*/FUSE_* 缝 → 本目录只在本锁出现。"""
    os.makedirs(STUBS, exist_ok=True)
    with open(os.path.join(STUBS, "pids.py"), "w", encoding="utf-8") as f:
        f.write("print(424242)\nprint(434343)\n")
    with open(os.path.join(STUBS, "kill.py"), "w", encoding="utf-8") as f:
        f.write("import sys,os\n"
                "open(os.environ['KILLMARK'],'a',encoding='utf-8')"
                ".write('KILLED %s\\n'%sys.argv[1])\n")
    with open(os.path.join(STUBS, "wake.py"), "w", encoding="utf-8") as f:
        f.write("import os\n"
                "open(os.environ['WAKEMARK'],'a',encoding='utf-8')"
                ".write('WAKE|%s\\n'%os.environ.get('PM_WAKE_REASON','').replace('\\n',' '))\n")


def _cmd(stub, tail=""):
    return '"%s" -I "%s"%s' % (PY, os.path.join(STUBS, stub), tail)


def force_release(cid, lane, apply=True, kill_seams=False, extra=None):
    """复刻判死放流：beat 持平恰杀位(下圈 plateau=2) + grace=0 撤豁免 → recycle full 环。
    未 CLAIMED 自动补 claim（二次翻台用）。快照缝默认 `rem noop`（空表，免跑真 CIM——
    本锁绝不碰真实进程）；kill_seams=True 挂 pids/kill 双缝+KILLMARK 观察杀。"""
    if gcard(cid).get("status") != "CLAIMED":
        rc, out = card("claim %s --owner %s --pid 999999" % (cid, lane))
        assert rc == 0, out
    ep = int(gcard(cid).get("claimed_epoch") or 0)
    seed_beat(lane, ep, 1, cid, plateau_prev=1)
    fresh_transcript(lane, age_min=0.0)
    e = {"RECYCLE_TRANSCRIPT_GRACE_MIN": "0", "RECYCLE_SESSION_PID_CMD": "rem noop"}
    if kill_seams:
        e["RECYCLE_SESSION_PID_CMD"] = _cmd("pids.py")
        e["RECYCLE_KILL_CMD"] = _cmd("kill.py", " %P%")
        e["KILLMARK"] = os.path.join(HOME, "kill.txt")
    args = [os.path.join(SRC, "ops", "recycle.py"), "--ring", "full"]
    if apply:
        args.insert(1, "--apply")
    e.update(extra or {})
    rc, out = sh(args, extra_env=e)
    return out


def fuse_extra(wmark):
    """S2 圈环境：心跳常绿(S1静)/日志缺件(S2 死账只可能来自 events)/唤醒落桩/冷却放零；
    他段处置全 NOACT——wake marker 有且只可能来自 S2 本体。"""
    hb = os.path.join(HOME, "hb.txt")
    with open(hb, "w", encoding="utf-8") as f:
        f.write(time.strftime("%F %T") + " hb\n")
    return {"FUSE_MAIN_REPO": os.path.dirname(HOME), "FUSE_HB": hb,
            "FUSE_LOGS": os.path.join(HOME, "logs", "dispatch.log"),
            "FUSE_PM_WAKE_CMD": _cmd("wake.py"), "WAKEMARK": wmark,
            "FUSE_WAKE_MIN_INTERVAL_MIN": "0", "FUSE_RESTART_THROTTLE_MIN": "0",
            "FUSE_NOACT": "s1,s3,s4,s5"}


def run_fuse(extra):
    rc, out = sh([os.path.join(SRC, "ops", "fuse.py"), "--apply"], extra_env=extra, timeout=240)
    return out


def _exec_ns(rel_path, tag, mutate=None):
    """进程内加载 ops 模块源码（可内存变异=撤修复探针，零写盘零落变异文件）。
    mutate 基线针若不在=源码形变了 → 返回 None，由调用侧 bad 报出（锁对形状敏感须如实说）。"""
    src = open(os.path.join(SRC, rel_path), encoding="utf-8").read()
    if mutate:
        old, new = mutate
        if old not in src:
            return None
        src = src.replace(old, new, 1)
    ns = {"__file__": os.path.join(SRC, rel_path), "__name__": tag}
    exec(compile(src, "%s.py" % tag, "exec"), ns)
    return ns


def main():
    # ================= G 款：绿锁 —— 首拍后无续拍(plateau 恰杀位) 但转录在写 → 保活 =================
    c = mk_claim("T1", "slot-99")
    ep = int(c.get("claimed_epoch") or 0)
    if not (c.get("status") == "CLAIMED" and ep >= 1):
        bad("夹具前提(claim 落名牌)", json.dumps(c, ensure_ascii=False)[:200])
        return finish()
    seed_beat("slot-99", ep, 1, "T1", plateau_prev=1)      # 下一拍算得 plateau=2 → lane_alive 判死
    p = fresh_transcript("slot-99", age_min=0.0)           # worker 还在写（slot-25 活体同构）
    rc, out = recycle("--apply --ring full")
    if "CONT-EXEMPT T1" in out and gcard("T1").get("status") == "CLAIMED" \
            and "ZOMBIE T1" not in out:
        ok("G 绿锁: beat在账+圈号持平判死，但转录龄%.1fmin → CONT-EXEMPT 保活不放流"
           % ((time.time() - os.path.getmtime(p)) / 60.0))
    else:
        bad("G 保护形未生效", out[-500:])
    if not os.path.isdir(os.path.join(CODE, "wf-slot-99")):
        ok("G 前提自证: 工作树夹具缺席——豁免只靠转录信号（单源即生效，符合闸语义）")

    # ================= R 款：红检 —— 同场景把闸关掉(grace=0) 必红（空检查比没检查更糟） =================
    c = mk_claim("T1", "slot-99")
    ep = int(c.get("claimed_epoch") or 0)
    seed_beat("slot-99", ep, 1, "T1", plateau_prev=1)
    p = fresh_transcript("slot-99", age_min=0.0)
    rc, out = sh([os.path.join(SRC, "ops", "recycle.py"), "--apply", "--ring", "full"],
                 extra_env={"RECYCLE_TRANSCRIPT_GRACE_MIN": "0"})
    ta = (time.time() - os.path.getmtime(p)) / 60.0
    if "ZOMBIE T1" in out and gcard("T1").get("status") == "OPEN" and ta < 1.0:
        ok("R 红检①(挂点①): 撤闸即杀——同夹具转录龄%.1fmin仍 ZOMBIE release 回 OPEN，"
           "证明救命的是闸不是运气" % ta)
    else:
        bad("R 红检①未红(保护形可能是空检查!)", out[-500:])

    # ================= F 款：双源真冻 → 照杀（宁漏不误保，闸不是大赦） =================
    c = mk_claim("T1", "slot-99")
    ep = int(c.get("claimed_epoch") or 0)
    seed_beat("slot-99", ep, 1, "T1", plateau_prev=1)
    p = fresh_transcript("slot-99", age_min=0.0)
    utime(p, 20.0)                                          # 转录冻结 20min > grace 15min
    rc, out = recycle("--apply --ring full")
    if "ZOMBIE T1" in out and gcard("T1").get("status") == "OPEN" and "CONT-EXEMPT" not in out:
        ok("F 不误保: 转录冻20min+工作树缺源 → 闸不豁免，僵尸照旧回收")
    else:
        bad("F 款失守(误保或路径不通)", out[-500:])

    # ================= L 款：legacy 挂点② —— 存量无名牌 账本静默>阈 但转录在写 → 保活；撤闸必红 =================
    c = mk_claim("T2", "slot-98")
    ep = int(c.get("claimed_epoch") or 0)
    # 名牌三处全拆（卡面 claimed_epoch / claim.json.epoch / lane 锁 meta.json.epoch——
    # recycle 对 ep 是三级兜底，只拆卡面会残牌改走 beat-stale 挂点①，测不到挂点②）
    gp = os.path.join(HOME, "state", "graph.json")
    g = json.load(open(gp, encoding="utf-8"))
    g["cards"]["T2"].pop("claimed_epoch", None)             # 存量无名牌形态（机制落地前旧档）
    json.dump(g, open(gp, "w", encoding="utf-8"), ensure_ascii=False)
    cd = os.path.join(HOME, "claims", "T2")
    for root, _dirs, files in os.walk(cd):
        for fn in files:
            fp = os.path.join(root, fn)
            if fn == "claim.json":
                d = json.load(open(fp, encoding="utf-8"))
                d.pop("epoch", None)
                json.dump(d, open(fp, "w", encoding="utf-8"))
            utime(fp, 20.0)                                 # 账本静默 20min ≥ 阈15
    lk = os.path.join(HOME, "state", "lane-slot-98.lock", "meta.json")
    if os.path.isfile(lk):
        d = json.load(open(lk, encoding="utf-8"))
        d.pop("epoch", None)
        json.dump(d, open(lk, "w", encoding="utf-8"))
        utime(lk, 20.0)
    p = fresh_transcript("slot-98", age_min=0.0)
    rc, out = recycle("--apply --ring full")
    if "CONT-EXEMPT T2" in out and "存量无名牌" in out and gcard("T2").get("status") == "CLAIMED":
        ok("L 绿锁(挂点②): 三处名牌全拆+账本静默20min，转录在写 → legacy 路径同被闸拦下")
    else:
        bad("L 保护形挂点②未生效(或未走 legacy 分支)", out[-400:])
    # 同场景撤闸 → 必红
    rc, out = sh([os.path.join(SRC, "ops", "recycle.py"), "--apply", "--ring", "full"],
                 extra_env={"RECYCLE_TRANSCRIPT_GRACE_MIN": "0"})
    if "ZOMBIE T2" in out and "存量无名牌" in out and gcard("T2").get("status") == "OPEN":
        ok("R 红检②(挂点②): legacy 场景撤闸即杀")
    else:
        bad("R 红检②未红(挂点②可能是空检查!)", out[-400:])

    # ================= W 款：工作树单源续写（零转录道）也救命——两源并联 =================
    c = mk_claim("T3", "slot-97")
    ep = int(c.get("claimed_epoch") or 0)
    seed_beat("slot-97", ep, 1, "T3", plateau_prev=1)
    d = os.path.join(CODE, "wf-slot-97")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "a.sql"), "w", encoding="utf-8") as f:
        f.write("select 1;")                                # 刚写的文件、无转录（合法长判据道）
    rc, out = recycle("--apply --ring full")
    if "CONT-EXEMPT T3" in out and "工作树" in out and gcard("T3").get("status") == "CLAIMED":
        ok("W 双源并联: 无转录但工作树 mtime 在续写 → 豁免走 moving 支")
    else:
        bad("W 款失守(工作树支未接)", out[-400:])

    # ============ ③④ 段（slot-29 续做，卡面验收A）——上方 G/R/F/L/W 为 PM 代落①②的锁 ============
    ensure_stubs()
    sys.path.insert(0, os.path.join(SRC, "ops"))
    import fuse as F                                  # 纯判据单元用（stdlib-only，导入零副作用）

    # ============ K 款：④ S2 补面——同卡 2 次 recycle-release 翻台 → STOPLOSS HOLD+红灯+叫PM；
    #                    FUSE_NOACT=s2 反证处置本体（先摘后放，同沙箱对照） =================
    mk_claim("T4", "slot-95")
    force_release("T4", "slot-95")                    # 翻台①：slot-95 被误杀放流
    force_release("T4", "slot-94")                    # 翻台②：重派 slot-94 又放流——09:30 轮形
    ev4 = readf(os.path.join(HOME, "state", "events.log"))
    nrel = len([l for l in ev4.splitlines() if "|T4|CLAIMED->OPEN|recycle|release" in l])
    if nrel != 2:
        bad("K 前提: events 同卡 recycle-release 应2行（实 %d）——放流事件形与 churn_index 解析脱节" % nrel)
    rc, out = card("create REQ-FLEET-FUSE-1 --title S2段级痕自卡(沙箱) --verify true")
    assert rc == 0, out                               # 段级痕必须挂真实卡号（reconcile 幽灵判据同律）
    wmark = os.path.join(HOME, "wake-K.txt")
    out = run_fuse(dict(fuse_extra(wmark), FUSE_NOACT="s1,s2,s3,s4,s5"))
    if "S2 T4 处置=NOACT" in out and gcard("T4").get("status") == "OPEN" \
            and not os.path.isfile(wmark):
        ok("K 反证(FUSE_NOACT=s2): 摘段圈只报不动——卡仍 OPEN 零 STOPLOSS 零叫PM，处置本体在 s2 闸后")
    else:
        bad("K 反证失守(摘段仍处置或路径不通)", out[-400:])
    out = run_fuse(fuse_extra(wmark))
    hold = str(gcard("T4").get("hold") or "")
    ev4 = readf(os.path.join(HOME, "state", "events.log"))
    if "S2 T4 窗口死次=2" in out and "S2 T4 处置=STOPPED" in out \
            and gcard("T4").get("status") == "HOLD" and "STOPLOSS:fuse" in hold \
            and "FUSE-S2-STOPLOSS" in ev4 and os.path.isfile(wmark):
        ok("K ④补面现网形: 2 次 recycle-release 折进死次账→卡已 OPEN 则不重复放流只挂"
           " HOLD(STOPLOSS:fuse)+叫PM 三面齐落（fuse 自身痕不回数的活证——摘段圈没自触发）")
    else:
        bad("K ④补面未生效(翻台没折进死次账?)", out[-500:])

    # ============ N 款：单翻台不误伤——1 次 recycle-release < need=2 → 只点名的不处置 =================
    mk_claim("T5", "slot-91")
    force_release("T5", "slot-91")
    rc, out = card("create REQ-FLEET-FUSE-1 --title S2段级痕自卡(沙箱) --verify true")
    assert rc == 0, out
    wmarkN = os.path.join(HOME, "wake-N.txt")
    out = run_fuse(fuse_extra(wmarkN))
    if "触发=False" in out and gcard("T5").get("status") == "OPEN" and not os.path.isfile(wmarkN):
        ok("N 单翻台不误伤: 死次=1 未达 need=2 → 不停毒不叫PM（保护形②救回的活道不被 S2 二次放倒）")
    else:
        bad("N 过度处置(单翻台被停毒?)", out[-400:])

    # ============ X 款：③ 放流成功=同轮按快照杀旧会话（双 pid 全落刀） =================
    mk_claim("T6", "slot-90")
    out = force_release("T6", "slot-90", kill_seams=True)
    kills = readf(os.path.join(HOME, "kill.txt"))
    if "GHOST-KILL T6" in out and "杀=2" in out and kills.count("KILLED") == 2 \
            and "KILLED 424242" in kills and "KILLED 434343" in kills \
            and gcard("T6").get("status") == "OPEN":
        ok("X ③杀幽灵现网形: release rc=0 同轮双快照 pid 全落刀（424242/434343），卡已回 OPEN")
    else:
        bad("X ③未杀或杀不对", out[-300:] + "｜kill=" + repr(kills))

    # ============ Y 款：本进程链即在任会话（CCLANE=道名-）→ 自杀必须跳过 =================
    mk_claim("T7", "slot-89")
    out = force_release("T7", "slot-89", kill_seams=True, extra={"CCLANE": "slot-89-自测班"})
    if "GHOST-KILL-SKIP T7" in out and "即在任会话" in out \
            and not os.path.isfile(os.path.join(HOME, "kill.txt")) \
            and gcard("T7").get("status") == "OPEN":
        ok("Y 不自杀: 回收进程 CCLANE 前缀=道名 → 快照再真也不动手（杀自己=断看护轮），放流照做幽灵转 PM")
    else:
        bad("Y 自杀防护失守", out[-300:])

    # ============ Z 款：dry 点名零放刀（回收手滑裸跑不动进程） =================
    mk_claim("T8", "slot-88")
    out = force_release("T8", "slot-88", apply=False, kill_seams=True)
    if "ZOMBIE T8" in out and "release rc=" not in out \
            and not os.path.isfile(os.path.join(HOME, "kill.txt")) \
            and gcard("T8").get("status") == "CLAIMED":
        ok("Z dry 零副作用: 点名不放流不杀会话，卡仍在 CLAIMED（APPLY 才动=与 recycle 主链同纪律）")
    else:
        bad("Z dry 越权(放流或杀了?)", out[-300:])

    # ============ U 款：churn_index 解析单元面（who 只认 recycle；fuse 自痕不计；lane 取法） ============
    rows = [
        "2026-11-03T09:30:01+0800|CX|CLAIMED->OPEN|recycle|release",
        "2026-11-03T09:30:01+0800|CX|OPEN->OPEN|recycle|HANDOVER-OUT e2 by=slot-99 n=3",
        "2026-11-03T09:31:02+0800|CY|CLAIMED->OPEN|fuse|FUSE-S2-STOPLOSS release(rc=0) 已停毒",
        "2026-11-03T09:32:03+0800|CZ|CLAIMED->OPEN|dispatcher|release",
        "2026-11-03T09:33:04+0800|CW|CLAIMED->OPEN|recycle|release",
        "2026-11-03T09:34:05+0800|CX|OPEN->DONE|PM|done",
    ]
    ci = F.churn_index(rows)
    if list(ci.get("CX", [])) and ci["CX"][0][1] == "slot-99" and "CY" not in ci and "CZ" not in ci \
            and len(ci.get("CX", [])) == 1 and ci.get("CW", [("-",)])[0][1] == "-":
        ok("U churn_index 形: recycle-release 计入(带 HANDOVER by=道名)/fuse 自痕与 dispatcher 行拒收/"
           "无 HANDOVER 行 lane=-（不编）/DONE 行不入账")
    else:
        bad("U 解析回归", json.dumps({k: [(x[1], x[2][:30]) for x in v] for k, v in ci.items()},
                                     ensure_ascii=False))

    # ============ B 款：brief 频度规静态面（首拍后静默 3~4min 必放流的旧坑=两套时钟自相矛盾） ============
    dsp = readf(os.path.join(SRC, "ops", "dispatch.py"))
    if all(x in dsp for x in ("每完成一个工作步骤就续拍一笔", "≤5 分钟是天花板",
                              "领牌即首拍", "开跑前先拍一笔")):
        ok("B brief频度规: 派单简报已教「一步一拍+天花板非保险垫+领牌即首拍+长链拆段拍」——"
           "PM 09:55 裁定的频度规落进 worker 视野（回归即红）")
    else:
        bad("B 频度规文案缺失(简报回归?)")

    # ============ S 款：单锁纪律——①② 只认主干 beatzombie_exempt，本道第二锁形永不合树 ============
    src_rec = readf(os.path.join(SRC, "ops", "recycle.py"))
    if "churn_rescue" not in src_rec and src_rec.count("beatzombie_exempt(") == 3 \
            and "def _plateau_death" in src_rec:
        ok("S 单锁纪律: lane_alive 内部第二锁形(churn_rescue)零在册，保护形唯主干 beatzombie_exempt"
           "（def+双挂点=3 引用）——双锁并存=今晚翻台事故的二次温床，钉死；"
           "注: LANE_PLATEAU_DEATH 串已移出禁用面——10:28 活卡面正当阈旋钮(P 款行为断言管语义)，"
           "旧砍除形改由 churn_rescue 独标")
    else:
        bad("S 双锁复活/阈旋钮缺位/主干闸引用数变了",
            "count=%d plateau针=%s" % (src_rec.count("beatzombie_exempt("),
                                       "def _plateau_death" in src_rec))

    # ============ P 款：plateau 可配（活卡面 10:28 根治项「硬编仍在」）——env 升阈救命，撤 env 必杀 ============
    mk_claim("TD", "slot-86")
    epd = int(gcard("TD").get("claimed_epoch") or 0)
    seed_beat("slot-86", epd, 1, "TD", plateau_prev=1)      # 下一圈 plateau=2 < 升后阈3
    fresh_transcript("slot-86", age_min=30.0)               # 转录冻30min+grace=0：豁免面救不了，只有阈能救
    rc, out = sh([os.path.join(SRC, "ops", "recycle.py"), "--apply", "--ring", "full"],
                 extra_env={"RECYCLE_TRANSCRIPT_GRACE_MIN": "0", "LANE_PLATEAU_DEATH": "3",
                            "RECYCLE_SESSION_PID_CMD": "rem noop"})
    if "ZOMBIE TD" not in out and gcard("TD").get("status") == "CLAIMED":
        ok("P 绿: LANE_PLATEAU_DEATH=3 下 plateau=2 → 第三问不杀（心跳龄闸照走），卡仍在制")
    else:
        bad("P 升阈未生效", out[-400:])
    seed_beat("slot-86", epd, 1, "TD", plateau_prev=1)      # 同卡同持平，env 摘回缺省阈2
    rc, out = sh([os.path.join(SRC, "ops", "recycle.py"), "--apply", "--ring", "full"],
                 extra_env={"RECYCLE_TRANSCRIPT_GRACE_MIN": "0", "RECYCLE_SESSION_PID_CMD": "rem noop"})
    if "ZOMBIE TD" in out and gcard("TD").get("status") == "OPEN":
        ok("P 对照: 同 plateau=2 不设 env → 必杀——救命的是阈旋钮不是运气（该死照死=非空检查）")
    else:
        bad("P 对照未杀(阈面可能是空检查!)", out[-400:])
    sys.path.insert(0, SRC)
    try:
        nsp = _exec_ns(os.path.join("ops", "recycle.py"), "recycle_thr")
        thr = {}
        for val, want in ((None, 2), ("3", 3), ("1", 2), ("xx", 2)):
            if val is None:
                os.environ.pop("LANE_PLATEAU_DEATH", None)
            else:
                os.environ["LANE_PLATEAU_DEATH"] = val
            thr[str(val)] = (nsp["_plateau_death"](), want)
        if all(g == w for g, w in thr.values()):
            ok("P 下限纪律: env 只升不降+畸形回退(None→2 '3'→3 '1'→2 'xx'→2)——#91「戳文件装活」持平即死语义不可击穿")
        else:
            bad("P 下限纪律被击穿", json.dumps(thr, ensure_ascii=False))
    finally:
        os.environ.pop("LANE_PLATEAU_DEATH", None)
        sys.path[:] = [p for p in sys.path if p != SRC]

    # ============ E/R③ 款（进程内）：rc=0 双杀→撤调用行必零杀→rc≠0 零杀（变异零写盘） ============
    mk_claim("T9", "slot-87")
    os.environ["CC_TEAM_HOME"] = HOME
    kmark = os.path.join(HOME, "kill_unit.txt")
    for k, v in (("RECYCLE_SESSION_PID_CMD", _cmd("pids.py")),
                 ("RECYCLE_KILL_CMD", _cmd("kill.py", " %P%")), ("KILLMARK", kmark)):
        os.environ[k] = v
    sys.path.insert(0, SRC)
    # 只换 ccards.cli 库闸桩（release 不落真账），ccards 真包必须保留——
    # card_of 走 `from ccards import graph`，整包打桩会掐死它（首跑 ImportError 实锤后改正）
    import ccards as pkg
    cl = types.ModuleType("ccards.cli")
    saved = {"mod": sys.modules.get("ccards.cli"), "attr": getattr(pkg, "cli", None)}
    sys.modules["ccards.cli"] = cl
    pkg.cli = cl
    try:
        ns_ok = _exec_ns(os.path.join("ops", "recycle.py"), "recycle_chk")
        ns_mut = _exec_ns(os.path.join("ops", "recycle.py"), "recycle_mut",
                          mutate=("    if rc == 0:\n        _ghost_kill(cid, owner, ghost_pids)\n",
                                  "    if rc == 0:\n        pass  # MUT 撤修复\n"))
        cl.run = lambda argv: None
        got_ok = ns_ok and ns_ok["do_release"]("T9", "unit-ok")
        n0 = readf(kmark).count("KILLED")
        got_mut = ns_mut and ns_mut["do_release"]("T9", "unit-mut")
        n1 = readf(kmark).count("KILLED")
        cl.run = lambda argv: (_ for _ in ()).throw(SystemExit(3))
        got_fail = ns_ok and ns_ok["do_release"]("T9", "unit-fail")
        n2 = readf(kmark).count("KILLED")
        if got_ok and n0 == 2:
            ok("E 单元绿: 库闸 rc=0 → 快照双 pid 全落刀（单元面独立于 E2E X 款，双保险）")
        else:
            bad("E 单元放杀未发生", "rc0形 got=%s KILLED=%d" % (got_ok, n0))
        if got_mut and n1 == n0:
            ok("R③ 撤修复必红: 变异源(去 _ghost_kill 行)放流照成功却零杀——本锁抓得住")
        else:
            bad("R③ 红检未红或基线针漂移", "got=%s n0=%d n1=%d" % (got_mut, n0, n1))
        if got_fail is False and n2 == n0:
            ok("E 失败不动手: rc=3 → 返回 False 且零杀（宁漏不误杀，杀只在放流成功同轮）")
        else:
            bad("E 失败面失守(rc≠0 仍杀或返回真)", "got=%s n=%d" % (got_fail, n2))
    finally:
        for k in ("RECYCLE_SESSION_PID_CMD", "RECYCLE_KILL_CMD", "KILLMARK"):
            os.environ.pop(k, None)
        if saved["mod"] is None:
            sys.modules.pop("ccards.cli", None)
        else:
            sys.modules["ccards.cli"] = saved["mod"]
        if saved["attr"] is None:
            try:
                del pkg.cli
            except AttributeError:
                pass
        else:
            pkg.cli = saved["attr"]
        sys.path[:] = [p for p in sys.path if p != SRC]

    # ============ R④ 款（进程内双跑）：撤 churn 合并行→双翻台全瞎零处置；复原即 HOLD ============
    mk_claim("TB", "slot-81")
    force_release("TB", "slot-81")
    force_release("TB", "slot-80")                    # TB 同卡两翻台（翻台后重派形）
    rc, out = card("create TC --title churn单锁夹具 --verify true")
    assert rc == 0, out
    force_release("TC", "slot-79")
    force_release("TC", "slot-78")
    card("create REQ-FLEET-FUSE-1 --title S2段级痕自卡(沙箱) --verify true")
    wmarkR = os.path.join(HOME, "wake-R4.txt")
    saved_env = {}
    for k, v in fuse_extra(wmarkR).items():
        saved_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        MUT = ("    for cid, rows in churn_index(events).items():\n"
               "        idx.setdefault(cid, []).extend(rows)\n")
        ns_m = _exec_ns(os.path.join("ops", "fuse.py"), "fuse_mut", mutate=(MUT, ""))
        ns_r = _exec_ns(os.path.join("ops", "fuse.py"), "fuse_chk")
        if ns_m and ns_r:
            ns_m["seg_s2"](ns_m["build_cfg"](True))
            b_m, c_m = gcard("TB").get("status"), gcard("TC").get("status")
            ns_r["seg_s2"](ns_r["build_cfg"](True))
            b_r, c_r = gcard("TB").get("status"), gcard("TC").get("status")
            if b_m == "OPEN" and c_m == "OPEN":
                ok("R④ 撤合并必红: 变异源(去 churn 合并)对双翻台台账全程瞎——零停毒零叫PM（在册=0 老盲区形）")
            else:
                bad("R④ 红检未红(撤了合并仍处置?)", "TB=%s TC=%s" % (b_m, c_m))
            if b_r == "HOLD" and c_r == "HOLD":
                ok("R④ 复原对照: 同夹具同进程面复原源=两张双翻台卡全 HOLD(STOPLOSS)——红绿差即合并行的功劳")
            else:
                bad("R④ 复原面失守", "TB=%s TC=%s" % (b_r, c_r))
        else:
            bad("R④ 基线针漂移(churn 合并行形状变了)", "mut/r=%s/%s" % (bool(ns_m), bool(ns_r)))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("slot-churn: fails=%d" % len(fails))
    return finish()


def finish():
    if fails:
        print("SLOT_CHURN_LOCK_FAIL: " + "; ".join(fails))
        sys.exit(1)
    print("SLOT_CHURN_LOCK_PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
