#!/usr/bin/env python3
# bind-miss-streak-test.py — BUG-BIND-DEADPATH-1「BIND-MISS 连拍升级」常驻红绿锁。
# 病灶（2026-11-04 夜实锤）：当日 BIND-WAIT 91 次、零 BIND-OK、全部 CLAIMED 卡 session=None。
#   根因链=起会话核失踪（~/.openclaw/tmp/cc-msg/run-cc.ps1 随原生切流归档，BUG-RUNNER-GONE-1
#   已另行收口）→会话从未起→转录永缺→旧补绑形=每拍静默 miss 永无升级：缺锚卡永远挂黄字，
#   尸检侧按「无新转录」判死=误杀风暴随时复发。交接单疑「转录根 WSL 视角未接入」经夜班复现
#   排除：config.CC_PROJ 已派生 Windows 原生视图（宿主用户主目录下的 .claude\projects，
#   wf-* 转录目录 67 个在盘）；残留散点=idle_pulse 缺省 expanduser("~")（本锁随收：单入口校准）。
# 修复形（ops/dispatch.py backfill_binds）：建连拍账 state/bind-streak.json——连续
#   ≥dispatch.bind_miss_streak（默认 5）拍无转录可绑=ALARM BIND-MISS 三面落笔（键恒定防打地鼠，
#   同键聚合 count++），其后每 IDLE_REPEAT(10) 拍重申；绑上/离账=清账。
# 纪律（守卫要验过它红得起来）：C1~C7 绿=全链判据；C8 红=撤闸差分对——源码串改摘掉 alarm_emit
#   调用行（marker 先验在场）重 exec，同流程必「零报警」，若仍见 BIND-MISS=假锁。
# ── BUG-BIND-PMCLAIM-EXEMPT-1（2026-11-04 夜，本卡随增）──
# 病灶：常驻岗自执卡（pm-mailbox 等，claim kind≠lane）不经 dispatch 起班，简报双锚转录结构性
#   不存在=discover_session 永不绑上——旧形连拍账无 kind 闸，岗持卡连拍到阈必响 ALARM BIND-MISS
#   喂 S6（实锤 REQ-OSS-README-UX-1@pm-mailbox 22:51 起响）；同期 recycle session 铃 x12 同根。
# 修复形：豁免判定单入口=ops/dispatch 顶层 claim_is_selfexec（kind≠lane，禁岗名字面量——
#   pm/pm-mailbox/pmmail 别名六形会漂移；落 dispatch 而非 plan 文案建议的 ccards/config=
#   卡面 files 域只声明 ops/tests 四件、done 越域闸对域外文件机械拒勾，判定单点/零散点/
#   零岗名的规格本体不变——交卷注明）；dispatch 连拍账/半成/签收/尸检/unbind 四处、
#   recycle 回收探针、idle_pulse 停摆归因全部函数内并轨同一入口（recycle 判死面与铃语义零改动，
#   selftest.sh #48/#67 与 lane-silence S11 既有护栏照常绿——本卡收的是「口径散点」非「铃」）。
# 新用例：C9 豁免（岗卡零连拍零报警）；C10 差分对照（派单 lane 卡照连拍到阈，豁免不波及）；
#   C11 撤豁免差分（摘闸重 exec 岗卡必重新进账出警=守卫红得起来）；C12 单入口判定表；
#   C13 recycle 真形不判死（沙箱实认领+三轮 apply：CLAIMED 保住+文案兼容+口径已并轨）；
#   C14 idle_pulse 归因纯函数（在岗全自执=结构性静默，ready 优先与旧形对照不变）。
# 跑法: python -I tests/bind-miss-streak-test.py  （exit 0 + 尾行 BIND_MISS_LOCK_PASS = 全绿）
# 沙箱独立账家（CC_BINDMISS_HOME 可移，防并跑互踩）：claims/state/proj/code 全落临时区，
#   run_gate/trace/alarm 落笔全部桩化，零真账、零真会话、零 powershell。
import json, os, shutil, subprocess, sys, tempfile, time, types
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # cc-team 根
DP = os.path.join(SRC, "ops", "dispatch.py")
HOME = os.environ.get("CC_BINDMISS_HOME") or os.path.join(tempfile.gettempdir(), "cc-bindmiss")
fails = []


def ok(name, cond, extra=""):
    if cond is False:
        return bad(name, "判据为 False")
    if cond:
        print("PASS:", name, extra)
        return True
    print("FAIL:", name, extra)
    fails.append(name)
    return False


def bad(name, extra=""):
    print("FAIL:", name, extra)
    fails.append(name)


def load_dispatch(sandbox, src_text=None):
    """exec dispatch.py 进独立模块并全桩化：trace 收音、run_gate 记账、账/转录根指沙箱。
    返回 (module, log)——log 收 trace 行；sandbox=含 state/claims/proj/code 的临时家。"""
    mod = types.ModuleType("dp_" + os.path.basename(sandbox))
    mod.__file__ = DP
    mod.__dict__["__name__"] = "dp_under_test"
    code = compile(src_text if src_text is not None else open(DP, encoding="utf-8").read(),
                   DP + "#sbx", "exec")
    old_path = list(sys.path)
    sys.path.insert(0, os.path.join(SRC, "ops"))
    sys.path.insert(0, SRC)
    try:
        exec(code, mod.__dict__)
    finally:
        sys.path[:] = old_path
    import ccards.config as _cfg
    cfg = mod._cfg
    cfg.CLAIMS = os.path.join(sandbox, "claims")
    cfg.STATE = os.path.join(sandbox, "state")
    cfg.REJECTS = os.path.join(sandbox, "state", "rejects.log")
    mod.PROJ = os.path.join(sandbox, "proj")
    mod.CODE = os.path.join(sandbox, "code")
    mod.BIND_STREAK = os.path.join(sandbox, "state", "bind-streak.json")
    log = []
    mod.trace = lambda m: log.append(str(m))
    gates = []
    mod.run_gate = lambda *a: (gates.append(a), (0, "STUB"))[1]
    mod._BIND_GATES = gates
    for d in ("state", "claims", "proj", "code"):
        os.makedirs(os.path.join(sandbox, d), exist_ok=True)
    return mod, log


def put_claim(sandbox, cid, bound=False, lane="st1", kind=None):
    d = os.path.join(sandbox, "claims", cid)
    os.makedirs(d, exist_ok=True)
    body = {"session_id": "sid-123" if bound else "-", "lane": lane,
            "ts": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")}
    if kind:
        body["kind"] = kind                       # BUG-BIND-PMCLAIM-EXEMPT-1：岗自执形=kind session
    with open(os.path.join(d, "claim.json"), "w", encoding="utf-8") as f:
        json.dump(body, f)


def bind_miss_lines(log):
    return [l for l in log if "BIND-MISS" in l]


def alerts_state(sandbox):
    p = os.path.join(sandbox, "state", "alerts.json")
    try:
        return json.load(open(p, encoding="utf-8")).get("alerts", {})
    except (OSError, ValueError):
        return {}


def main():
    shutil.rmtree(HOME, ignore_errors=True)
    sbx = os.path.join(HOME, "home")
    m, log = load_dispatch(sbx)
    cid = "CARD-BM1"
    put_claim(sbx, cid)
    claimed = {cid: "st1"}

    # C1 未达阈不提前出警（连拍 1~4 只建账）；C2 第 5 拍=阈到，恰好 1 次 BIND-MISS
    for _ in range(4):
        m.backfill_binds(claimed)
    ok("C1 连拍4<阈5 无 ALARM", not bind_miss_lines(log))
    streak = json.load(open(m.BIND_STREAK, encoding="utf-8"))
    ok("C1b 连拍账=4（state/bind-streak.json）", streak.get(cid) == 4, str(streak))
    m.backfill_binds(claimed)
    ok("C2 第5拍到阈=恰好1次 BIND-MISS 进 trace", len(bind_miss_lines(log)) == 1)

    # C3 重申节奏：阈后 6~14 拍沉默、第 15 拍重申（(15-5)%10==0）
    for _ in range(9):
        m.backfill_binds(claimed)
    ok("C3a 第6~14拍不出警（防每拍刷铃）", len(bind_miss_lines(log)) == 1)
    m.backfill_binds(claimed)
    ok("C3b 第15拍重申=累计2次", len(bind_miss_lines(log)) == 2)

    # C4 聚合账同键 count 累积（键恒定防打地鼠：无「连拍N」时距串进键）
    al = alerts_state(sbx)
    keys = [k for k in al if "BIND-MISS" in k]
    ok("C4 alerts 同键聚合 keys=1 count=2",
       len(keys) == 1 and al[keys[0]]["count"] == 2, str(keys))

    # C5 转录出现=解困：双锚主会话开场行可发现 → bind 落闸 + 连拍清零、不再出警
    pdir = os.path.join(m.PROJ, m._cfg.proj_dir_name(m.CODE, "st1"))
    os.makedirs(pdir, exist_ok=True)
    line0 = json.dumps({"type": "queue-operation", "content": "你是泳道 st1 的 worker…\n\n# 卡 %s：x" % cid,
                        "sessionId": "sess-live-1"}, ensure_ascii=False)
    with open(os.path.join(pdir, "sess-live-1.jsonl"), "w", encoding="utf-8") as f:
        f.write(line0 + "\n")
    m.backfill_binds(claimed)
    ok("C5a 转录在场=bind 落闸一次",
       any(a[0] == "bind" and a[1] == cid for a in m._BIND_GATES), str(m._BIND_GATES[-1:]))
    streak = json.load(open(m.BIND_STREAK, encoding="utf-8"))
    ok("C5b 解困即清账", cid not in streak, str(streak))
    put_claim(sbx, cid, bound=True)
    n_before = len(bind_miss_lines(log))
    for _ in range(8):
        m.backfill_binds(claimed)
    ok("C5c 已绑卡后续拍零升级", len(bind_miss_lines(log)) == n_before)

    # C6 离账清漂移：卡 release（claimed 名单与 claims 目录都没了）→ 连拍文件不留孤儿键
    put_claim(sbx, cid)                       # 回到缺锚，攒两拍
    m.backfill_binds(claimed)
    m.backfill_binds(claimed)
    m.backfill_binds({})
    streak = json.load(open(m.BIND_STREAK, encoding="utf-8"))
    ok("C6 离账卡从连拍表剔除（防漂移）", cid not in streak, str(streak))

    # C7 已有锚卡不建账（防「绑上后又被数进缺锚」）
    shutil.rmtree(sbx, ignore_errors=True)
    sbx2 = os.path.join(HOME, "home2")
    m2, log2 = load_dispatch(sbx2)
    put_claim(sbx2, "CARD-BM2", bound=True)
    for _ in range(7):
        m2.backfill_binds({"CARD-BM2": "st1"})
    ok("C7 有锚卡永不进连拍/永不出警",
       not bind_miss_lines(log2) and not json.load(open(m2.BIND_STREAK, encoding="utf-8")))

    # C8 撤闸差分对（守卫红得起来）：源码摘掉 alarm_emit 调用行（marker 先验在场）重 exec，
    #    同流程必须「零报警」——摘了闸还见 BIND-MISS=锁是假的（空检查比没检查更糟）。
    src = open(DP, encoding="utf-8").read()
    lines = src.splitlines(keepends=True)
    idx = next((i for i, l in enumerate(lines) if 'alarm_emit("ALARM BIND-MISS' in l), None)
    ok("C8a 撤点 marker 在场（锁腐=硬败）", idx is not None)
    if idx is not None:
        j = idx
        while "))" not in lines[j]:            # 多行调用=整段摘除（撤闸=等价删调用）
            j += 1
        indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]
        gutted = "".join(lines[:idx] + [indent + "pass  # 撤闸（差分注入）\n"] + lines[j + 1:])
        sbx3 = os.path.join(HOME, "home3")
        m3, log3 = load_dispatch(sbx3, src_text=gutted)
        put_claim(sbx3, "CARD-BM3")
        for _ in range(8):
            m3.backfill_binds({"CARD-BM3": "st1"})
        ok("C8b 撤闸实跑=零 BIND-MISS 报警（差分对绿）", not bind_miss_lines(log3),
           str(bind_miss_lines(log3)[:2]))
    else:
        bad("C8b 撤闸差分对", "marker 缺席无法 exec")

    # ============ BUG-BIND-PMCLAIM-EXEMPT-1 用例（C9~C15） ============
    # C9 豁免①：常驻岗自执卡（kind=session）连拍 8 拍——零报警、零入账、零探测绑定
    sbx9 = os.path.join(HOME, "home9")
    m9, log9 = load_dispatch(sbx9)
    cid9 = "CARD-EX1"
    put_claim(sbx9, cid9, kind="session", lane="pm-mailbox")
    for _ in range(8):
        m9.backfill_binds({cid9: "pm-mailbox"})
    ok("C9a 岗自执卡连拍8拍零 BIND-MISS（豁免出警面）", not bind_miss_lines(log9),
       str(bind_miss_lines(log9)[:2]))
    streak9 = json.load(open(m9.BIND_STREAK, encoding="utf-8"))
    ok("C9b 岗自执卡不进连拍账（豁免入账面）", cid9 not in streak9 and not streak9, str(streak9))
    ok("C9c 岗自执卡连补绑探测都不做（结构性无转录，省 IO 亦防误绑）", not m9._BIND_GATES,
       str(m9._BIND_GATES[:2]))

    # C10 差分对照②：同拍里派单 lane 卡（kind=lane 显式形）照常连拍到阈出警——豁免不漏派单族
    sbx10 = os.path.join(HOME, "home10")
    m10, log10 = load_dispatch(sbx10)
    put_claim(sbx10, "CARD-EX2A", kind="session", lane="st1")
    put_claim(sbx10, "CARD-EX2B", kind="lane", lane="st1")
    for _ in range(5):
        m10.backfill_binds({"CARD-EX2A": "st1", "CARD-EX2B": "st1"})
    ml = bind_miss_lines(log10)
    ok("C10a 派单 lane 卡到阈恰一次出警（豁免不波及）", len(ml) == 1, str(ml[:2]))
    ok("C10b 报警行只含 lane 卡不含自执卡", bool(ml) and "CARD-EX2B" in ml[0]
       and "CARD-EX2A" not in ml[0], str(ml[:1]))
    streak10 = json.load(open(m10.BIND_STREAK, encoding="utf-8"))
    ok("C10c 连拍账只记 lane 卡（=5），自执卡无键",
       streak10.get("CARD-EX2B") == 5 and "CARD-EX2A" not in streak10, str(streak10))

    # C11 撤豁免差分②（守卫红得起来）：摘 BIND-EXEMPT 闸重 exec——岗卡必重新入账出警
    src11 = open(DP, encoding="utf-8").read()
    lines11 = src11.splitlines(keepends=True)
    gi = [i for i, l in enumerate(lines11)
          if l.strip().startswith("if claim_is_selfexec(d):") and "BIND-EXEMPT" in l]
    ok("C11a 豁免闸 marker 在场且唯一（锁腐=硬败）", len(gi) == 1, "n=%d" % len(gi))
    if len(gi) == 1:
        indent = lines11[gi[0]][:len(lines11[gi[0]]) - len(lines11[gi[0]].lstrip())]
        gutted11 = "".join(lines11[:gi[0]]
                           + [indent + "if False:  # 撤豁免（差分注入）\n"]
                           + lines11[gi[0] + 1:])
        sbx11 = os.path.join(HOME, "home11")
        m11, log11 = load_dispatch(sbx11, src_text=gutted11)
        put_claim(sbx11, "CARD-EX3", kind="session", lane="pm-mailbox")
        for _ in range(6):
            m11.backfill_binds({"CARD-EX3": "pm-mailbox"})
        ok("C11b 撤豁免实跑=岗卡重新入账到阈出警（差分对红得起来）",
           bool(bind_miss_lines(log11))
           and json.load(open(m11.BIND_STREAK, encoding="utf-8")).get("CARD-EX3") == 6,
           str(bind_miss_lines(log11)[:1]))
    else:
        bad("C11b 撤豁免差分", "marker 缺席无法 exec")

    # C12 单入口判定表：三处 ops 并轨此函数，语义必须逐档合卡面口径
    # 判定单入口落 ops/dispatch（卡面 files 域内；非 plan 文案的 ccards/config，交卷注明）
    if os.path.join(SRC, "ops") not in sys.path:
        sys.path.insert(0, os.path.join(SRC, "ops"))
    from dispatch import claim_is_selfexec as _judge
    _cfgs = types.SimpleNamespace(claim_is_selfexec=_judge)
    ok("C12a kind=lane→派单族(不豁免)", _cfgs.claim_is_selfexec({"kind": "lane"}) is False)
    ok("C12b kind=session→自执豁免", _cfgs.claim_is_selfexec({"kind": "session"}) is True)
    ok("C12c 缺 kind 默认 lane=派发侧保守进账", _cfgs.claim_is_selfexec({}, default="lane") is False)
    ok("C12d 缺 kind 默认 session=recycle 无锚侧保守不回收",
       _cfgs.claim_is_selfexec({}, default="session") is True)
    ok("C12e 多源优先（meta 先 claim 后）",
       _cfgs.claim_is_selfexec({"kind": "session"}, {"kind": "lane"}) is True
       and _cfgs.claim_is_selfexec({}, {"kind": "lane"}) is False)
    ok("C12f 未知 kind 归自执（保守不回收向）+ None 安全",
       _cfgs.claim_is_selfexec({"kind": "weird"}) is True
       and _cfgs.claim_is_selfexec(None) is False
       and _cfgs.claim_is_selfexec(None, default="session") is True)

    # C13 recycle③ 真形不判死：沙箱真认领（kind=session+真道锁）+全环 apply 三轮
    rec_home = os.path.join(HOME, "rec")
    shutil.rmtree(rec_home, ignore_errors=True)
    os.makedirs(os.path.join(rec_home, "logs"), exist_ok=True)
    envr = dict(os.environ, CC_TEAM_HOME=rec_home, PYTHONIOENCODING="utf-8")
    dag = os.path.join(SRC, "cards_dag.py")
    r1 = subprocess.run([sys.executable, "-I", dag, "create", "SE-1", "--title", "岗自执卡沙箱",
                         "--verify", "true", "--lanes", "ANY", "--files", "cc-team/"],
                        env=envr, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=120)
    r2 = subprocess.run([sys.executable, "-I", dag, "claim", "SE-1", "--owner", "pm-mailbox"],
                        env=envr, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=120)
    claim_e1 = {}
    try:
        claim_e1 = json.load(open(os.path.join(rec_home, "claims", "SE-1", "claim.json"),
                                  encoding="utf-8"))
    except (OSError, ValueError):
        pass
    ok("C13a 真形认领=kind session（复现今晚 22:46 pm-mailbox 形：无 pid 无预置道锁）",
       r1.returncode == 0 and r2.returncode == 0 and claim_e1.get("kind") == "session",
       ((r1.stdout or "") + (r1.stderr or "") + (r2.stdout or "") + (r2.stderr or ""))[:200])
    rcy = os.path.join(SRC, "ops", "recycle.py")
    outs, rcs = [], []
    for _ in range(3):
        rr = subprocess.run([sys.executable, "-I", rcy, "--apply", "--ring", "full"],
                            env=envr, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=180)
        outs.append((rr.stdout or "") + (rr.stderr or ""))
        rcs.append(rr.returncode)
    try:
        stat = ((json.load(open(os.path.join(rec_home, "state", "graph.json"),
                                encoding="utf-8")).get("cards") or {}).get("SE-1") or {}).get("status")
    except (OSError, ValueError) as e:
        stat = "图读炸:%r" % e
    ok("C13b 全环 apply 三轮 rc=0（岗自执卡不炸尸检主链）", all(rc == 0 for rc in rcs), str(rcs))
    ok("C13c 无锚自执卡不被「无转录」判死：状态恒 CLAIMED、零 SILENCE-DETACH/WORKER-DIED",
       stat == "CLAIMED" and not any("SILENCE-DETACH SE-1" in o or "WORKER-DIED SE-1" in o
                                      for o in outs), "status=%s" % stat)
    ok("C13d session 卡点名行照出（与 selftest.sh#48 / lane-silence S11 既有护栏零相撞）",
       any("session 执行卡" in o for o in outs))
    rsrc = open(rcy, encoding="utf-8").read()
    ok("C13e recycle 判定已并轨单入口（两处调用；内联 kind 判定零残留）",
       rsrc.count("claim_is_selfexec(") == 2 and 'kind == "session"' not in rsrc
       and 'or "lane"' not in rsrc, "n=%d" % rsrc.count("claim_is_selfexec("))

    # C14 idle_pulse③ 归因收口（纯判定函数 + 差分对照）
    ipy = os.path.join(SRC, "ops", "idle_pulse.py")
    sys.path.insert(0, os.path.dirname(ipy))
    import idle_pulse as ip
    sys.path.pop(0)
    w1 = ip.stall_attribution(0, 2, 2)
    ok("C14a 在制全岗自执=结构性静默归因（不错指派发断）",
       "结构性静默" in w1 and "派发/启动断" not in w1, w1)
    ok("C14b 差分：在制全派单族=旧文案逐字不变（豁免不波及）",
       ip.stall_attribution(0, 2, 0) == "在岗断流（有在制无活性）")
    ok("C14c ready 积压优先不变（派发职责不随岗豁免退让）",
       ip.stall_attribution(1, 1, 1) == "派发/启动断（账上有料却无活性）")
    ok("C14d 供料枯竭/三源皆空两支保留（快照炸负值保守向）",
       ip.stall_attribution(0, 0, 0) == "供料枯竭（ready=0 且 claimed=0，该发新卡）"
       and ip.stall_attribution(-1, -1, -1) == "活性三源皆空（账本/转录/心跳都读不到）")
    isrc = open(ipy, encoding="utf-8").read()
    ok("C14e idle_pulse 归因走单入口", isrc.count("claim_is_selfexec(") >= 1)

    # C15 散点普查：dispatch 判定全并轨（≥4 处调用、旧内联口径零残留）
    dsrc15 = open(DP, encoding="utf-8").read()
    ok("C15 dispatch 判定散点全收口（连拍账/半成上游/签收/尸检/unbind 走同入口）",
       dsrc15.count("claim_is_selfexec(") >= 4
       and 'str(d.get("kind") or "lane")' not in dsrc15,
       "n=%d" % dsrc15.count("claim_is_selfexec("))

    if fails:
        print("BIND_MISS_LOCK_FAIL fails=%d %s" % (len(fails), ";".join(fails)))
        return 1
    shutil.rmtree(HOME, ignore_errors=True)
    print("BIND_MISS_LOCK_PASS cases=15")
    return 0


if __name__ == "__main__":
    sys.exit(main())
