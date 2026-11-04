#!/usr/bin/env python3
# guard-watch-test.py — 系统保障互盯探活器红绿锁（业主 seq=10；对应清单 §三 gate-watch-lock）。
# 手法=拔锚实验沙箱复现（同 resident-watch-test 精神，但账本对象是清单不是卡）：
#   S1 清单解析：正常读全量；缺块/空表=大声 RuntimeError→rc1（永真空闸防住）
#   S2 全活一轮：0 告警、不写 DEGRADED
#   S3 拔锚：log 陈旧/json 龄超/argv 非0/url 不可达 → 各自翻 DEGRADED+告警键入账+状态账落盘
#   S4 持续劣化：第二轮 persist 刷计数、不重复 degraded_new
#   S5 不自愈：心跳回来只 recovery_notice，状态仍 DEGRADED；--reset 是唯一复原通道
#   S6 影子=不写账（无 --apply 时状态文件不新建/不改）
#   S7 红检：撤探针必红由 S3 自身构造保证（假活判据在 S3 必翻劣，永绿假闸=本锁红）
#   S9 BUG-GUARD-RINGWIN-CAL-1 差分对：ring 锚判窗=按 ctrld 注册表 every/timeout 机械派生
#      max(240, every*2+timeout)——合法长轮(>240s 未破预算)必绿、真停摆必红；撤派生必红
#      （红基线在册 docs/evidence/bug-guard-ringwin-cal-1/）
# 跑法: python -I cc-team/tests/guard-watch-test.py     exit 0 = GUARD_WATCH_ALL_PASS
import io, json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OPS = os.path.join(ROOT, "ops", "guard_watch.py")
BEGIN, END = "<!-- GUARD-BLOCK-BEGIN -->", "<!-- GUARD-BLOCK-END -->"
fails = []

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("guard_watch", os.path.join(ROOT, "ops", "guard_watch.py"))
_gw = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_gw)
gw_check = _gw.check_guard


def ok(tag):
    print("PASS:", tag)


def bad(tag, *ctx):
    fails.append(tag)
    print("FAIL:", tag, *[str(c)[:200] for c in ctx])


def run(env, *args):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"   # 子程输出钉 UTF-8（GBK 宿主 locale 解码炸弹防回归）
    e.update(env)
    r = subprocess.run([sys.executable, "-I", OPS] + list(args), capture_output=True,
                       encoding="utf-8", errors="replace", env=e, timeout=120)
    return r.returncode or 0, (r.stdout or "") + (r.stderr or "")


def mk_md(guards):
    return ("%s\n```json\n%s\n```\n%s\n" % (BEGIN, json.dumps({"guards": guards}, ensure_ascii=False), END))


def fresh(path, secs_ago=0):
    io.open(path, "w", encoding="utf-8").write("line\n")
    os.utime(path, (time.time() - secs_ago, time.time() - secs_ago))


def main():
    tmp = tempfile.mkdtemp(prefix="guardwatch_")
    state = os.path.join(tmp, "state")
    os.makedirs(os.path.join(state))
    home = os.path.join(tmp, "cc-team")
    os.makedirs(os.path.join(home, "state"))
    logs = os.path.join(tmp, "logs")
    os.makedirs(logs)
    md = os.path.join(tmp, "清单.md")
    hb = os.path.join(tmp, "hb.heartbeat")
    env = {"CC_TEAM_HOME": home, "GUARD_WATCH_STATE": state,
           "GUARD_WATCH_MD": md, "GUARD_WATCH_HB": hb}
    log_ok = os.path.join(logs, "ok.log")
    log_stale = os.path.join(logs, "stale.log")
    jsonp = os.path.join(tmp, "watch.json")
    fresh(log_ok)
    fresh(log_stale, 99999)
    with io.open(jsonp, "w", encoding="utf-8") as fh:
        json.dump({"last_beat": time.time()}, fh)
    guards = [
        {"id": "G-OK", "name": "活log", "kind": "log", "path": log_ok, "window_s": 60},
        {"id": "G-STALE", "name": "陈旧log", "kind": "log", "path": log_stale, "window_s": 60},
        {"id": "G-MISS", "name": "缺失log", "kind": "log", "path": os.path.join(logs, "none.log"),
         "window_s": 60},
        {"id": "G-JSON", "name": "json龄", "kind": "json-age", "path": jsonp, "field": "last_beat",
         "window_s": 300},
        {"id": "G-ARGV0", "name": "argv 0", "kind": "argv",
         "argv": [sys.executable, "-I", "-c", "pass"]},
        {"id": "G-ARGV3", "name": "argv 非0", "kind": "argv",
         "argv": [sys.executable, "-I", "-c", "raise SystemExit(3)"]},
    ]
    io.open(md, "w", encoding="utf-8").write(mk_md(guards))

    # S1 账本完整性
    io.open(md, "w", encoding="utf-8").write("没有标记段")
    rc, out = run(env, "--apply")
    (bad if rc != 1 or "ALARM" not in out else ok)("S1 缺 GUARD-BLOCK=rc1 大声报（空账假绿防住）")
    io.open(md, "w", encoding="utf-8").write(mk_md([]))
    rc, out = run(env, "--apply")
    (bad if "空表" not in out and "guards" not in out else ok)("S1b guards 空表被拒")
    io.open(md, "w", encoding="utf-8").write(mk_md(guards))

    # S2/S3 第一轮全量探
    rc, out = run(env, "--apply", "--json")
    doc = json.loads(next(l for l in out.splitlines() if l.startswith("{")))
    # 拔锚面=3 红（G-STALE 陈旧/G-MISS 缺失=真空/G-ARGV3 非0）；活面=3 绿（G-OK/G-JSON/G-ARGV0）
    if doc["alarms"] == 3 and doc["degraded_new"] == 3:
        ok("S3 拔锚恰红3条，活项3条不误伤（永绿假闸/错杀双向防住）")
    else:
        bad("S3 首轮计数漂移", doc, out[:200])
    st = json.load(io.open(os.path.join(state, "guard-status.json"), encoding="utf-8"))
    if st["G-STALE"]["state"] == "DEGRADED" and st["G-ARGV3"]["state"] == "DEGRADED" \
            and st["G-MISS"]["state"] == "DEGRADED" and "G-OK" not in st and "G-JSON" not in st:
        ok("S3b 状态账=只落劣化项，DEGRADED 粘性")
    else:
        bad("S3b 状态账漂移", st)
    al = json.load(io.open(os.path.join(home, "state", "alerts.json"), encoding="utf-8"))
    keys = " ".join(al.get("alerts", {}).keys())
    if "G-STALE" in keys and "G-ARGV3" in keys:
        ok("S3c 告警进聚合账（沙箱 CC_TEAM_HOME，不污染真账）")
    else:
        bad("S3c 告警未入账", keys[:120])
    if rc == 1:
        ok("S3d 有告警轮 rc=1（ctrld 环日志可点名）")
    else:
        bad("S3d rc 漂移", rc)

    # S4 持续劣化
    rc, out = run(env, "--apply")
    if "persist=3" in out and "degraded_new=0" in out:
        ok("S4 第二轮=持续刷新不重复翻劣（不刷屏）")
    else:
        bad("S4 persist 口径漂移", out[-160:])

    # S5 不自愈 + reset 唯一通道
    fresh(log_ok)  # 心跳回来了
    os.utime(log_stale, None)
    rc, out = run(env, "--apply")
    st = json.load(io.open(os.path.join(state, "guard-status.json"), encoding="utf-8"))
    if st["G-STALE"]["state"] == "DEGRADED" and "NO-SELF-HEAL" in out:
        ok("S5 心跳回来不自愈（仍 DEGRADED，人 reset 才复原）")
    else:
        bad("S5 自愈违规（最重口径）", out[-160:])
    rc, out = run(env, "--reset", "G-NOTEXIST")
    if rc == 2:
        ok("S5b reset 只认在册 DEGRADED（乱 reset 拒）")
    else:
        bad("S5b reset 闸漂", rc, out[:120])
    rc, out = run(env, "--reset", "G-STALE", "--who", "PM")
    if rc == 0 and "DEGRADED->OK" in out:
        ok("S5c 人工 reset 成功留痕")
    else:
        bad("S5c reset 通道断", out[:160])

    # S6 影子不写账：G-ARGV3 还在劣化历史里，无 --apply 的一轮不得改状态账
    before = io.open(os.path.join(state, "guard-status.json"), encoding="utf-8").read()
    rc, out = run(env)  # 缺省=影子
    if io.open(os.path.join(state, "guard-status.json"), encoding="utf-8").read() == before:
        ok("S6 影子只报不写账（T2 同纪律）")
    else:
        bad("S6 影子写账（live-only 闸失）")

    # S8 kind=ring：共写文件里的死环不被邻行 mtime 刷新蒙蔽（时间戳行龄判据）
    ringlog = os.path.join(tmp, "ring.log")
    nowline = time.strftime("%Y-%m-%d %H:%M:%S")
    oldline = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 4000))
    io.open(ringlog, "w", encoding="utf-8").write(
        "%s RING alive rc=0 tail=[x]\n%s RING dead rc=1 tail=[y]\n" % (nowline, oldline))
    g_ok = {"id": "R-OK", "kind": "ring", "path": ringlog, "grep": "RING alive rc=0", "window_s": 300}
    g_dead = {"id": "R-DEAD", "kind": "ring", "path": ringlog, "grep": "RING dead rc=0", "window_s": 300}
    g_stale = {"id": "R-STALE", "kind": "ring", "path": ringlog, "grep": "RING dead rc=1", "window_s": 300}
    for gg, want in ((g_ok, True), (g_dead, False), (g_stale, False)):
        alive, detail = gw_check(gg)
        if alive == want:
            ok("S8 %s ring 判据正确（%s）" % (gg["id"], detail[:50]))
        else:
            bad("S8 %s ring 判据漂" % gg["id"], detail)

    # S9 BUG-GUARD-RINGWIN-CAL-1：判窗机械派生差分对（合法长轮绿/真停摆红；撤派生必红）
    os.environ["CTRLD_SWEEP_SEC"] = "60"        # 预算锚钉死=现注册表默认，不吃宿主 env/toml
    os.environ["CTRLD_SWEEP_TIMEOUT"] = "1800"
    os.environ["CTRLD_FULL_SEC"] = "60"
    os.environ["CTRLD_FULL_TIMEOUT"] = "300"
    ringcal = os.path.join(tmp, "ringcal.log")

    def _ts(secs_ago):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - secs_ago))
    io.open(ringcal, "w", encoding="utf-8").write(
        "%s RING full rc=0 tail=[x]\n%s RING sweep rc=0 tail=[y]\n" % (_ts(300), _ts(2400)))
    tbl = _gw._ring_budgets()
    drift = [n for n, (e, t) in tbl.items()
             if _gw._ring_window({"kind": "ring", "grep": "RING %s rc=0" % n})[0]
             != max(240.0, e * 2 + t)]
    if not drift and tbl.get("sweep") == (60.0, 1800.0) and tbl.get("full") == (60.0, 300.0):
        ok("S9a 环锚窗逐环机械派生=注册表 max(240, every*2+timeout)（全 %d 环公式核过）" % len(tbl))
    else:
        bad("S9a 派生公式漂", drift, tbl)
    alive, detail = gw_check({"id": "R-LONG", "kind": "ring", "path": ringcal,
                              "grep": "RING full rc=0"})
    if alive:
        ok("S9b full 合法长轮 300s>旧窗240s 不破派生窗420s（旧形必红=撤修复即红）（%s）" % detail[:60])
    else:
        bad("S9b 合法长轮误判 DEGRADED（本卡根病复发）", detail)
    alive, detail = gw_check({"id": "R-GONE", "kind": "ring", "path": ringcal,
                              "grep": "RING dispatch rc=0"})
    if not alive:
        ok("S9c 真停摆（无任何 dispatch rc=0 行）照旧红（%s）" % detail[:40])
    else:
        bad("S9c 无心跳假绿", detail)
    alive, detail = gw_check({"id": "R-OVER", "kind": "ring", "path": ringcal,
                              "grep": "RING sweep rc=0"})
    if not alive:
        ok("S9d 2400s>预算窗1920s=sweep 真哑仍红——派生不是空白支票（%s）" % detail[:60])
    else:
        bad("S9d 派生窗变空白支票", detail)
    alive, detail = gw_check({"id": "R-OVW", "kind": "ring", "path": ringcal,
                              "grep": "RING full rc=0", "window_s": 60})
    if alive and "派生" in detail:
        ok("S9e 清单残留硬编码 window_s=60 被派生窗压制（禁止逐条硬编码）（%s）" % detail[:60])
    else:
        bad("S9e 残留清单窗仍夺权", detail)
    # S9f live 通道端到端：独立账本一轮=长轮绿+缺行红，degraded_new=1 且明细带窗来源
    md2 = os.path.join(tmp, "ringcal-清单.md")
    io.open(md2, "w", encoding="utf-8").write(mk_md([
        {"id": "full", "name": "回收看护环", "kind": "ring", "path": ringcal, "grep": "RING full rc=0"},
        {"id": "dispatch", "name": "发单环", "kind": "ring", "path": ringcal, "grep": "RING dispatch rc=0"},
    ]))
    rc, out = run(dict(env, GUARD_WATCH_MD=md2))
    if (rc == 1 and "degraded_new=1" in out and "scanned=2" in out
            and "GUARD-WATCH dispatch" in out and "GUARD-WATCH full" not in out):
        ok("S9f live 一轮=full 长轮 300s 不误伤/dispatch 缺行红（scanned=2 红1 rc=1）")
    else:
        bad("S9f live 通道口径漂", rc, out[-200:])

    if fails:
        print("=== GUARD_WATCH_FAIL n=%d ===" % len(fails))
        sys.exit(1)
    print("GUARD_WATCH_ALL_PASS")


if __name__ == "__main__":
    main()
