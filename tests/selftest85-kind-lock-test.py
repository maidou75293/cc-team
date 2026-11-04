#!/usr/bin/env python3
# selftest85-kind-lock-test.py — BUG-SELFTEST85-EXEMPT-FIXTURE-1 常驻红绿锁（卡面验收③三案在册，
# 另加 A4 夹具静态钉=病灶主因永在册）。
# 病灶（2026-11-05 01:1x 业主直连会话取证：公仓 run head c5642b8c17 windows+ubuntu 同红
#   FAIL: 85 补绑失灵或幂等破）：BUG-BIND-PMCLAIM-EXEMPT-1（800e052d，77c5135a 并主干）后
#   claim 落盘 kind（ccards/commands.py cmd_claim：has_anchor=--pid 传了真值 或 道锁在场
#   →kind=lane；皆无=session 自执档）。selftest.sh ④ 段夹具用**无锚 claim** 造派发形——该形
#   自动落 kind=session → ops/dispatch.py backfill_binds 顶置 BIND-EXEMPT 闸整卡跳过（引擎
#   定版语义：自执卡结构性无简报双锚转录，永不绑、不进连拍账、不出铃=豁免不得染指）→
#   session_id 恒 "-" → ④必红。
# 判性：测试夹具过期，引擎行为正确。修法=④ claim 补 --pid 真锚（selftest 壳进程 $$ 活到
#   backfill 跑完），闸体零改动——本锁两头钉：既锁「派发型补绑链是活的」（A1），
#   又锁「豁免闸本身没被拆」（A2），并自证锁咬得住（A3 差分对）、夹具不再漂移（A4 静态）。
# 用例：
#   A1 行为正形：真账根 create+claim 带活 pid→kind=lane→合成双锚转录→backfill_binds→
#      claim.json session_id=夹具会话 且道锁 meta 同锚（双锁同写）；再跑幂等锚不漂移。
#   A2 对照形：同形不传 pid→kind=session→backfill 跳过（session_id 恒 "-"、不进连拍账、
#      零 BIND-MISS 铃、bind 闸零触）=豁免在册非回归；「拿拆闸当修法」在此当场现形。
#   A3 差分对：A1 流程摘去真锚重放（无 pid），A1 的核心断言必红（会话不被补回、恒 "-"）
#      ——守卫红得起来；若摘锚还能绑上=A1 是空检查比没有更糟，本案戳穿。
#   A4 夹具静态锁：selftest.sh ④ 段 claim 0853 行唯一且带 --pid——防夹具二次漂移回无锚形。
# 跑法: python -I tests/selftest85-kind-lock-test.py （exit 0 + 尾行 S85KIND_LOCK_PASS = 全绿）
# 沙箱独立账家（CC_S85KIND_HOME 可移，防并跑互踩，同 all.sh 防撞纪律）：真 claim/真 bind 写闸/
#   真 backfill 全链，账根/转录根/码根 import 前全部指临时区——零真账、零真会话、零 powershell。
import json, os, shutil, subprocess, sys, tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # cc-team 根
DAG = os.path.join(SRC, "cards_dag.py")
SELF = os.path.join(SRC, "tests", "selftest.sh")
HOME = os.environ.get("CC_S85KIND_HOME") or os.path.join(tempfile.gettempdir(), "cc-s85kind")
SBX = os.path.join(HOME, "ledger")
PROJ = os.path.join(HOME, "proj")
CODE = os.path.join(HOME, "code")
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


def gate(*args):
    """cards_dag 写闸子进程——env 继承本进程 os.environ（CC_TEAM_HOME=沙箱 import 前已置）。"""
    return subprocess.run([sys.executable, "-I", DAG] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)


def claim_fixture(tag, pid):
    """真账根夹具：create → claim（pid=None 即无锚自执形）→ 合成双锚转录。返回 (cid, lane, sid)。
    与 selftest.sh ④ 段逐字同形（占位锚 "-"、简报双锚开场行、mtime 新于起班），
    差别只在 --pid 有无=本卡病灶的唯一变量（差分对咬的就是这一维）。"""
    cid = "S85" + tag
    lane = "s85k-%s" % tag.lower()
    r1 = gate("create", cid, "--title", "kind锁夹具%s" % tag, "--verify", "true",
              "--lanes", "ANY", "--files", "cc-team/")
    if r1.returncode != 0:
        raise SystemExit("夹具 create 炸（前提不成立，硬败不装绿）: " +
                         ((r1.stdout or "") + (r1.stderr or ""))[:300])
    args = ["claim", cid, "--owner", lane]
    if pid:
        args += ["--pid", str(pid)]
    r2 = gate(*args)
    if r2.returncode != 0:
        raise SystemExit("夹具 claim 炸（前提不成立，硬败不装绿）: " +
                         ((r2.stdout or "") + (r2.stderr or ""))[:300])
    sid = "s85-live-%s" % tag.lower()
    d = os.path.join(dsp.PROJ, dsp._cfg.proj_dir_name(dsp.CODE, lane))
    os.makedirs(d, exist_ok=True)
    line0 = json.dumps({"type": "queue-operation", "operation": "enqueue", "sessionId": sid,
                        "content": "你是泳道 %s 的 worker\n\n# 卡 %s：kind锁班" % (lane, cid)},
                       ensure_ascii=False)
    with open(os.path.join(d, sid + ".jsonl"), "w", encoding="utf-8") as f:
        f.write(line0 + "\n")
    return cid, lane, sid


def claim_json(cid):
    try:
        return json.load(open(os.path.join(SBX, "claims", cid, "claim.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lane_meta(lane):
    try:
        return json.load(open(os.path.join(dsp._locks.lane_lock_dir(lane), "meta.json"),
                              encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def backfill(cid, lane, times=1):
    for _ in range(times):
        dsp.backfill_binds({cid: lane})


def main():
    global dsp
    shutil.rmtree(HOME, ignore_errors=True)
    for d in (SBX, PROJ, CODE):
        os.makedirs(d, exist_ok=True)
    # 账根/转录根/码根在 import 前钉进 os.environ：in-process dispatch 与 gate 子进程
    #（run_gate→cards_dag bind）同源随锚——锚不生效=下面 A0 自检当场硬败，绝不写/读真账。
    os.environ["CC_TEAM_HOME"] = SBX
    os.environ["DISPATCH_PROJ"] = PROJ
    os.environ["DISPATCH_CODE_ROOT"] = CODE
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ.pop("CLAW_SESSION_ID", None)   # 占位锚 "-" 的宿主免疫：会话泄漏进夹具=前提假红
    sys.path.insert(0, os.path.join(SRC, "ops"))
    sys.path.insert(0, SRC)
    import dispatch as dsp_mod
    dsp = dsp_mod
    if not (os.path.abspath(dsp._cfg.TEAM) == os.path.abspath(SBX)
            and os.path.abspath(dsp.PROJ) == os.path.abspath(PROJ)
            and os.path.abspath(dsp.CODE) == os.path.abspath(CODE)):
        bad("A0 沙箱锚定自检", "dispatch 账根/转录根/码根没指沙箱（有污染真账风险，硬败）: %s|%s|%s"
            % (dsp._cfg.TEAM, dsp.PROJ, dsp.CODE))
        print("S85KIND_LOCK_FAIL fails=%d" % len(fails))
        return 1
    print("PASS: A0 沙箱锚定自检（账根/转录根/码根全指临时区）")

    # ---------------- A1 行为正形：带活 pid 认领=派发型，补绑链必须真通 ----------------
    cidA, laneA, sidA = claim_fixture("a", os.getpid())   # 活锚=本锁测试进程，活到全链跑完
    kA = claim_json(cidA)
    ok("A1a 带真锚认领 kind=lane（④夹具修复后形状；has_anchor=--pid 真值）",
       kA.get("kind") == "lane", "kind=%r" % (kA.get("kind"),))
    ok("A1b 补绑前占位锚在场（在制无锚=与 selftest ④ 前提同形）", kA.get("session_id") == "-",
       "session_id=%r" % (kA.get("session_id"),))
    backfill(cidA, laneA)
    kA2 = claim_json(cidA)
    mA2 = lane_meta(laneA)
    ok("A1c backfill 后 claim.json 会话锚=夹具会话（④段转绿的根：派发形进得了补绑链）",
       kA2.get("session_id") == sidA, "session_id=%r 期望=%r" % (kA2.get("session_id"), sidA))
    ok("A1d 道锁 meta 同锚（bind 写闸双锁同写真通道）", mA2.get("session_id") == sidA,
       "meta.session_id=%r" % (mA2.get("session_id"),))
    backfill(cidA, laneA)
    ok("A1e 幂等再跑锚不漂移（已绑卡再跑原样不动=④段幂等断言同判）",
       claim_json(cidA).get("session_id") == sidA and lane_meta(laneA).get("session_id") == sidA)

    # ---------------- A2 对照形：无锚=session 自执档，豁免在册（非回归） ----------------
    cidB, laneB, sidB = claim_fixture("b", None)          # 复现 800e052d 后 ④旧夹具形
    kB = claim_json(cidB)
    ok("A2a 无锚认领 kind=session（自执档落盘=引擎定版语义，病灶复现钉）",
       kB.get("kind") == "session", "kind=%r" % (kB.get("kind"),))
    backfill(cidB, laneB, 3)                               # 转录与 A1 同样新鲜——豁免闸必须无视它
    ok("A2b session 卡 backfill 整卡跳过：session_id 恒 '-'（BIND-EXEMPT 在册）",
       claim_json(cidB).get("session_id") == "-",
       "session_id=%r" % (claim_json(cidB).get("session_id"),))
    try:
        streak = json.load(open(dsp.BIND_STREAK, encoding="utf-8"))
    except (OSError, ValueError):
        streak = {}
    ok("A2c 自执卡不进连拍账（豁免入账面：pm-mailbox 连拍虚警的病灶不回流）",
       cidB not in streak, str(streak))
    alerts_p = os.path.join(SBX, "state", "alerts.json")
    try:
        alerts = json.load(open(alerts_p, encoding="utf-8")).get("alerts", {})
    except (OSError, ValueError):
        alerts = {}
    ok("A2d 零 BIND-MISS 铃（豁免出警面：连拍三拍到阈也不许响自执卡）",
       not [k for k in alerts if "BIND-MISS" in k], str(list(alerts)[:2]))
    try:
        ev = open(os.path.join(SBX, "state", "events.log"), encoding="utf-8").read()
    except OSError:
        ev = ""
    ok("A2e bind 闸零触（自执卡连探测 bind 都不发=结构性省 IO，事件面独立佐证）",
       not any(("|%s|" % cidB) in ln and "bind" in ln for ln in ev.splitlines()))

    # ---------------- A3 差分对：A1 流程摘去真锚重放，A1 断言必红（守卫红得起来） ----------------
    # 唯一变量=--pid 有无（本卡病灶本体）；其余夹具逐字同 A1。若摘锚仍绑上=A1 是空检查
    # 比没有检查更糟，本案当场戳穿（不放水：正形绿必须建立在反形红之上）。
    cidR, laneR, sidR = claim_fixture("r", None)
    backfill(cidR, laneR)
    kR = claim_json(cidR)
    ok("A3 摘锚重放：kind 落 session 且 A1c 断言必红（会话不被补回=本锁咬得住，非空检查）",
       kR.get("kind") == "session" and kR.get("session_id") != sidR
       and kR.get("session_id") == "-",
       "kind=%r session_id=%r（若摘锚仍绑上=锁是假的）" % (kR.get("kind"), kR.get("session_id")))

    # ---------------- A4 夹具静态锁：selftest.sh ④ claim 0853 行必须带 --pid ----------------
    try:
        src = open(SELF, encoding="utf-8").read()
    except OSError as e:
        src = ""
        bad("A4 夹具静态锁", "selftest.sh 读炸: %r" % (e,))
    hits = [l for l in src.splitlines() if "$CARD claim 0853" in l]
    ok("A4 selftest ④ 段 claim 0853 行唯一且带 --pid 真锚（夹具二次漂移回无锚形=当场红）",
       len(hits) == 1 and "--pid" in hits[0] and "--owner r74b" in hits[0], str(hits)[:2])

    if fails:
        print("S85KIND_LOCK_FAIL fails=%d %s" % (len(fails), ";".join(fails)))
        return 1
    shutil.rmtree(HOME, ignore_errors=True)
    print("S85KIND_LOCK_PASS cases=4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
