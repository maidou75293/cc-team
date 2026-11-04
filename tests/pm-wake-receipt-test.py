#!/usr/bin/env python3
# pm-wake-receipt-test.py — BUG-PM-WAKE-RECEIPT-1「拉成回执」常驻红绿锁（仿 slot-churn-test 沙箱独立账家形态）。
# 病灶（业主 2026-11-04 04:06 班次指令；ctrld.log 04:04~04:09 连拍实证）：旧看门只记「拉过」不核「拉成」——
#   被拉起的 PM 会话死于 openclaw update 重启、信箱没清，看门在 30min 满窗冷却里持续 SKIP-COOLDOWN 空转，
#   近半小时不再重试=叫醒通道名存实亡。
# 修复形（ops/inbox_watchdog.py 回执版）：
#   ① 拉起落锚=(sig,n) 快照；sig 变化或 n 下降=拉成确认（WAKE-RECEIPT）→清锚重开闸；
#   ② 同签名无进展=上一拉作废，短背棍窗（INBOX_WATCH_RETRY_MIN 默认 5min）到点重试，不傻等满窗；
#   ③ 同签名连续 3 拉无回执=WAKE-STALL 进 alerts 聚合账（键=恒定文案防打地鼠）+state 钉住停拉；
#   ④ lane-pmmail.lock 锚死=同族判据 LOCK-REAP 清残放行（无 pid 证据者走 30min 病残窗不误拆活锁）。
# 纪律（守卫要验过它红得起来）：绿=全链判据；红=逐件撤修复必红（R1 撤背棍回满窗/R2 撤收刀/R3 撤停损阈值/
#   R4 撤落账/R5 撤回执清闸），撤点缺失=硬败（防锁腐，撤前查 marker 在场、撤后先跑通基线才许判红）。
# 跑法: python -I tests/pm-wake-receipt-test.py   （exit 0 + 尾行 PM_WAKE_RECEIPT_LOCK_PASS = 全绿）
# 沙箱独立账家（CC_PMWAKE_HOME 可移，防并跑互踩）：inbox/state/lock/alerts 全落临时区，零真账、零真会话
#   （env 缝 INBOX_WATCH_SIM_LAUNCH=1 不真起 powershell；INBOX_WATCH_NOW=epoch 冻结时钟打时点回放）。
import json, os, shutil, subprocess, sys, tempfile, time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # cc-team 根
MOD = os.path.join(SRC, "ops", "inbox_watchdog.py")
HOME = os.environ.get("CC_PMWAKE_HOME") or os.path.join(tempfile.gettempdir(), "cc-pmwake")
PROJ = os.path.join(tempfile.gettempdir(), "cc-pmwake-probe")           # 备用；本器不建工程树
T0 = 1_000_000.0            # 冻结 epoch（远早于墙钟：idle 钳 0、utime 用 T0-年龄 打时差）
DEAD = 2 ** 31 - 2          # 本机不可能驻留的 pid（Win OpenProcess 失败/posix ESRCH 皆判死）
RETRY_S = 5 * 60            # 与生产默认背棍窗一致（env 显式钉 5，坏境不干扰判据）
fails = []


def ok(name, extra=""):
    # 判据=False 必须落账（R1~R5 都按 ok(name, bool) 传判据；只打 False 不落账=锁具形同虚设）
    if extra is False:
        return bad(name, "判据为 False")
    print("PASS:", name, extra)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, detail)


def run_env(home, now_epoch, extra=None):
    e = dict(os.environ)
    e.update({"CC_TEAM_HOME": home,                    # 红灯账/看门旁证账全进沙箱（config.STATE 随 env）
              "INBOX_WATCH_SIM_LAUNCH": "1",          # 测试缝：LAUNCH 落锚不真起 powershell
              "INBOX_WATCH_QUIET_MIN": "0",           # 凑批门放开：本器测的是回执节奏不是凑批（凑批门另有 selftest 矩阵）
              "INBOX_WATCH_RETRY_MIN": "5",
              "INBOX_WATCH_NOW": str(now_epoch),
              "PYTHONIOENCODING": "utf-8"})
    e.update(extra or {})
    return e


class Case:
    """每例一套沙箱账家：inbox/state/lock 独立，撤修复变体互不沾染。"""

    def __init__(self, name, mod=MOD):
        self.home = os.path.join(HOME, "case-" + name)
        self.mod = mod
        shutil.rmtree(self.home, ignore_errors=True)
        os.makedirs(os.path.join(self.home, "state"), exist_ok=True)
        self.box = os.path.join(self.home, "OWNER-INBOX.md")
        self.st = os.path.join(self.home, "inbox-watch.json")
        self.lk = os.path.join(self.home, "state", "lane-pmmail.lock")  # --lock 显式注入，落沙箱不进 .cc 真锁区
        self.inbox(["23", "24", "25"])

    def inbox(self, unacked, acked=()):
        lines = []
        for s in acked:
            lines.append("## seq=%s 2026-11-04T02:05+0800 [acked]\nbody\n" % s)
        for s in unacked:
            lines.append("## seq=%s 2026-11-04T02:05+0800\nbody\n" % s)
        with open(self.box, "w", encoding="utf-8") as f:
            f.write("# box\n" + "".join(lines))

    def tick(self, now_epoch):
        p = subprocess.run([sys.executable, "-I", self.mod, "--apply",
                            "--inbox", self.box, "--state", self.st, "--lock", self.lk],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=run_env(self.home, now_epoch))
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out

    def state(self):
        return json.load(open(self.st, encoding="utf-8"))

    def alerts(self):
        p = os.path.join(self.home, "state", "alerts.json")
        try:
            return json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            return {"alerts": {}}

    def stall_keys(self):
        return [k for k in self.alerts().get("alerts", {}) if "WAKE-STALL" in k]

    def make_dead_lock(self):
        os.makedirs(self.lk, exist_ok=True)
        with open(os.path.join(self.lk, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"pid": DEAD}, f)
        os.utime(os.path.join(self.lk, "meta.json"), (T0 - 30, T0 - 30))


def ladder(c, up_to="retry1"):
    """回放业主指定场景「拉起死掉不清账」：G1 首拉→G2 背窗内不叠拉→（残锁在场）G3 窗满清残重试。
    up_to: 'retry1'=到首次重试；'stall'=打到 WAKE-STALL 钉闸。"""
    rc, out = c.tick(T0)
    assert rc == 0 and " LAUNCH " in out and "回执窗" in out, ("基线首拉断", out[-200:])
    if up_to == "retry1":
        return
    rc, out = c.tick(T0 + 120)
    assert "SKIP-RETRY" in out, ("基线背窗内应不叠拉", out[-200:])
    c.make_dead_lock()
    rc, out = c.tick(T0 + 310)            # 310s>300s 背窗：回执未落=判失败重试，不傻等 30min 满窗
    assert " LAUNCH " in out and "LOCK-REAP" in out, ("基线窗满应重试+清残", out[-200:])
    rc, out = c.tick(T0 + 320)
    assert "SKIP-RETRY" in out, ("基线再拉后回背窗", out[-200:])
    if up_to == "retry2":
        return
    rc, out = c.tick(T0 + 620)
    assert " LAUNCH " in out and "WAKE-STALL" not in out, ("基线第2拉", out[-200:])
    rc, out = c.tick(T0 + 930)
    assert "ALARM" in out and "WAKE-STALL" in out, ("基线第3次无回执应停损", out[-200:])


# ================= 绿段：现形全链判据 =================
c = Case("green")
rc, out = c.tick(T0)
ok("G1 首拉=LAUNCH+回执窗痕", rc == 0 and " LAUNCH " in out and "回执窗5min" in out)
st = c.state()
ok("G1 锚含n快照(拉成回执的对照物)", st["last_n"] == 3 and st["last_sig"] and st["last_launch_ts"] == T0)
rc, out = c.tick(T0 + 120)
ok("G2 背窗内(2min<5min)不叠拉=SKIP-RETRY", "SKIP-RETRY" in out and "brief=" not in out)
c.make_dead_lock()
rc, out = c.tick(T0 + 310)
ok("G3 拉起死掉不清账=5min短背棍重试+残锁清理(业主主案)",
   "LOCK-REAP" in out and " LAUNCH " in out and not os.path.exists(c.lk)
   and c.state()["windows"] == 1)
rc, out = c.tick(T0 + 320)
ok("G4 重试后再进背窗", "SKIP-RETRY" in out)
rc, out = c.tick(T0 + 620)
ok("G5 窗满无进展再拉(w2)", " LAUNCH " in out and c.state()["windows"] == 2)
rc, out = c.tick(T0 + 930)
ok("G6 第3次无回执=ALARM+WAKE-STALL", "ALARM" in out and "WAKE-STALL" in out and c.state()["alarmed"] is True)
sk = c.stall_keys()
ok("G6b WAKE-STALL进alerts聚合账且键稳定单条",
   len(sk) == 1 and c.alerts()["alerts"][sk[0]]["count"] >= 1 and "龄" not in sk[0])
rc, out = c.tick(T0 + 990)
ok("G7 钉住后停拉=ALARMED（风暴止于3拉）", "ALARMED" in out and "brief=" not in out)
c.inbox(["24", "25"], acked=["23"])       # 被叫会话清了一条=拉成确认
rc, out = c.tick(T0 + 1000)
ok("G8 签名变化=WAKE-RECEIPT解除钉闸重开闸",
   "WAKE-RECEIPT" in out and " LAUNCH " in out and c.state()["alarmed"] is False
   and c.state()["windows"] == 0)
rc, out = c.tick(T0 + 1010)
ok("G9 新episode回背窗节奏(10s<5min)", "SKIP-RETRY" in out)
c.inbox([], acked=["23", "24", "25"])
rc, out = c.tick(T0 + 1310)
st = c.state()
ok("G10 全清=OK复位锚(launches保留在岗痕)",
   rc == 0 and st["last_launch_ts"] is None and st["alarmed"] is False and st["launches"] >= 4)
p = subprocess.run([sys.executable, "-I", MOD, "--dry", "--inbox", c.box],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=run_env(c.home, T0 + 1400, {"INBOX_WATCH_NOW": ""}))
ok("G11 --dry判据面含retry读数", p.returncode == 0 and "retry_min" in (p.stdout or ""))

# ================= 红段：逐件撤修复必红 =================


def stripped(name, marker, repl):
    """把 MOD 复制进临时区并做唯一替换；撤点不在场=硬败（锁腐自检）。
    变异体不在 ops/ 目录、且 -I 隔离模式不吃 PYTHONPATH——复制时前置一行 sys.path 锚回
    真实 cc-team（ccards 可 import），账家三件（inbox/state/lock）仍全走显式注入不受影响。"""
    src = open(MOD, encoding="utf-8").read()
    if src.count(marker) < 1:
        bad(name + " 撤点漂移(marker不在源码)", marker)
        return None
    src = src.replace("import argparse,", "import sys as _s; _s.path.insert(0, r'%s')\nimport argparse," % SRC, 1)
    dst = os.path.join(HOME, "mod-%s.py" % name)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(src.replace(marker, repl, 1))
    return dst


# R1 撤背棍=恢复 30min 傻等满窗：拉起的会话死了不清账，+5min 处不许有重试
m = stripped("R1", "retry * 60.0", "30.0 * 60.0")
if m:
    c1 = Case("R1", mod=m)
    rc, out = c1.tick(T0)
    if " LAUNCH " not in out:
        bad("R1 变异体基线未跑通", out[-200:])
    else:
        rc, out = c1.tick(T0 + 310)
        ok("R1 撤修复必红(5min处仍SKIP不重试=旧病复发形)",
           " LAUNCH " not in out and "SKIP-RETRY" in out and c1.state()["windows"] == 0)

# R2 撤残锁收刀：锚死锁永不清=通道被自己留下的残锁堵死
m = stripped("R2", "_pid_alive(pid)", "True")
if m:
    c2 = Case("R2", mod=m)
    rc, out = c2.tick(T0)
    if " LAUNCH " not in out:
        bad("R2 变异体基线未跑通", out[-200:])
    else:
        c2.make_dead_lock()
        rc, out = c2.tick(T0 + 310)
        ok("R2 撤修复必红(锚死锁不拆=SKIP-INFLIGHT堵死)",
           "LOCK-REAP" not in out and " LAUNCH " not in out and os.path.exists(c2.lk))

# R3 撤停损阈值（3→30）：无回执风暴不止。不用 ladder（其 930s 已消费该拍），自走三拉：
m = stripped("R3", "ALARM_WINDOWS = 3", "ALARM_WINDOWS = 30")
if m:
    c3 = Case("R3", mod=m)
    seq = [c3.tick(T0), c3.tick(T0 + 310), c3.tick(T0 + 620), c3.tick(T0 + 930)]
    if not all(" LAUNCH " in o for _, o in seq):
        bad("R3 变异体基线未跑通(先于红断言)", " | ".join(o[-120:] for _, o in seq))
    else:
        rc, out = seq[-1]
        ok("R3 撤修复必红(第3拉无停损=风暴续拉)",
           " LAUNCH " in out and "WAKE-STALL" not in out and c3.state()["alarmed"] is False
           and c3.state()["windows"] == 3)

# R4 撤红灯落账：WAKE-STALL 只打日志不进聚合账（打地鼠病灶回归）
m = stripped("R4", "_AL.record(stall)", "pass")
if m:
    c4 = Case("R4", mod=m)
    try:
        ladder(c4, up_to="stall")
    except AssertionError as e:
        bad("R4 变异体基线未跑通(先于红断言)", str(e))
    else:
        ok("R4 撤修复必红(钉闸在但alerts账无STALL键)",
           c4.state()["alarmed"] is True and len(c4.stall_keys()) == 0)

# R5 撤回执清闸：信箱已进展仍被旧钉闸压死（拉成不认账=旧「只记拉过」的另一种死法）
# 撤点用上下文锚（`_clear_receipt(s)` 同串也出现在 def 行，裸替换会拆签名）
m = stripped("R5", "_clear_receipt(s)\n    if s.get", "pass\n    if s.get")
if m:
    c5 = Case("R5", mod=m)
    try:
        ladder(c5, up_to="stall")
    except AssertionError as e:
        bad("R5 变异体基线未跑通(先于红断言)", str(e))
    else:
        c5.inbox(["24", "25"], acked=["23"])
        rc, out = c5.tick(T0 + 1000)
        ok("R5 撤修复必红(清了一条仍不重开闸=ALARMED压死)",
           "ALARMED" in out and " LAUNCH " not in out and c5.state()["alarmed"] is True)

# R6 BUG-RUNNER-GONE-1 缺位闸（11-04 04:5x 事故：起会话核被 OpenClaw 切流整树归档，
#     三路拉起静默空转近 6h——powershell 报 -File 不存在秒退、回执永不落、白烧三窗）。
#     绿：RUNNER 指不存在文件 ⇒ launch 必返 RUNNER-MISSING；红证：撤缺位闸变体=照写 brief
#     并组 argv 空起（Popen 换名单 spy，零真 powershell）。CC_TEAM_HOME 先设=brief 落沙箱。
def _iw6(variant=None):
    import importlib.util
    home6 = os.path.join(HOME, "case-R6" + ("-m" if variant else ""))
    os.makedirs(os.path.join(home6, "logs"), exist_ok=True)
    os.environ["CC_TEAM_HOME"] = home6
    os.environ.pop("INBOX_WATCH_SIM_LAUNCH", None)
    src = open(MOD, encoding="utf-8").read()
    if variant == "stripped":
        a = '    if not os.path.isfile(RUNNER):\n        return None, "RUNNER-MISSING'
        if a not in src:
            bad("R6 撤点缺失(锁腐：缺位闸不在源)", a[:40]); return None
        src = src.replace(a, '    if False:\n        return None, "RUNNER-MISSING', 1)
        b = ('subprocess.Popen(runner_argv(to_win(fn)), stdin=subprocess.DEVNULL,\n'
             '                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WIN)')
        if b not in src:
            bad("R6 Popen spy 撤点缺失(锁腐)", b[:40]); return None
        src = src.replace(b, "POPEN_SPY.append(runner_argv(to_win(fn)))", 1)
        src = "POPEN_SPY = []\n" + src
    tmpmod = os.path.join(home6, "iw_variant.py")
    with open(tmpmod, "w", encoding="utf-8") as f:
        f.write(src)
    sys.path.insert(0, SRC)
    spec = importlib.util.spec_from_file_location("iw_r6" + ("_m" if variant else ""), tmpmod)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

_m6 = _iw6()
if _m6:
    _m6.RUNNER = os.path.join(HOME, "case-R6", "gone", "run-cc.ps1")   # 不存在=缺位态
    _fn6, _err6 = _m6.launch()
    _g6 = (_fn6 is None and "RUNNER-MISSING" in (_err6 or "")
           and not os.listdir(os.path.join(HOME, "case-R6", "logs")))
    ok("R6 缺位闸在场：RUNNER 不存在⇒RUNNER-MISSING 且零 Popen 零 brief") if _g6 else bad(
       "R6 缺位闸失灵", "fn=%r err=%r" % (_fn6, _err6))
    _m6s = _iw6("stripped")
    if _m6s:
        _m6s.RUNNER = os.path.join(HOME, "case-R6-m", "gone", "run-cc.ps1")
        _fn6s, _err6s = _m6s.launch()
        _r6 = bool(_fn6s) and "RUNNER-MISSING" not in (_err6s or "") \
              and len(_m6s.POPEN_SPY) == 1 and _m6s.POPEN_SPY[0][-1] == "pmmail"
        ok("R6 撤修复必红(撤缺位闸=照写brief组argv空起，正是6h空转事故形)") if _r6 else bad(
           "R6 变异基线未跑通", "fn=%r err=%r spy=%r" % (_fn6s, _err6s, getattr(_m6s, "POPEN_SPY", None)))

# ================= 收口 =================
shutil.rmtree(HOME, ignore_errors=True)
if fails:
    print("PM_WAKE_RECEIPT_LOCK_FAIL: " + ",".join(fails))
    sys.exit(1)
print("PM_WAKE_RECEIPT_LOCK_PASS cases=%d" % (11 + 5 + 1))   # +1= R6 缺位闸对（BUG-RUNNER-GONE-1）
