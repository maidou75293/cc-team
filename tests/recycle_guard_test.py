#!/usr/bin/env python3
# recycle_guard_test.py — MECH-R3 回归锁：recycle 误回收修复的行为断言（独立沙箱 /tmp/cc-recguard，不碰真实账）。
# 事故背景: v2 recycle 用 claims/<id>/claim.json 的 pid 判活，PM/子会话执行的卡该 pid 是 card.py 命令
# 自身的瞬时进程必死 → 健康卡被误 release（实锤: E2E-T1 分诊会话跑中被 18:00 tick 踢回 OPEN）。
# 定版: 只有「CC 泳道执行」(lane-<owner>.lock 存在且锁内 pid WSL+Windows 双侧确死) 才回收；
#       无 lane 锁的 CLAIMED 卡一律只 WARN 不 release，连续两轮同卡 WARN 升 ALARM。
# 跑法: python3 tests/recycle_guard_test.py   （exit 0 = 全绿）
import json, os, shutil, subprocess, sys, time

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # cc-team 根
# MECH-TRAIN-ADR-1：沙箱根可移（火车逐圈传沙箱下唯一路径，与并跑泳道互踩全断）；缺省逐字不变。
SANDBOX = os.environ.get("CC_RECGUARD_HOME") or "/tmp/cc-recguard"
REPO_OLD = os.environ.get("CC_RECGUARD_REPO") or "/tmp/cc-recguard-old-recycle"
OLD_SCRIPT = os.path.join(REPO_OLD, "recycle.py")
fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, detail)


def sh(cmd, env=None, timeout=120):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def card(args, env):
    return sh("python3 %s/cards_dag.py %s" % (SRC, args), env=env)


def recycle(env, extra=""):
    return sh("python3 %s/ops/recycle.py %s" % (SRC, extra), env=env)


def backdate(path, minutes=25):
    t = time.time() - minutes * 60
    os.utime(path, (t, t))


# 修复前 recycle.py（v2）逐字副本（cc-team/ 不入 git，无法从历史取，故固化于此作对照基准）。
# MECH-GBKDECODE-BOMB-1：夹具内 release 文本调用已钉 encoding=utf-8/errors=replace（唯一偏离逐字——
# 不钉则 GBK 宿主跑对照基线时 _readerthread 先炸，判据 A 的母程 locale 解码炸弹会污染基准本身）。
OLD_V2_SRC = '''#!/usr/bin/env python3
import argparse, json, os, shutil, subprocess, sys, time

TEAM = os.environ.get("CC_TEAM_HOME") or os.path.dirname(os.path.abspath(__file__))
CLAIMS = os.path.join(TEAM, "claims")
TASKS = os.path.join(TEAM, "tasks")
CARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card.py")
FINISHED = ("DONE", "VERIFIED")


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def silence_min(d):
    mt = 0.0
    for root, _dirs, files in os.walk(d):
        for fn in files:
            try:
                mt = max(mt, os.path.getmtime(os.path.join(root, fn)))
            except OSError:
                pass
    if mt == 0.0:
        return float("inf")
    return (time.time() - mt) / 60.0


def card_of(cid):
    p = os.path.join(TASKS, cid + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(prog="recycle.py")
    ap.add_argument("--minutes", type=float, default=15.0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.dry:
        a.apply = False

    if not os.path.isdir(CLAIMS):
        print("no claims dir; nothing to do")
        return
    n_stale = n_zombie = n_alive = 0
    verb = "回收" if a.apply else "(dry)会回收"
    for cid in sorted(os.listdir(CLAIMS)):
        d = os.path.join(CLAIMS, cid)
        if not os.path.isdir(d):
            continue
        card = card_of(cid)
        if card is None or card.get("status") != "CLAIMED":
            tag = "无卡" if card is None else card.get("status", "?")
            print("STALE %s: claims 残渣(卡=%s) → %s清锁" % (cid, tag, verb))
            if a.apply:
                shutil.rmtree(d, ignore_errors=True)
            n_stale += 1
            continue
        cj = os.path.join(d, "claim.json")
        pid = None
        try:
            pid = json.load(open(cj, encoding="utf-8")).get("pid")
        except (OSError, ValueError):
            pass
        sil = silence_min(d)
        if pid_alive(pid):
            print("ALIVE %s: pid=%s 活着(静默%.0fmin) → 不动" % (cid, pid, sil))
            n_alive += 1
        elif sil >= a.minutes:
            print("ZOMBIE %s: pid=%s 不在且静默 %.0fmin(阈%.0f) → %s" % (cid, pid, sil, a.minutes, verb))
            if a.apply:
                rc = subprocess.run([sys.executable, CARD, "release", cid, "--who", "recycle"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                print("   release rc=%d %s" % (rc.returncode, (rc.stdout or rc.stderr).strip()))
                if rc.returncode == 0:
                    n_zombie += 1
            else:
                n_zombie += 1
        else:
            print("WAIT %s: pid=%s 不在但静默仅 %.0fmin(<%.0f) → 再观察" % (cid, pid, sil, a.minutes))
            n_alive += 1
    print("recycle: stale=%d zombie=%d alive=%d mode=%s" % (
        n_stale, n_zombie, n_alive, "APPLY" if a.apply else "DRY"))


if __name__ == "__main__":
    main()
'''


def gcard(cid):
    """MECH-PURE1：事实只读 graph.json（旧「tasks/S*.json」读点全部换线）。"""
    g = json.load(open(os.path.join(SANDBOX, "state", "graph.json"), encoding="utf-8"))
    return g["cards"].get(cid, {})


def main():
    env = {"CC_TEAM_HOME": SANDBOX}
    shutil.rmtree(SANDBOX, ignore_errors=True)

    # ---- 0. 建两张卡: S1 会话执行(session型), S2 泳道执行(lane型：claim --pid 自建 v3 道锁+死 pid) ----
    # MECH-PURE1：旧「预置 v1 文件锁」夹具随兼容层退役——v3 原生形态=带 --pid 的 claim 自建道锁
    rc, out = card("create S1 --title 会话卡 --verify true", env)
    assert rc == 0, out
    rc, out = card("create S2 --title 泳道卡 --verify true", env)
    assert rc == 0, out
    rc, out = card("claim S1 --owner sessA", env)
    assert rc == 0, out
    cj1 = os.path.join(SANDBOX, "claims", "S1", "claim.json")
    k = json.load(open(cj1, encoding="utf-8")).get("kind")
    ok("claim 无 lane 锁落 kind=session") if k == "session" else bad("kind=session 未落", repr(k))
    rc, out = card("claim S2 --owner lanA --pid 999999", env)
    assert rc == 0, out
    cj2 = os.path.join(SANDBOX, "claims", "S2", "claim.json")
    k = json.load(open(cj2, encoding="utf-8")).get("kind")
    ok("claim 有 lane 锁落 kind=lane") if k == "lane" else bad("kind=lane 未落", repr(k))

    backdate(cj1)
    backdate(cj2)

    # ---- 1. 修复前行为对照（git HEAD~ 之前的 v2 判据无法再取，用等价手工复算证明场景成立） ----
    # 说明: 旧 v2 = claim.pid 死 + 静默>=15min → release。S1 满足两条件(瞬时 pid+陈旧 mtime)。
    pid1 = json.load(open(cj1, encoding="utf-8")).get("pid")
    try:
        os.kill(int(pid1), 0)
        dead1 = False
    except (ProcessLookupError, ValueError, TypeError):
        dead1 = True
    sil1 = (time.time() - os.path.getmtime(cj1)) / 60.0
    if dead1 and sil1 >= 15:
        ok("误回收场景前提成立: S1 的 claim.pid=%s 已死且静默 %.0fmin —— 旧 v2 判据必回收(事故复现)" % (pid1, sil1))
    else:
        bad("场景前提不成立", "dead=%s sil=%.0f" % (dead1, sil1))

    # ---- 2. 修复后: session 卡 apply 只 WARN 不 release ----
    rc, out = recycle(env, "--apply")
    if "WARN S1" in out and gcard("S1").get("status") == "CLAIMED" \
            and os.path.isdir(os.path.join(SANDBOX, "claims", "S1")):
        ok("session 卡不被回收，只 WARN（E2E-T1 场景被拦）")
    else:
        bad("session 卡回收防护失效", out[-400:])

    # ---- 3. 连续两轮同卡 WARN → ALARM ----
    backdate(cj1)
    rc, out = recycle(env, "--apply")
    if "ALARM S1" in out:
        ok("第二轮同卡 WARN 升 ALARM 供 PM 看")
    else:
        bad("ALARM 升级未生效", out[-400:])

    # ---- 4. lane 僵尸卡（锁内 pid 双侧确死）照旧回收 ----
    rc, out = recycle(env, "--apply")  # S2 本轮前已陈旧; 上轮 apply 已回收则跳过
    s2 = gcard("S2")
    if s2["status"] == "OPEN" and not os.path.isdir(os.path.join(SANDBOX, "claims", "S2")):
        ok("lane 僵尸卡被回收 release 回 OPEN（假锁 pid=999999 WSL+Windows 双侧确死）")
    else:
        bad("lane 卡未按期回收", "status=%s out=%s" % (s2["status"], out[-300:]))

    # ---- 5. 存量兼容: 无 kind 字段 + 无 lane 锁 → 退回旧判据分类 = session → 不回收 ----
    rc, out = card("create S3 --title 存量卡 --verify true", env)
    assert rc == 0, out
    rc, out = card("claim S3 --owner oldA", env)
    assert rc == 0, out
    cj3 = os.path.join(SANDBOX, "claims", "S3", "claim.json")
    d = json.load(open(cj3, encoding="utf-8"))
    d.pop("kind", None)
    json.dump(d, open(cj3, "w", encoding="utf-8"))
    backdate(cj3)
    rc, out = recycle(env, "--apply")
    s3 = gcard("S3")
    if s3["status"] == "CLAIMED" and "S3" in out:
        ok("存量无 kind claim(无锁) 不被回收（旧判据兼容按锁分类=session）")
    else:
        bad("存量兼容断裂", out[-300:])

    # ---- 6. dry 永不 release ----
    rc, out = recycle(env, "")
    s3b = gcard("S3")
    if s3b["status"] == "CLAIMED":
        ok("dry 默认不动手")
    else:
        bad("dry 动了手")

    # ---- 7. 修复前/后对照实锤（嵌入修复前 v2 脚本逐字副本，同沙箱同场景：旧版必 release，新版只 WARN） ----
    # v2 旧判据：claim.json 的 pid 死 + 静默≥阈 → release。对 session 卡该 pid 永远是 card.py 瞬时进程（必死）。
    os.makedirs(REPO_OLD, exist_ok=True)
    with open(OLD_SCRIPT, "w", encoding="utf-8") as f:
        f.write(OLD_V2_SRC)
    link = os.path.join(REPO_OLD, "card.py")
    if os.path.islink(link) or os.path.exists(link):
        os.remove(link)
    # MECH-PURE1：card.py 转发档已清零——历史对照副本的 release 转调改指现役唯一入口 cards_dag.py。
    # BUG-ALARM-LIFECYCLE-1 搭车修（如实报：非本卡主题）：旧档 os.symlink 在 Windows 宿主执行时
    # __file__ 停在链接位、sys.path 不含 SRC → ModuleNotFoundError: ccards，对照轮假红既遂。
    # 改写显式转发 shim（插 SRC 路径再进 cli.main），WSL/Windows 双平台同码可跑。
    with open(link, "w", encoding="utf-8") as f:
        f.write("import os, sys\nsys.dont_write_bytecode = True\nsys.path.insert(0, r'%s')\n"
                "from ccards.cli import main\n"
                "if __name__ == \"__main__\":\n    main()\n" % SRC)
    rc, out = card("create S9 --title 对照卡 --verify true", env)
    assert rc == 0, out
    rc, out = card("claim S9 --owner cmpA", env)
    assert rc == 0, out
    cj9 = os.path.join(SANDBOX, "claims", "S9", "claim.json")
    d9 = json.load(open(cj9, encoding="utf-8"))
    d9.pop("kind", None)  # 模拟存量（v2 时代无 kind）
    json.dump(d9, open(cj9, "w", encoding="utf-8"))
    backdate(cj9)
    # 旧 v2 副本按 tasks 形态读卡=历史世界的数，由夹具从事实图导出（对照专用，用完即弃）
    os.makedirs(os.path.join(SANDBOX, "tasks"), exist_ok=True)
    json.dump(gcard("S9"), open(os.path.join(SANDBOX, "tasks", "S9.json"), "w", encoding="utf-8"))
    rc, out = sh("python3 %s --apply" % OLD_SCRIPT, env=env)
    s9 = gcard("S9")
    if "ZOMBIE S9" in out and s9["status"] == "OPEN":
        shutil.rmtree(os.path.join(SANDBOX, "tasks"), ignore_errors=True)
        ok("修复前对照: 旧版(v2) recycle.py 对同沙箱 session 卡判 ZOMBIE 并 release —— 误回收复现")
    else:
        bad("旧版对照未回收", out[-400:])
    # 重领同场景 → 新版只 WARN 不 release
    rc, out = card("claim S9 --owner cmpA", env)
    if rc != 0:  # 残留锁/一人一在制等边角 → 手工清后重领
        card("release S9", env)
        rc, out = card("claim S9 --owner cmpA", env)
    assert rc == 0, out
    backdate(cj9)
    rc, out = recycle(env, "--apply")
    s9b = gcard("S9")
    if "S9" in out and s9b["status"] == "CLAIMED" and "ZOMBIE S9" not in out:
        ok("修复后对照: 同场景同条件，新版只 WARN 不 release —— E2E-T1 类事故被拦")
    else:
        bad("新版对照失效", out[-400:])

    # ---- 8. recycle 不越权动"未到静默阈"的活锁（WAIT 档：卡与道锁都不碰） ----
    rc, out = card("claim S2 --owner lanA --pid 999999", env)
    if rc != 0:
        card("release S2", env)
        rc, out = card("claim S2 --owner lanA --pid 999999", env)
    assert rc == 0, out
    rc, out = recycle(env, "--apply")
    lk = os.path.join(SANDBOX, "state", "lane-lanA.lock")
    if os.path.isdir(lk) and gcard("S2").get("status") == "CLAIMED":
        ok("未到静默阈：在跑道锁+卡 recycle 一概不动（越权零容忍，v3 原生夹具）")
    else:
        bad("recycle 动了不该动的道锁/卡", out[-300:])

    # ---- 9. 警账生命周期结算（BUG-ALARM-LIFECYCLE-1：死警报不自动销=待裁决面板虚报29件）----
    # 病根实锤（2026-11-02 主仓账）：alerts.json 36 键里 25 条 ack=None 全挂在
    # VERIFIED/DONE/已再claim 的卡上——报警出生在本看护轮里，死也得在这轮里结算。
    AP = os.path.join(SANDBOX, "state", "alerts.json")

    def iso_at(offset_min=0.0):
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() + offset_min * 60))

    def craft_alerts(d):
        json.dump({"updated": iso_at(0), "alerts": d}, open(AP, "w", encoding="utf-8"), ensure_ascii=False)

    def read_alerts():
        try:
            return json.load(open(AP, encoding="utf-8")).get("alerts", {})
        except (OSError, ValueError):
            return {}

    K1 = "ALARM S1: 生命周期迁移场景"      # 卡 release→OPEN：迁销（last 新鲜，时间轴不背锅）
    K2 = "ALARM S2: 生命周期再claim场景"    # 卡被再claim：上一轮的铃不该让新属主背
    K3 = "ALARM 旧残渣: 未续报场景"          # 无主可循的腐警：2×tick 未续报=腐灭
    K4 = "ALARM S3: 仍在响的警必须保留"      # 反例：还在 CLAIMED+还在续报，不许乱销
    K5 = "ALARM S9: 人已处置不再复响"        # 反例：ack 后未续报=已闭，不二次盖章
    rc, out = card("release S1 --who test", env)
    assert rc == 0, out
    rc, out = card("release S2 --who test", env)
    assert rc == 0, out
    rc, out = card("claim S2 --owner lanA --pid 999999", env)  # 再claim：claimed_at 晚于 K2.last
    assert rc == 0, out

    def craft5():
        craft_alerts({
            K1: {"msg": K1, "first": iso_at(-30), "last": iso_at(0), "count": 1, "ack": None},
            K2: {"msg": K2, "first": iso_at(-30), "last": iso_at(-0.5), "count": 1, "ack": None},
            K3: {"msg": K3, "first": iso_at(-60), "last": iso_at(-25), "count": 2, "ack": None},
            K4: {"msg": K4, "first": iso_at(-30), "last": iso_at(0), "count": 1, "ack": None},
            K5: {"msg": K5, "first": iso_at(-60), "last": iso_at(-25), "count": 3,
                 "ack": iso_at(-10), "acked_by": "业主", "ack_reason": "人已处置"},
        })
    craft5()

    # 9.1 dry 只点名不落笔
    rc, out = recycle(env, "")
    a0 = read_alerts()
    if ("AUTOACK" in out and "(dry)会销" in out and "会销" in out
            and not a0[K1].get("ack") and not a0[K2].get("ack") and not a0[K3].get("ack")):
        ok("警账结算 dry=只点名不落笔（与回收主体同纪律）")
    else:
        bad("dry 动了警账或没点名", out[-400:] + repr(a0)[:400])

    # 9.2 apply 结算：①迁销 ②再claim销 ②腐灭销；仍在响/已处置的不许动
    rc, out = recycle(env, "--apply")
    a1 = read_alerts()
    if a1[K1].get("ack") and str(a1[K1].get("acked_by", "")).startswith("auto@") \
            and a1[K1].get("ack_reason") == "卡状态迁移 OPEN":
        ok("①卡离开 CLAIMED(done/转正/下线/撤销)→autoack 原因=卡状态迁移")
    else:
        bad("卡状态迁移未自动销警", repr(a1.get(K1)))
    if a1[K2].get("ack") and a1[K2].get("ack_reason") == "卡状态迁移 再claim":
        ok("①再claim：上一轮遗留警自动销，新属主不背旧铃")
    else:
        bad("再claim未销旧警", repr(a1.get(K2)))
    if a1[K3].get("ack") and a1[K3].get("ack_reason") == "超2tick未续报":
        ok("②last 超 2×tick 未续报→自动 close（腐警不入面板）")
    else:
        bad("腐灭未生效", repr(a1.get(K3)))
    if not a1[K4].get("ack"):
        ok("②反例锁：卡仍 CLAIMED 且警仍在续报=必须保留（不滥销，宁留人工 ack 口子）")
    else:
        bad("活警被滥销", repr(a1.get(K4)) + out[-300:])
    if a1[K5].get("acked_by") == "业主" and a1[K5].get("ack_reason") == "人已处置":
        ok("已处置未复响的警不二次盖章（人工处置痕不被 auto 覆写）")
    else:
        bad("autoack 覆写了人工痕", repr(a1.get(K5)))
    if "autoack=2/1" in out:
        ok("结算计数入汇总行 autoack=迁销2/腐灭1（看板/日志可对账）")
    else:
        bad("汇总行缺 autoack 计数", out[-300:])

    # 9.3 红自证：摘掉**全部**自动销警挂点（结算迁销/腐灭 + 范围清账）跑同场景 → K1 铃必须还响。
    # 双挂点都会销同一批看护警（BUG-REAPER-SCOPE-1 兜底路），只摘一条另一条照样销=假绿；
    # 所以变异体必须同时摘两条，且两条替换各自过标记检查（漂移即红，不做半截变异）。
    mut_src = open(os.path.join(SRC, "ops", "recycle.py"), encoding="utf-8").read()
    mut = mut_src.replace("n_ack_state, n_ack_stale = autoack_sweep(a.tick_minutes, a.apply)",
                          "n_ack_state, n_ack_stale = (0, 0)  # redprobe-LIFECYCLE")
    mut = mut.replace('hit = apply_mode and _AL.autoack(k, "auto@recycle-scope")',
                      "hit = False  # redprobe-SCOPE")
    if "redprobe-LIFECYCLE" not in mut or "redprobe-SCOPE" not in mut:
        bad("红自证替换未命中（recycle.py 销警挂点漂移，锁需同步）")
    else:
        mp = os.path.join(SRC, "ops", "recycle_redprobe_%d.py" % os.getpid())
        try:
            with open(mp, "w", encoding="utf-8") as f:
                f.write(mut)
            craft5()
            rc, out = sh("python3 %s --apply" % mp, env=env)
            a2 = read_alerts()
            if not a2[K1].get("ack") and not a2[K3].get("ack") and "autoack=0/0" in out:
                ok("红自证：摘掉 sweep → 死警原样挂着（撤修复必红）")
            else:
                bad("红自证未红——sweep 摘了警还被销？", repr(a2.get(K1)) + out[-300:])
        finally:
            try:
                os.remove(mp)
            except OSError:
                pass
        craft5()
        rc, out = recycle(env, "--apply")
        a3 = read_alerts()
        if a3[K1].get("ack") and str(a3[K1].get("acked_by", "")).startswith("auto@"):
            ok("同场景换回真版即红转绿（红绿同源，非两套判据）")
        else:
            bad("真版未销", repr(a3.get(K1)))

    # 9.4 复响回开放：autoack 之后问题再现（record 续报）→ 仍算开放，不被一次性销死
    # 键主卡选无卡可寻的（UX-ZRB-1 不在沙箱账）：迁销路径不适用，专打「时间轴腐灭」闭环
    ZK = "ALARM UX-ZRB-1: 复响验证"
    probe = os.path.join(SANDBOX, "reprobe.py")
    with open(probe, "w", encoding="utf-8") as f:
        # 三段链确定性写法：record→autoack→模拟续报(last 推到 ack 之后)。
        # 不用「连调两次 record」测复响：now() 秒级颗粒度下 ack==last 会假阴性（测到flake不是测到语义）。
        f.write("import sys, json\n"
                "sys.dont_write_bytecode = True\n"
                "sys.path.insert(0, r'%s')\n"
                "from ccards import alerts\n"
                "ZK = '''%s'''\n"
                "alerts.record(ZK)\n"
                "n0 = sum(1 for e in alerts.open_entries() if '复响验证' in e.get('msg', ''))\n"
                "hit = alerts.autoack(ZK, '复响即销演练')\n"
                "n1 = sum(1 for e in alerts.open_entries() if '复响验证' in e.get('msg', ''))\n"
                "p = alerts._path()\n"
                "d = json.load(open(p, encoding='utf-8'))\n"
                "d['alerts'][ZK]['last'] = '2999-01-01T00:00:00+0800'\n"
                "alerts.atomic_write_json(p, d)\n"
                "n2 = sum(1 for e in alerts.open_entries() if '复响验证' in e.get('msg', ''))\n"
                "print('CHAIN=%%d/%%s/%%d' %% (n0, hit, n2))\n" % (SRC, ZK))
    rc, out = sh("python3 %s" % probe, env=env)
    if "CHAIN=1/True/1" in out:
        ok("警账三段链：record 即开放→autoack 即停铃→同题再现(last>ack)自动回开放（停铃≠灭失）")
    else:
        bad("复响闭环断链", out[-400:])
    # 复响后不再续报=第二代僵尸，仍归腐灭闸收口（生命周期闭环）
    a5 = read_alerts()
    a5[ZK] = {"msg": ZK, "first": iso_at(-60), "last": iso_at(-25), "count": 2, "ack": None}
    craft_alerts(a5)
    rc, out = recycle(env, "--apply")
    a6 = read_alerts()
    if a6.get(ZK, {}).get("ack_reason") == "超2tick未续报":
        ok("复响后的警同样受腐灭闸收口（不留第二代僵尸）")
    else:
        bad("复响僵尸未收", repr(a6.get(ZK)) + out[-300:])

    print("=== recycle_guard fails=%d ===" % len(fails))
    if fails:
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("GUARD_ALL_PASS")


if __name__ == "__main__":
    main()
