#!/usr/bin/env python3
# resident-watch-test.py — MECH-RESIDENT-WATCH-1 红绿锁：常驻岗互盯「拔锚实验」复现。
# 验收口径（业主 seq8 点6 原文）：人为停任一岗心跳（冻结 log touch）→ 两个扫描周期内
# 该岗卡 ON-DUTY→DEGRADED + 告警入账（events 流水 + alerts 聚合账）；且**不自动自愈**——
# 心跳回来岗卡仍停在 DEGRADED，只有人工 --reset 通道复原。
# 本锁独立沙箱（临时 CC_TEAM_HOME，不碰真实账）、无网络无模型调用、可重跑（主套跑两遍）。
# 跑法: python -I tests/resident-watch-test.py     exit 0 = RESIDENT_WATCH_ALL_PASS
# 红检（判据不自证之防）：三组变异体各自「恰好红且只红其位」——
#   M1 摘掉翻牌写入行 → 拔锚不再降岗；M2 摘掉缺判据告警入账 → 看门真空哑巴；
#   M3 OFF 挡失效 → 停岗位被误盯。任何一组不红 = 本锁自抓（本锁是空锁）。
import json, os, shutil, subprocess, sys, tempfile, time

for _s in (sys.stdout, sys.stderr):  # 本锁自身：控制台 GBK 打不出 U+FFFD 会崩(首轮实锤)
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # cc-team 根
PY = sys.executable
fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, "｜", detail)


def expect(cond, name, detail=""):
    if cond:
        ok(name)
    else:
        bad(name, detail)


def run(argv, env=None, timeout=120):
    e = dict(os.environ)
    e.update(env or {})
    e["PYTHONIOENCODING"] = "utf-8"  # 非 -I 场景兜底
    # 宿主编码坑（首轮实锤）：python -I 会忽略 PYTHON* 环境变量（含 PYTHONIOENCODING），
    # 子进程 stdout 落回 GBK，父侧 UTF-8 解码=乱码假红。-X utf8 是 -I 下唯一稳的钉法。
    if argv and argv[0] == PY:
        argv = [PY, "-X", "utf8"] + list(argv[1:])
    r = subprocess.run(argv, capture_output=True, env=e, timeout=timeout)
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, out


SEED_SRC = ("import json,sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from ccards import graph\n"
            "graph.save(json.loads(sys.argv[2]))\n")


def seed(env, cid, root=ROOT, **fields):
    """卡面直落。数据层 MECH-RESIDENT-1(m2) 的写通道落地前，测试件按 m2 卡面口径直写
    resident/duty/心跳判据字段——与 ops/reconcile.py 的宽容识别同一套契约；m2 定名不同
    时改这里与 DUTY_ALIASES/HB_ALIASES 一行，不动判据。"""
    c = dict(id=cid, title=cid, deps=[], status="VERIFIED", owner="pm", files=[],
             lanes=[], verify="bash -c 'true'", ref_impl="", hold="", repo="", parent="",
             commit=None, evidence=None, output={}, verdict=None)
    c.update(fields)
    rc, out = run([PY, "-I", "-c", SEED_SRC, root, json.dumps(c, ensure_ascii=False)], env)
    assert rc == 0, "seed %s 失败: %s" % (cid, out)


def mk_home():
    home = tempfile.mkdtemp(prefix="cc-rwatch-")
    os.makedirs(os.path.join(home, "state"), exist_ok=True)
    os.makedirs(os.path.join(home, "logs"), exist_ok=True)
    return home, {"CC_TEAM_HOME": home}


def hb_log(home, name, age_min=0.0):
    p = os.path.join(home, "logs", name)
    with open(p, "w", encoding="utf-8") as f:
        f.write("run=1\n")
    if age_min:
        t = time.time() - age_min * 60
        os.utime(p, (t, t))
    return p.replace("\\", "/")


def touch(p):
    os.utime(p, None)


def graph_doc(home):
    return json.load(open(os.path.join(home, "state", "graph.json"), encoding="utf-8"))


def duty_of(home, cid, key="duty"):
    return (graph_doc(home)["cards"].get(cid) or {}).get(key)


def card_json(home, cid):
    return graph_doc(home)["cards"].get(cid) or {}


def read_text(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def scan(env, script="ops/reconcile.py", root=ROOT, extra=("--json",)):
    rc, out = run([PY, "-I", os.path.join(root, script)] + list(extra), env=env)
    js = {}
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            js = json.loads(ln)
    return rc, out, js


def alerts_json(home):
    try:
        return json.load(open(os.path.join(home, "state", "alerts.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def alert_items(home, substr):
    return {k: v for k, v in (alerts_json(home).get("alerts") or {}).items() if substr in k}


def build_cards(home):
    """八张常驻岗 + 一张普通卡。B=拔锚对象(log mtime 冻结90min)；E=命令判据 rc≠0；
    F=命令判据超时(卡死也报)；G2=无显式 duty 字段(VERIFIED 派生在岗)+冻结 log；
    D=在岗缺判据(看门真空)；C=OFF 停岗(心跳同样断，不该被盯)；A/G 健康；REG-1 普通卡对照。
    判据命令用 `python -c`——cmd.exe/bash 两侧同形可跑（宿主纪律：判据必须验收侧能跑）。"""
    b = hb_log(home, "b.log", age_min=90)
    hb_log(home, "c.log", age_min=90)
    c2 = hb_log(home, "g2.log", age_min=90)
    a = hb_log(home, "a.log")
    g = hb_log(home, "g.log")
    fail_cmd = 'python -c "import sys; sys.exit(1)"'
    slow_cmd = 'python -c "import time; time.sleep(6)"'
    return {
        "POST-A": dict(resident=True, duty="ON-DUTY", heartbeat={"log": a, "fresh_min": 15}),
        "POST-B": dict(resident=True, duty="ON-DUTY", heartbeat={"log": b, "fresh_min": 15}),
        "POST-C": dict(resident=True, duty="OFF",
                       heartbeat={"log": os.path.join(home, "logs", "c.log").replace("\\", "/"), "fresh_min": 15}),
        "POST-D": dict(resident=True, duty="ON-DUTY"),
        "POST-E": dict(resident=True, duty="ON-DUTY", heartbeat_cmd=fail_cmd),
        "POST-F": dict(resident=True, duty="ON-DUTY", heartbeat_cmd=slow_cmd),
        "POST-G": dict(resident=True, duty="ON-DUTY", heartbeat={"log": g, "fresh_min": 15}),
        "POST-G2": dict(resident=True, heartbeat={"log": c2, "fresh_min": 15}),
        "REG-1": dict(),
    }, dict(b=b, a=a, g=g, c2=c2)


def open_suite(root=ROOT):
    """建账：沙箱 + 全部卡 seed + dag 物化视图（与主仓同一写口）。"""
    home, env = mk_home()
    cards, L = build_cards(home)
    for cid, f in cards.items():
        seed(env, cid, root=root, **f)
    rc, out = run([PY, "-I", os.path.join(root, "cards_dag.py"), "dag"], env)
    assert rc == 0, "dag 物化失败: " + out
    return home, dict(env, RESIDENT_WATCH_CMD_TIMEOUT="3"), L


def main_suite(tag):
    home, env, L = open_suite()

    # ── S1 周期1：拔锚即降岗（两个周期口径——首个周期就抓到） ────────────────
    rc, out, js = scan(env)
    want = dict(resident=8, scanned=6, off=1, skipped=0, degraded_new=4,
                persist=0, recovery=0, missing=1, invalid=0)
    expect(all(js.get(k) == v for k, v in want.items()), tag + "S1 扫描计数逐位对账",
           "want=%s got=%s" % (want, js))
    expect(js.get("alarms") == 5, tag + "S1 alarms=5(4降岗+1缺判据)", str(js.get("alarms")))
    expect(rc == 1, tag + "S1 检出即非零退出(cron 可感)", "rc=%d" % rc)
    expect(duty_of(home, "POST-B") == "DEGRADED", tag + "S1 拔锚岗 B→DEGRADED", str(duty_of(home, "POST-B")))
    expect(duty_of(home, "POST-E") == "DEGRADED", tag + "S1 命令rc≠0 岗 E→DEGRADED")
    expect(duty_of(home, "POST-F") == "DEGRADED", tag + "S1 命令超时岗 F→DEGRADED(卡死也报)")
    expect(duty_of(home, "POST-G2") == "DEGRADED", tag + "S1 VERIFIED派生在岗也能被盯(m2 落地前可用)")
    expect(duty_of(home, "POST-A") == "ON-DUTY", tag + "S1 健康岗 A 不动")
    expect(duty_of(home, "POST-C") == "OFF", tag + "S1 停岗 OFF 不被盯")
    expect(duty_of(home, "POST-D") == "ON-DUTY", tag + "S1 缺判据只报警不降岗(无仪表≠岗死)")
    expect("duty" not in card_json(home, "REG-1") and not card_json(home, "REG-1").get("resident"),
           tag + "S1 普通卡零沾染")

    # ── S2 告警入账：events 流水 + alerts 聚合账，且不破判据一（status 流水同值）──
    ev = read_text(os.path.join(home, "state", "events.log"))
    flip_lines = [ln for ln in ev.splitlines() if "duty:ON-DUTY->DEGRADED" in ln]
    expect(len(flip_lines) == 4, tag + "S2 events 翻牌流水 4 条", str(len(flip_lines)))
    expect(all(ln.split("|")[2] == "VERIFIED->VERIFIED" for ln in flip_lines),
           tag + "S2 duty 流水不破七态状态对账(判据一安全)")
    expect("RESIDENT-WATCH" in ev, tag + "S2 events 带互盯标记")
    items = alert_items(home, "RESIDENT-WATCH")
    expect(len(items) >= 2, tag + "S2 alerts 聚合账有开放项", str(len(items)))
    expect(any("无心跳判据" in k for k in items), tag + "S2 缺判据=看门无人看真空单独入账", str(list(items)))

    # ── S3 周期2：持续可见、不重复翻牌、告警计数累加 ──────────────────────────
    rc, out, js = scan(env)
    expect(js.get("degraded_new") == 0 and js.get("persist") == 4,
           tag + "S3 周期2 不重复翻牌只持续报", json.dumps(js))
    ev2 = read_text(os.path.join(home, "state", "events.log"))
    expect(len([ln for ln in ev2.splitlines() if "duty:ON-DUTY->DEGRADED" in ln]) == 4,
           tag + "S3 周期2 无新翻牌流水(幂等)")
    bitems = alert_items(home, "RESIDENT-WATCH POST-B")
    expect(bitems and any(int(v.get("count") or 0) >= 2 for v in bitems.values()),
           tag + "S3 B 岗告警开放项计数≥2(持续可数)", json.dumps(bitems))

    # ── S4 不自动自愈：B 心跳回来，岗卡仍停在 DEGRADED ─────────────────────────
    touch(L["b"])
    rc, out, js = scan(env)
    expect(js.get("recovery") == 1, tag + "S4 心跳恢复只发通告", json.dumps(js))
    expect(duty_of(home, "POST-B") == "DEGRADED", tag + "S4 不自愈——B 仍 DEGRADED(业主铁律)")
    expect("NO-SELF-HEAL" in out, tag + "S4 恢复通告点明 NO-SELF-HEAL", out[-300:])

    # ── S5 人工通道 --reset：唯一复原路径，必留痕；误用拒 ─────────────────────
    rc, out = run([PY, "-I", os.path.join(ROOT, "ops", "reconcile.py"), "--reset", "POST-B", "--who", "TEST"], env)
    expect(rc == 0, tag + "S5 reset rc=0", out)
    expect(duty_of(home, "POST-B") == "ON-DUTY", tag + "S5 reset 后 B→ON-DUTY")
    ev = read_text(os.path.join(home, "state", "events.log"))
    expect("duty:DEGRADED->ON-DUTY manual-reset by=TEST" in ev, tag + "S5 人工复原留痕")
    rc2, out2 = run([PY, "-I", os.path.join(ROOT, "ops", "reconcile.py"), "--reset", "POST-A"], env)
    expect(rc2 == 2 and "REJECT-RESET-NOT-DEGRADED" in out2, tag + "S5 reset 拒在岗健康卡", out2)
    rc3, out3 = run([PY, "-I", os.path.join(ROOT, "ops", "reconcile.py"), "--reset", "REG-1"], env)
    expect(rc3 == 2 and "REJECT-RESET-NOT-RESIDENT" in out3, tag + "S5 reset 拒普通卡", out3)
    rc, out, js = scan(env)
    expect(js.get("recovery") == 0 and js.get("degraded_new") == 0,
           tag + "S5 reset 后下一轮 B 彻底转绿", json.dumps(js))

    # ── S6 互盯岗自身心跳痕（看门狗也要被看） ────────────────────────────────
    hb = os.path.join(home, "state", "resident-watch.heartbeat")
    expect(os.path.exists(hb), tag + "S6 自身心跳痕落盘(岗卡册可点名判 reconcile 岗)")
    expect(time.time() - os.path.getmtime(hb) < 120, tag + "S6 心跳痕随扫描刷新")
    shutil.rmtree(home, ignore_errors=True)


def dry_suite(tag):
    """--dry 零写账 + 空账静默（m2 数据层未落地日不许炸真实 cron）。"""
    home, env, L = open_suite()
    before = read_text(os.path.join(home, "state", "graph.json"))
    rc, out, js = scan(env, extra=("--json", "--dry"))
    expect(js.get("degraded_new") == 4, tag + "S7 dry 检出照报", json.dumps(js))
    expect(read_text(os.path.join(home, "state", "graph.json")) == before, tag + "S7 dry 不落账")
    expect("duty:ON-DUTY->DEGRADED" not in read_text(os.path.join(home, "state", "events.log")),
           tag + "S7 dry 不写流水")
    expect(not alerts_json(home).get("alerts"), tag + "S7 dry 不写告警账", json.dumps(alerts_json(home)))
    empty_home, empty_env = mk_home()
    rc2, out2 = run([PY, "-I", os.path.join(ROOT, "ops", "reconcile.py")], env=empty_env)
    expect(rc2 == 0 and "resident=0" in out2, tag + "S8 空账静默", out2)
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(empty_home, ignore_errors=True)


def hook_suite(tag):
    """reconcile_events 钩子：*/30 对账岗跑起来即互盯，原三判据行为不破。"""
    home, env, L = open_suite()
    rc, out = run([PY, "-I", os.path.join(ROOT, "ops", "reconcile_events.py")], env)
    expect("RESIDENT-WATCH" in out, tag + "S9 对账输出含互盯行", out[-200:])
    expect("reconcile: cards=" in out and "alarms=" in out, tag + "S9 原汇总行格式未破")
    expect(rc == 1, tag + "S9 互盯告警计入对账 rc(cron 可感)", "rc=%d" % rc)
    expect(duty_of(home, "POST-B") == "DEGRADED", tag + "S9 钩子真翻牌")
    bad_status_lines = [ln for ln in read_text(os.path.join(home, "state", "events.log")).splitlines()
                        if len(ln.split("|")) > 2 and "->DEGRADED" in ln.split("|")[2]]
    expect(not bad_status_lines, tag + "S9 七态状态流水无 DEGRADED 污染", str(bad_status_lines))
    # 判据一整体不红：互盯翻牌后逐卡「流水尾==卡面 status」仍一致（自抓「假绿对账」）
    rc2, out2 = run([PY, "-I", os.path.join(ROOT, "ops", "reconcile_events.py")], env)
    expect(not [ln for ln in out2.splitlines() if ln.startswith("ALARM: POST-")],
           tag + "S9 复跑对账无裸改假红(判据一仍绿)", out2[-300:])
    shutil.rmtree(home, ignore_errors=True)


def patch(path, old, new, must=1):
    s = read_text(path)
    assert s.count(old) == must, "变异锚点失配(%d处): %s" % (s.count(old), old)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s.replace(old, new))


def mutation_suite(tag):
    """红检：变异体必须恰好红且只红其位——证明本锁不是空锁。"""
    tmp = tempfile.mkdtemp(prefix="cc-rwatch-mut-")
    # 每条变异 = 一组 (old,new) 补丁；M3 两刀=OFF 防线有循环层+锁内复核层两道，
    # 只摘一道被另一道拦住「变异不成立」——两道全摘才红，反向证明双保险真实在场。
    muts = [
        ("M1 摘翻牌写入", [("node[dkey] = DEGRADED", "node[dkey] = ON_DUTY  # MUT")],
         lambda home, env: duty_of(home, "POST-B") == "ON-DUTY"),
        ("M2 摘缺判据告警入账", [('alerts.record("ALARM: %s %s 在岗无心跳判据(看门无人看)" % (MARK, cid))',
                                  "pass  # MUT")],
         lambda home, env: not any("无心跳判据" in k for k in alert_items(home, "RESIDENT-WATCH"))),
        ("M3 OFF 双道全摘", [("if dval == OFF:", "if False:"),
                             ('if now_val in (DEGRADED, OFF) or (now_val or "").startswith("INVALID"):',
                              'if now_val == DEGRADED:  # MUT 锁内复核放行 OFF')],
         lambda home, env: duty_of(home, "POST-C") == "DEGRADED"),
    ]
    for i, (name, pats, assert_mut) in enumerate(muts):
        root_m = os.path.join(tmp, "mut%d" % i)
        shutil.copytree(ROOT, root_m, ignore=shutil.ignore_patterns("__pycache__"))
        try:
            for old, new in pats:
                patch(os.path.join(root_m, "ops", "reconcile.py"), old, new)
        except AssertionError as e:
            bad(tag + name + " 锚点", str(e))
            continue
        home, env, L = open_suite(root=root_m)
        rc, out, js = scan(env, root=root_m)
        expect(assert_mut(home, env), tag + name + " 红得起来(行为确实变形)")
        if i == 0:  # 只红其位：缺判据告警仍在
            expect(duty_of(home, "POST-D") == "ON-DUTY"
                   and any("无心跳判据" in k for k in alert_items(home, "RESIDENT-WATCH")),
                   tag + name + " 只红其位(缺判据闸完好)")
        if i == 1:  # 只红其位：翻牌仍在
            expect(duty_of(home, "POST-B") == "DEGRADED", tag + name + " 只红其位(翻牌闸完好)")
        if i == 2:  # 只红其位：B 照常降岗、健康岗 A 不动
            expect(duty_of(home, "POST-B") == "DEGRADED" and duty_of(home, "POST-A") == "ON-DUTY",
                   tag + name + " 只红其位(拔锚主链仍绿)")
        shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for t in ("R1", "R2"):  # 主套跑两遍=「可重跑」口径（每次全新高沙箱，互不残留）
        main_suite(t)
        dry_suite(t)
        hook_suite(t)
    mutation_suite("M")
    print("=" * 60)
    if fails:
        print("FAIL=%d RESIDENT_WATCH_RED" % len(fails))
        sys.exit(1)
    print("RESIDENT_WATCH_ALL_PASS")
