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
# 跑法: python -I tests/bind-miss-streak-test.py  （exit 0 + 尾行 BIND_MISS_LOCK_PASS = 全绿）
# 沙箱独立账家（CC_BINDMISS_HOME 可移，防并跑互踩）：claims/state/proj/code 全落临时区，
#   run_gate/trace/alarm 落笔全部桩化，零真账、零真会话、零 powershell。
import json, os, shutil, sys, tempfile, time, types
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


def put_claim(sandbox, cid, bound=False, lane="st1"):
    d = os.path.join(sandbox, "claims", cid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "claim.json"), "w", encoding="utf-8") as f:
        json.dump({"session_id": "sid-123" if bound else "-", "lane": lane,
                   "ts": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")}, f)


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

    if fails:
        print("BIND_MISS_LOCK_FAIL fails=%d %s" % (len(fails), ";".join(fails)))
        return 1
    shutil.rmtree(HOME, ignore_errors=True)
    print("BIND_MISS_LOCK_PASS cases=8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
