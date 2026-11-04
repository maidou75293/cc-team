#!/usr/bin/env python3
# sweeplock-deadpid-test.py — BUG-SWEEPLOCK-DEADPID-1 红绿锁：recycle.pid_alive Windows 确死
#   必判死 → sweep_lock_try「死主即接管」立刻触发。
# 病根（2026-11-03 fuse S4 实烧 25min）：旧 os.kill(pid,0) 探测在 Windows 对死 pid 抛
#   OSError(winerror=87)/SystemError，全被旧保守 except 整口吞=确死恒活 → S4 重启 kill_tree
#   留下的 .recycle-sweep.lock 死主永不接管，只能等 SWEEP_LOCK_STALE_MIN=40min 到龄兜底，
#   火车只在 sweep 环 train_scan 里跑、被 sweep 锁挡在上游 → 全链停摆。
# 修复=委派 ccards.config.pid_alive（MECH-PIDALIVE-WIN-1 同引擎：OpenProcess+
#   GetExitCodeProcess，STILL_ACTIVE=活、查无此进程(2/87)=死、拿不到判定保守活）。
# 本锁五段：L1 真死子进程判死（卡面①）；L2 活主拒占不破（卡面③）；L3 死主即时接管+
#   meta 重写+接管行样式（卡面②）；L4 通道静态锁=pid_alive 消费者仅 sweep_lock_try
#   （full 环不共用此判定，full/sweep 重叠归 ctrld "script" 同脚本互斥）+委派引擎在案
#   （卡面④）+边界零改动锁（40min 兜底；mergetrain 判定 2026-11-04 改判委派权威件）；L5 变异自证=旧逐字副本
#   在 Win 宿主恒把确死判活，证明本锁断言在修复前必红（守卫红得起来）。
# 跑法: python -I cc-team/tests/sweeplock-deadpid-test.py
#       exit 0 且末行 SWEEPLOCK_DEADPID_PASS=全绿；红检流程见卡面⑤（临时还原旧分支必红，
#       证据 logs/，git checkout 复原）。
import ast, contextlib, io, json, os, shutil, subprocess, sys, tempfile, time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cc-team 根
OPS = os.path.join(SRC, "ops")
# 沙箱账根必须在 import recycle 之前落定（TEAM/STATE/SWEEP_LOCK 于 import 时绑定）——
# 接管/拒占都要真动锁，绝不许碰生产 state/.recycle-sweep.lock。
SANDBOX = tempfile.mkdtemp(prefix="cc-sweeplock-")
os.environ["CC_TEAM_HOME"] = SANDBOX
sys.path.insert(0, OPS)
import recycle                                       # noqa: E402
from ccards import config as _cfg                   # noqa: E402

fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, detail)


def check(name, cond, detail=""):
    ok(name) if cond else bad(name, detail)


def nowts():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def forge_sweep_lock(meta):
    """在生产路径之外的沙箱里伪造一把 sweep 锁（meta=None=裸锁无 meta）。"""
    shutil.rmtree(recycle.SWEEP_LOCK, ignore_errors=True)
    os.makedirs(os.path.dirname(recycle.SWEEP_LOCK), exist_ok=True)
    os.mkdir(recycle.SWEEP_LOCK)
    if meta is not None:
        with open(os.path.join(recycle.SWEEP_LOCK, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)


def read_meta():
    try:
        return json.load(open(os.path.join(recycle.SWEEP_LOCK, "meta.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ===== L0 沙箱接线：判活/锁路径全在沙箱，绝不碰生产账 =====
check("L0 recycle.STATE 落沙箱", recycle.STATE.startswith(SANDBOX), recycle.STATE)
check("L0 recycle.SWEEP_LOCK 落沙箱", recycle.SWEEP_LOCK.startswith(SANDBOX), recycle.SWEEP_LOCK)

# ===== L1 真死判定（卡面①：spawn 短命子进程退出后 pid_alive==False）=====
p_dead = subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.exit(7)"])
p_dead.wait()
time.sleep(0.2)   # 给内核释放句柄表对象留口（os.kill 时代僵尸恒活；GetExitCodeProcess 退码=7）
check("L1 已退子进程判死(卡面①)", recycle.pid_alive(p_dead.pid) is False)
check("L1 查无此进程 999999 判死", recycle.pid_alive(999999) is False)
check("L1 查无此进程 1000003 判死", recycle.pid_alive(1000003) is False)
check("L1 非法入参判死不裸崩",
      all(recycle.pid_alive(v) is False for v in (None, "x", 0, -1)))

# ===== L2 活主拒占不误杀（卡面③）=====
forge_sweep_lock({"pid": os.getpid(), "ts": nowts()})
buf2 = io.StringIO()
with contextlib.redirect_stdout(buf2):
    got2 = recycle.sweep_lock_try()
check("L2 活主(自身pid)锁拒占(卡面③)", got2 is False, "got=%r" % got2)
check("L2 拒占后锁目录完好不误删", os.path.isdir(recycle.SWEEP_LOCK))
check("L2 拒占后 meta 主未被改写", read_meta().get("pid") == os.getpid())
check("L2 拒占路径无接管行", "接管" not in buf2.getvalue())

# ===== L3 死主即时接管（卡面②：age≈0 远低于 2min/40min，接管只能来自死主判定）=====
forge_sweep_lock({"pid": p_dead.pid, "ts": nowts()})
age_before = (time.time() - os.path.getmtime(recycle.SWEEP_LOCK)) / 60.0
buf3 = io.StringIO()
with contextlib.redirect_stdout(buf3):
    got3 = recycle.sweep_lock_try()
out3 = buf3.getvalue()
check("L3 死主锁立即接管返回True(卡面②)", got3 is True, "got=%r out=%s" % (got3, out3.strip()))
check("L3 接管时锁龄远未到兜底(<2min,排除到龄路径)", age_before < 2.0, "age=%.3fmin" % age_before)
check("L3 接管后 meta 重写为本进程pid(卡面②)", read_meta().get("pid") == os.getpid())
check("L3 接管行样式保持(SWEEP-LOCK 接管陈旧锁(主=%s 龄…min) → 重占)" % p_dead.pid,
      ("SWEEP-LOCK 接管陈旧锁(主=%s 龄" % p_dead.pid) in out3 and "→ 重占" in out3,
      "out=%r" % out3)
shutil.rmtree(recycle.SWEEP_LOCK, ignore_errors=True)   # 本进程占着的锁先放掉再往下测

# ===== L4 通道与引擎静态锁（卡面④）=====
recycle_src = open(os.path.join(OPS, "recycle.py"), encoding="utf-8").read()
tree = ast.parse(recycle_src)
fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "pid_alive"), None)
check("L4 recycle.pid_alive 定义在案", fn is not None)
body_src = ast.get_source_segment(recycle_src, fn) or "" if fn else ""
# os.kill 只查**调用节点**（docstring 讲病根会提及该词，文本匹配=测试面假红，AST 才算数）
call_ids = set()
for node in ast.walk(fn) if fn else []:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
       and isinstance(node.func.value, ast.Name):
        call_ids.add("%s.%s" % (node.func.value.id, node.func.attr))
check("L4 旧 os.kill 探针已摘除(AST 调用层;确死恒活源头)", "os.kill" not in call_ids, sorted(call_ids))
check("L4 委派统一引擎 _cfg.pid_alive 或内联 OpenProcess 真判定在案",
      ("_cfg.pid_alive" in call_ids) or ("OpenProcess" in body_src), sorted(call_ids))
# 消费者收口：pid_alive 只许 sweep_lock_try 一个调用点——full 环不共用此判定（重叠防护
# 归 ctrld rings 的 "script" 同脚本互斥组），将来若有人再接通道，本锁必红逼回卡面对账。
consumers = set()
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name != "pid_alive":
        seg = ast.get_source_segment(recycle_src, n) or ""
        if "pid_alive(" in seg:
            consumers.add(n.name)
check("L4 pid_alive 消费者收口=仅 sweep_lock_try(full 环不共用=卡面④)", consumers == {"sweep_lock_try"},
      "consumers=%r" % (sorted(consumers),))
# 引擎同口径动态对拍（委派语义=两口径恒等）
check("L4 与 config 引擎同口径(死判死/活判活)",
      recycle.pid_alive(p_dead.pid) == _cfg.pid_alive(p_dead.pid) == False
      and recycle.pid_alive(os.getpid()) == _cfg.pid_alive(os.getpid()) == True)
# 边界零改动锁：age=40 兜底与接管窗原样。mergetrain 判定口径=2026-11-04 改判：
# 旧锁假定「os.kill 探针在它家是对的」（卡面 BUG-SWEEPLOCK-DEADPID-1 备注 L133-143
# except→alive=False 判死正确），当夜被 CTRL_C 广播案推翻——os.kill(pid,0) 的 sig=0
# 恰是 CTRL_C_EVENT，对活锁主=当场处决再夺锁（同族雷 BUG-FUSE-SELFKILL-1 追加面）。
# 收口方向不变（判死语义=死主让位/活主勿夺），载体改 cfg 权威件；本锁随之改形。
check("L4 边界:SWEEP_LOCK_STALE_MIN=40.0 兜底未动", recycle.SWEEP_LOCK_STALE_MIN == 40.0)
mt_src = open(os.path.join(OPS, "mergetrain.py"), encoding="utf-8").read()
check("L4 边界:mergetrain 锁主判定=委派 _cfg.pid_alive 且 os.kill 探针清零(2026-11-04 改判)",
      "_cfg.pid_alive(int(pid))" in mt_src and "os.kill(int(pid), 0)" not in mt_src)

# ===== L5 变异自证：修复前旧实现逐字副本，在本锁断言面必须给活（=本锁修复前必红）=====
def old_pid_alive(pid):
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
    except (OSError, SystemError, RuntimeError):
        # Windows 开不到句柄的 pid 抛 SystemError（非 OSError）——保守按活，宁漏不误杀
        return True


if os.name == "nt":
    check("L5 变异自证:旧实现在 Win 把已退子进程恒判活(本锁修复前 L1/L3 必红)",
          old_pid_alive(p_dead.pid) is True and old_pid_alive(999999) is True)
else:
    ok("L5 变异自证:POSIX 宿主旧新档同行为(可区分性由 Win 宿主层保证，本机跳过)")

shutil.rmtree(SANDBOX, ignore_errors=True)
if fails:
    print("SWEEPLOCK_DEADPID_FAIL %d 条: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("SWEEPLOCK_DEADPID_PASS")
