#!/usr/bin/env python3
# ctrld-ringstall-test.py — cc-ctrld 时间轮「慢/堵环不冻结其余环」常驻红绿锁
# （BUG-CTRLD-RINGSTALL-1，业主 2026-11-03 立卡；保险丝 S5 11:00 轮处置副产归因）。
#
# 事故签名（现网 ctrld.log/fuse.log 实锤，证据见卡）：
#   loop_core 在主线程**同步串行**跑环 ⇒ 单环实测时长（reconcile ~147s×24 次/日、
#   sweep 验判据/池起会话 330~513s）远超其余环 60s 节拍 ⇒ 整轮停摆 ~6min；
#   fuse 保险丝环同在 serial loop ⇒ 同窗自检失明（fuse.log 11:11:16→11:17:28 零行）。
#   潜在放大件=Windows subprocess.run(capture_output,timeout) 超时 kill 后仍要等管道
#   EOF——起会话的孙进程持继承句柄=超时失真。
#
# 本锁七案（每案各钉修复的一个面；撤对应修复=该案必红）：
#   A 结构案（撤「环起独立线程」→快环在慢环窗口内 0 拍=红）：真 loop() 沙箱驱动，
#     慢环睡 6.5s 期间快环仍按 ~1s 节拍落拍，且 ctrld.log 相邻 RING 行断档 ≤3.5s。
#   B 管道案（撤「输出文件重定向」→run_ring 堵在孙进程管道 EOF ≈6s=红）：直系子秒退、
#     孙持 stdout 6s 不写；新码 run_ring 应 <2.5s 返回 rc=0。
#   C 杀树案（撤 kill_tree→holder 孙进程杀不死继续写心跳文件=红）：timeout=2 到点，
#     断言整树停写（孙的滴答文件不再增长）且环在 <25s 内有界收线+RING-TIMEOUT 留痕。
#   D 互斥案（撤同脚本互斥组→full/sweep 重叠跑=红）：同 script 两环同拍只起一条，
#     前一环收线后再 fire 才可跑；marker 的 S/E 配平任意时刻并发 ≤1。
#   E 看门案（撤 watchdog→堵环永占环位=红）：注入超龄 busy 环，watchdog 留痕 RING-STUCK、
#     杀树、放行环位，随后同名环可再 fire。
#   F 旧锁回归：ctrld.py --selftest（T1~T7）必全绿，且 --fault=schedule 抽测必红
#     （守卫红得起来——本锁不把既有节拍排期锁改软）。
#   G 饿死案（BUG-CTRLD-SWEEPSTARVE-1，撤「跳拍记欠账+空位补放」→低优先环 0 拍=红）：
#     同脚本两环同节拍同相位沙箱对跑，断言 sweep 拍≥6、≥0.5×full、且并发恒 ≤1（互斥不动）。
#   H 静退案（23:27:30 现网实锤，撤「拍体 try/except」→注入异常掀翻进程=红）：
#     BoomWheel 桩按拍抛预排异常，断言引擎不死落「收 ticks=8」、首见签全栈恰 1 行、
#     同签抑制（退场报 n=2）、换签再落全栈、节拍不断。
#   I 双引擎案（00:08:43 调度器竞起实锤，撤「起前心跳互斥」→活 pid 也放行=红）：
#     五断言=活对头拒生/死对头放行/shadow 不拦/无 hb 放行/自 pid 放行。
#
# 手法纪律：全部沙箱=CC_TEAM_HOME 指临时根、环 argv=注入的假脚本，**不碰真账、不起真会话**；
#   高危案（A/B/C/D/E）各经子进程独立跑并带挂钟看门（撤修复致挂=看门红，测试永不自挂）。
# 跑法: python -I cc-team/tests/ctrld-ringstall-test.py
#       末行 CTRLD_RINGSTALL_PASS = 全绿；exit 0。
import io, os, subprocess, sys, tempfile, threading, time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))          # cc-team/tests
ROOT = os.path.dirname(HERE)                               # cc-team
OPS_DIR = os.path.join(ROOT, "ops")
NO_WIN = 0x08000000 if os.name == "nt" else 0
PY = sys.executable

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("ctrld_ringstall_ops", os.path.join(OPS_DIR, "ctrld.py"))
ctrld = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ctrld)

# ---------- 沙箱假环脚本（只写 marker/心跳文件，绝不出沙箱） ----------

SLEEPER = '''
import sys, time
secs, tag, marker = float(sys.argv[1]), sys.argv[2], sys.argv[3]
with open(marker, "a", encoding="utf-8") as f:
    f.write("S %s %.3f\\n" % (tag, time.time()))
time.sleep(secs)
with open(marker, "a", encoding="utf-8") as f:
    f.write("E %s %.3f\\n" % (tag, time.time()))
'''

TICKER = '''
import sys, time
tag, marker = sys.argv[1], sys.argv[2]
with open(marker, "a", encoding="utf-8") as f:
    f.write("T %s %.3f\\n" % (tag, time.time()))
'''

PIPEHOLD = '''
import sys, subprocess
# 孙进程继承直系子的 stdout 句柄后睡 N 秒不写；直系子立即退出。
# 管道 capture 时代：run() 即便 kill 掉直系子，communicate 仍等管道 EOF=堵 N 秒（=红案）。
subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(%s)" % sys.argv[1]])
'''

HOLDER = '''
import sys, time
hb = sys.argv[1]
# 时限 30s 自愈：正常案里 kill_tree 秒杀；撤修复的 legacy 版若杀不到，孤儿最多 30s 自退，
# 不在红检循环里攒僵尸进程。观测窗（+3s）远短于时限，语义不变。
t_end = time.time() + 30
while time.time() < t_end:
    with open(hb, "a", encoding="utf-8") as f:
        f.write("%.3f\\n" % time.time())
    time.sleep(0.25)
'''

KILLHOLD = '''
import sys, subprocess, time
# 直系子=长睡（触环超时），孙=holder 每 0.25s 滴答写文件。只杀直系子的旧码=孙脱管永写。
subprocess.Popen([sys.executable, "-I", sys.argv[1], sys.argv[2]])
time.sleep(600)
'''


def sandbox():
    home = tempfile.mkdtemp(prefix="ctrld-ringstall-home-")
    os.makedirs(os.path.join(home, "logs"), exist_ok=True)
    work = tempfile.mkdtemp(prefix="ctrld-ringstall-work-")
    paths = {}
    for name, src in (("sleeper", SLEEPER), ("ticker", TICKER), ("pipehold", PIPEHOLD),
                      ("holder", HOLDER), ("killhold", KILLHOLD)):
        p = os.path.join(work, name + ".py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
        paths[name] = p
    return home, work, paths


# ---------- 六案（--only 模式下在子进程内跑，返回 0=绿 1=红） ----------

def _lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [ln.split() for ln in f if ln.strip()]
    except OSError:
        return []


def _ring_gaps(logpath, t_lo, t_hi):
    """ctrld.log 指定窗内相邻 RING 行最大断档秒（时间戳=秒精度）。窗尾截到慢环收线
    +1s——loop 退场后的 drain 尾差（第二轮慢环）不算停摆，别误判。"""
    from datetime import datetime
    ts = []
    for ln in _open_lines(logpath):
        parts = ln.split()
        if len(parts) >= 4 and parts[2].startswith("RING") and parts[2] != "RING-DRAIN":
            try:
                t = datetime.strptime(parts[0] + " " + parts[1], "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                continue
            if t_lo <= t <= t_hi:
                ts.append(t)
    ts.sort()
    return max([b - a for a, b in zip(ts, ts[1:])] or [0.0])


def _open_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def case_A():
    """结构案：慢环跑 6.5s 期间，快环按拍续落、ctrld.log 无 >3.5s 断档。"""
    home, work, sp = sandbox()
    os.environ["CC_TEAM_HOME"] = home
    marker = os.path.join(work, "markerA.txt")
    fastm = os.path.join(work, "fastA.txt")
    rings = [
        {"name": "slow", "script": "slow.py", "every": 2, "timeout": 30,
         "argv": [PY, "-I", sp["sleeper"], "6.5", "slow", marker]},
        {"name": "fast", "script": "fast.py", "every": 1, "timeout": 10,
         "argv": [PY, "-I", sp["ticker"], "fast", fastm]},
    ]
    t_start = time.time()
    ctrld.loop(shadow=True, rings=rings, max_ticks=80, tick_sec=0.1)
    # S/E 成对还原每一段慢环窗口（别把第二段并进第一段，窗尾会假红）
    segs = []
    open_t = None
    for parts in _lines(marker):
        tag, t = parts[1], float(parts[2])
        if tag != "slow":
            continue
        if parts[0] == "S":
            open_t = t
        elif parts[0] == "E" and open_t is not None:
            segs.append((open_t, t)); open_t = None
    if not segs or segs[0][1] - segs[0][0] < 5:
        return False, "慢环窗口不全（S/E 未成对落盘）: %r" % (segs,)
    slow = segs[0]
    marks = [float(p[2]) for p in _lines(fastm)]
    inside = [t for t in marks if slow[0] < t < slow[1]]
    gap = _ring_gaps(os.path.join(home, "logs", "ctrld.log"), t_start, slow[1] + 1.0)
    # 并发≤1（同环不叠跑 IgnoreNew）：任一时刻 slow 段不得重叠
    overlap = any(segs[i][0] < segs[j][1] and segs[j][0] < segs[i][1]
                  for i in range(len(segs)) for j in range(i + 1, len(segs)))
    ok = (len(inside) >= 4 and gap <= 3.5 and not overlap)
    return ok, "慢窗内快环拍数=%d(需≥4) 断档=%.1fs(需≤3.5) 慢环段=%r" % (len(inside), gap, segs)


def case_B():
    """管道案：直系子秒退、孙持 stdout 6s——run_ring 必须 <2.5s 返回（旧管道版堵满 6s）。"""
    home, work, sp = sandbox()
    os.environ["CC_TEAM_HOME"] = home
    r = {"name": "pipehold", "script": "pipehold.py", "every": 60, "timeout": 2,
         "argv": [PY, "-I", sp["pipehold"], "6"]}
    t0 = time.time()
    spent = ctrld.run_ring(home, r)
    log = "\n".join(_open_lines(os.path.join(home, "logs", "ctrld.log")))
    ok = spent < 2.5 and ("RING pipehold rc=0" in log)
    return ok, "elapsed=%.1fs(需<2.5) 留痕=%s" % (spent, "RING pipehold rc=0" in log)


def case_C():
    """杀树案：timeout=2 到点必 kill_tree——holder 孙的滴答文件必须停长；环有界收线。"""
    home, work, sp = sandbox()
    os.environ["CC_TEAM_HOME"] = home
    hb = os.path.join(work, "holder-hb.txt")
    r = {"name": "killer", "script": "killer.py", "every": 60, "timeout": 2,
         "argv": [PY, "-I", sp["killhold"], sp["holder"], hb]}
    t0 = time.time()
    spent = ctrld.run_ring(home, r)
    n1 = len(_open_lines(hb))
    time.sleep(3.0)
    n2 = len(_open_lines(hb))
    log = "\n".join(_open_lines(os.path.join(home, "logs", "ctrld.log")))
    ok = (spent < 25 and n2 == n1 and "RING-TIMEOUT killer" in log and "kill_tree" in log)
    return ok, "elapsed=%.1fs 孙滴答 %d→%d(须相等) RING-TIMEOUT=%s" % (
        spent, n1, n2, "RING-TIMEOUT killer" in log)


def case_D():
    """互斥案：同 script 两环（模拟 recycle full/sweep）永不重叠。"""
    home, work, sp = sandbox()
    os.environ["CC_TEAM_HOME"] = home
    marker = os.path.join(work, "markerD.txt")
    mk = lambda tag: {"script": "recycle.py", "every": 60, "timeout": 30,
                      "argv": [PY, "-I", sp["sleeper"], "3", tag, marker]}
    r1 = mk("R1"); r1["name"] = "full"
    r2 = mk("R2"); r2["name"] = "sweep"
    w = ctrld.Wheel(home)
    w.fire(r1)
    w.fire(r2)                                    # 同脚本在跑=应跳过不叠跑
    started_early = any(p[1] == "R2" for p in _lines(marker))
    deadline = time.time() + 12
    while "full" in w.busy() and time.time() < deadline:
        time.sleep(0.2)
    w.fire(r2)                                    # 前一环收线后才准起
    w.drain(20)
    segs = {}
    order = []
    for p in _lines(marker):
        tag, t = p[1], float(p[2])
        if tag not in segs:
            segs[tag] = [t, t]; order.append(tag)
        else:
            segs[tag][1] = max(segs[tag][1], t)
    # 配平扫描：任意时刻并发 ≤1
    pts = []
    for tag, (s, e) in segs.items():
        pts.append((s, 1)); pts.append((e, -1))
    cur = mx = 0
    for _, d in sorted(pts):
        cur += d
        mx = max(mx, cur)
    ok = (not started_early) and mx <= 1 and len(segs) == 2 and all(len(segs[t]) == 2 for t in segs)
    return ok, "抢跑=%s 最大并发=%d 段=%r" % (started_early, mx, segs)


def case_E():
    """看门案：超龄堵环=RING-STUCK 留痕+杀树+放行环位，随后可再 fire。"""
    home, work, sp = sandbox()
    os.environ["CC_TEAM_HOME"] = home
    marker = os.path.join(work, "markerE.txt")
    r = {"name": "stuck", "script": "stuck.py", "every": 60, "timeout": 1,
         "argv": [PY, "-I", sp["sleeper"], "8", "S1", marker]}
    logs = []
    w = ctrld.Wheel(home, grace=0.5)
    w.fire(r)
    b = w.busy()["stuck"]
    b["t0"] -= 30                                 # 注入超龄（=堵死环在现网的样子）
    n = w.watchdog(now=time.time(), log=logs.append)
    freed = "stuck" not in w.busy()
    # 放行后同名环可再 fire（泄漏线程已被 kill_tree，不占环位）
    w.fire(r)
    refired = "stuck" in w.busy()
    w.drain(15)
    ok = (n == 1 and freed and refired and any("RING-STUCK stuck" in m for m in logs))
    return ok, "放行=%d 留痕=%r 再fire=%s" % (n, logs[:1], refired)


def case_G():
    """饿死案（BUG-CTRLD-SWEEPSTARVE-1）：同脚本两环同节拍同相位（沙箱加速复刻现网
    recycle full/sweep 每 60s 同在 :52 到点）——旧「跳拍即丢」=full 先占组位则 sweep
    每拍必撞 busy，现网 12:36:51 起 sweep 15min 零拍、REDAUTO 转正通道全停。
    修复=到点撞组忙记欠账，组内空位补放（不叠跑）。断言三件：
      ① 低优先环真落拍 ≥6（现行永跳=0 拍，撤修复必红）；
      ② sweep/full 拍数比 ≥0.5（不饿死=按占比拿组位，不是偶发漏一拍）；
      ③ 互斥保留：marker S/E 配平任意时刻并发 ≤1（recycle flock 语义不动）。"""
    home, work, sp = sandbox()
    os.environ["CC_TEAM_HOME"] = home
    marker = os.path.join(work, "markerG.txt")
    mk = lambda dur, tag: {"script": "recycle.py", "every": 3, "timeout": 30,
                           "argv": [PY, "-I", sp["sleeper"], str(dur), tag, marker]}
    r1 = mk(2.7, "full"); r1["name"] = "full"      # 占位≈一个节拍（现网 full 常 ≥60s）
    r2 = mk(0.4, "sweep"); r2["name"] = "sweep"
    ctrld.loop(shadow=True, rings=[r1, r2], max_ticks=320, tick_sec=0.1)
    segs, starts = {}, {"full": 0, "sweep": 0}
    for p in _lines(marker):
        kind, tag, t = p[0], p[1], float(p[2])
        if kind == "S":
            starts[tag] += 1
            segs.setdefault(tag, []).append([t, t])
        elif kind == "E" and segs.get(tag):
            segs[tag][-1][1] = max(segs[tag][-1][1], t)
    pts = []
    for tag, ss in segs.items():
        for s, e in ss:
            pts.append((s, 1)); pts.append((e, -1))
    cur = mx = 0
    for _, d in sorted(pts):
        cur += d
        mx = max(mx, cur)
    nf, ns = starts["full"], starts["sweep"]
    ok = (ns >= 6 and nf >= 1 and ns >= 0.5 * nf and mx <= 1)
    return ok, "full拍=%d sweep拍=%d(需≥6且≥0.5×full) 最大并发=%d(需≤1)" % (nf, ns, mx)


def case_H():
    """静退案（2026-11-03 23:27:30 现网实锤）：拍体（loop_core/watchdog/write_hb/
    supervise_board 任一）抛异常=旧码直接掀翻常驻进程，宿主 pythonw 无 stderr=
    traceback 蒸发，日志只剩「收 ticks STOP=False」一行无法归因，且 finally 的
    board.terminate 连带杀看板。修复=拍体 try/except：首见签落全栈 TICK-ERR、
    同签 60s 内抑制计数、换签再落全栈、退场补报总数、引擎绝不因此死。
    手法=注入 BoomWheel 桩（不碰真环），watchdog 按拍号抛预排异常。断言四件：
      ① loop() 正常返回且「收 ticks=8」落账（撤 try/except=异常掀翻=本案必红）；
      ② boom-same 全栈恰 1 行（第 2/3 拍同签被抑制）；
      ③ boom-diff 换签再落全栈恰 1 行；
      ④ 退场「抑制重复 n=2」落账 + watchdog 实际被叫 ≥8 拍（错误后节拍不断）。"""
    home, work, sp = sandbox()
    os.environ["CC_TEAM_HOME"] = home

    class BoomWheel:
        def __init__(self, sched):
            self.sched = sched
            self.calls = 0
        def fire(self, r):
            pass
        def watchdog(self):
            self.calls += 1
            exc = self.sched.get(self.calls)
            if exc:
                raise exc
        def drain(self, deadline=None, poll=0.2):
            pass

    w = BoomWheel({1: RuntimeError("boom-same"), 2: RuntimeError("boom-same"),
                   3: RuntimeError("boom-same"), 5: ValueError("boom-diff")})
    ctrld.loop(shadow=True, rings=[], wheel=w, max_ticks=8, tick_sec=0.02)
    log = "\n".join(_open_lines(os.path.join(home, "logs", "ctrld.log")))
    full = lambda kw: sum(1 for ln in log.splitlines()
                          if " TICK-ERR " in ln and "Traceback" in ln and kw in ln)
    n_same, n_diff = full("boom-same"), full("boom-diff")
    ok = ("收 ticks=8" in log and n_same == 1 and n_diff == 1
          and "TICK-ERR 抑制重复 n=2" in log and w.calls >= 8)
    return ok, "收账=%s same全栈=%d(需1) diff全栈=%d(需1) 抑制行=%s calls=%d(需≥8)" % (
        "收 ticks=8" in log, n_same, n_diff, "TICK-ERR 抑制重复 n=2" in log, w.calls)


def case_I():
    """双引擎案（00:08:43 调度器竞起现网实锤：旧实例健在时 RestartCount 又拉起一份=
    双环双写账+看板重叠影，23:32 三起同理）。撤 _singleton_ok 守卫=活 pid 也放行=本案必红。
    断言五件：①hb mode=live pid 活着⇒拒生 False；②该 pid 死⇒放行 True（正常换血）；
    ③hb mode=shadow⇒不拦 live，且 shadow 自身恒 True；④无 hb⇒True；⑤hb=自己 pid⇒True。"""
    home, work, sp = sandbox()
    os.environ["CC_TEAM_HOME"] = home
    hb = os.path.join(home, "logs", "ctrld.heartbeat")
    os.makedirs(os.path.dirname(hb), exist_ok=True)
    sleeper = subprocess.Popen([PY, "-I", sp["sleeper"], "30", "I", os.path.join(work, "markerI.txt")],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WIN)
    try:
        import datetime as _dt0
        ts_now = _dt0.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(hb, "w", encoding="utf-8") as f:
            f.write("%s ctrld mode=live pid=%d board=- rings=x/60s\n" % (ts_now, sleeper.pid))
        a = ctrld._singleton_ok(home, False) is False
        sleeper.kill(); sleeper.wait()
        b = ctrld._singleton_ok(home, False) is True
        with open(hb, "w", encoding="utf-8") as f:
            f.write("2026-11-04 00:00:00 ctrld mode=shadow pid=%d board=- rings=x/60s\n" % os.getpid())
        c = (ctrld._singleton_ok(home, False) is True) and (ctrld._singleton_ok(home, True) is True)
        d = ctrld._singleton_ok(home + "-none", False) is True
        with open(hb, "w", encoding="utf-8") as f:
            f.write("2026-11-04 00:00:00 ctrld mode=live pid=%d board=- rings=x/60s\n" % os.getpid())
        e = ctrld._singleton_ok(home, False) is True
        # ⑥ 时效门槛（00:28:16 秒级复起竞态案）：pid 真活着但心跳龄>120s ⇒ 放行
        import datetime as _dt
        sleeper2 = subprocess.Popen([PY, "-I", sp["sleeper"], "30", "I2", os.path.join(work, "markerI2.txt")],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WIN)
        try:
            old = (_dt.datetime.now() - _dt.timedelta(seconds=300)).strftime("%Y-%m-%d %H:%M:%S")
            with open(hb, "w", encoding="utf-8") as f:
                f.write("%s ctrld mode=live pid=%d board=- rings=x/60s\n" % (old, sleeper2.pid))
            f_ = ctrld._singleton_ok(home, False) is True
            fresh_ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(hb, "w", encoding="utf-8") as f:
                f.write("%s ctrld mode=live pid=%d board=- rings=x/60s\n" % (fresh_ts, sleeper2.pid))
            g_ = ctrld._singleton_ok(home, False) is False      # 同 pid 心跳新⇒照拒（门槛没把锁放空）
        finally:
            try:
                sleeper2.kill(); sleeper2.wait()
            except Exception:
                pass
        ok = a and b and c and d and e and f_ and g_
        return ok, "活pid拒=%s 死pid放=%s shadow面=%s 无hb=%s 自pid=%s 陈龄放=%s 新龄拒=%s" % (a, b, c, d, e, f_, g_)
    finally:
        try:
            sleeper.kill()
        except Exception:
            pass


def case_J():
    """收孤儿案（BUG-FUSE-SELFKILL-1②：Stop/kill 只杀引擎根，孤儿环继续持「同脚本组」锁
    → 新引擎 dispatch 永 RING-OWED → dispatcher.heartbeat 断更 → S1 误判断流把新引擎也
    处决=今夜 00:28/01:00/01:05/01:15 死亡螺旋的上游病根）。撤 _reap_orphans=孤儿照活=必红。
    断言三件：①父已死的 sleeper 被收（n≥1 且该 pid 亡）；②同款但父活着（本用例=活父）的
    对照 sleeper 不动（保守面=PM 会话/车道 worker 不误伤）；③留痕 REAP-ORPHAN 落 ctrld.log。"""
    home, work, sp = sandbox()
    os.environ["CC_TEAM_HOME"] = home
    os.environ["CTRLD_REAP_WILD"] = "*markerJ*"
    kids = []
    try:
        # ① 孤儿：甩手 launcher 起 sleeper，launcher 即退 ⇒ sleeper 父=死号
        pidf = os.path.join(work, "launcherJ.pid.txt")
        marker1 = os.path.join(work, "markerJ-orphan.txt")
        launcher_src = ("import subprocess,sys\n"
                        "p=subprocess.Popen([sys.executable,'-I',sys.argv[1],'30','J1',sys.argv[2]],"
                        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=%d)\n"
                        "open(sys.argv[3],'w').write(str(p.pid))\n" % NO_WIN)
        lt = subprocess.run([PY, "-I", "-c", launcher_src, sp["sleeper"], marker1, pidf],
                            timeout=30)
        with open(pidf, encoding="utf-8") as f:
            orphan_pid = int(f.read().strip())
        deadline = time.time() + 20                      # 等孤儿确生（父死子活态）
        while time.time() < deadline and not ctrld._pid_alive(orphan_pid):
            time.sleep(0.5)
        born = ctrld._pid_alive(orphan_pid)
        # ② 对照组：活父（本用例进程）同款 sleeper——命令行一样命中 wild，靠父活保住
        marker2 = os.path.join(work, "markerJ-alive.txt")
        live_kid = subprocess.Popen([PY, "-I", sp["sleeper"], "30", "J2", marker2],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=NO_WIN)
        kids.append(live_kid)
        n = ctrld._reap_orphans(home)
        deadline = time.time() + 15                      # 等 taskkill 生效
        while time.time() < deadline and ctrld._pid_alive(orphan_pid):
            time.sleep(0.5)
        a = born and n >= 1 and not ctrld._pid_alive(orphan_pid)
        time.sleep(2)
        b = ctrld._pid_alive(live_kid.pid)               # 二查仍在=没被收
        try:
            with open(os.path.join(home, "logs", "ctrld.log"), encoding="utf-8",
                      errors="replace") as f:
                log = f.read()
        except OSError:
            log = ""
        c = "REAP-ORPHAN" in log
        ok = a and b and c
        return ok, "孤收=%s 活父护=%s 留痕=%s n=%d" % (a, b, c, n)
    finally:
        for k in kids:
            try:
                k.kill(); k.wait()
            except Exception:
                pass


def case_F():
    """旧锁回归：ctrld --selftest 全绿；且 --fault=schedule 抽测必红（守卫红得起来）。"""
    ops = os.path.join(OPS_DIR, "ctrld.py")
    a = subprocess.run([PY, "-I", ops, "--selftest"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WIN)
    b = subprocess.run([PY, "-I", ops, "--selftest", "--fault=schedule"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WIN)
    ok = a.returncode == 0 and "ALL-GREEN" in (a.stdout or "") and b.returncode != 0
    return ok, "selftest rc=%d fault rc=%d" % (a.returncode, b.returncode)


CASES = {"A_ring_isolation": case_A, "B_pipe_deadlock": case_B, "C_kill_tree": case_C,
         "D_script_mutex": case_D, "E_watchdog": case_E, "F_selftest_regress": case_F,
         "G_sweep_starve": case_G, "H_tick_exception": case_H, "I_singleton": case_I,
         "J_reap_orphan": case_J}
TIMEBOX = {"A_ring_isolation": 40, "B_pipe_deadlock": 20, "C_kill_tree": 30,
           "D_script_mutex": 25, "E_watchdog": 25, "F_selftest_regress": 60,
           "G_sweep_starve": 90, "H_tick_exception": 30, "I_singleton": 25,
           "J_reap_orphan": 60}


def run_child(name):
    """高危案子进程跑：撤修复导致的无限堵=看门超时红，本测试永不自挂。"""
    fn = CASES[name]
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, "EXC %r" % (e,)
    print("CASE_RESULT %s rc=%d %.1fs %s" % (name, 0 if ok else 1, time.time() - t0, detail))
    return 0 if ok else 1


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--only":
        return run_child(sys.argv[2])
    fails, notes = [], []
    for name in CASES:
        try:
            r = subprocess.run([PY, "-I", os.path.abspath(__file__), "--only", name],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=TIMEBOX[name], creationflags=NO_WIN)
            out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
            line = next((ln for ln in out.splitlines() if ln.startswith("CASE_RESULT")), out[-200:])
            green = r.returncode == 0 and " rc=0 " in line
        except subprocess.TimeoutExpired:
            line = "TIMEBOX>%ds（撤修复=挂死，看门红）" % TIMEBOX[name]
            green = False
        print("%s %s | %s" % ("PASS:" if green else "FAIL:", name, line))
        if not green:
            fails.append(name)
    n = len(CASES)
    if fails:
        print("CTRLD_RINGSTALL FAIL(%d) cases=%d fails=%s" % (len(fails), n, ",".join(fails)))
        return 1
    print("CTRLD_RINGSTALL_PASS cases=%d fails=0" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
