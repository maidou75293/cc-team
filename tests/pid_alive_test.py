#!/usr/bin/env python3
# pid_alive_test.py — MECH-PIDALIVE-WIN-1 红绿锁：config.pid_alive Windows 确死必判死。
# 病根（2026-11-03 Windows 宿主实测定谳，CPython3.10/Win11）：os.kill(pid,0) 在 Windows
#   ① 查无此进程 → OSError(winerror=87)——被旧保守分支整口吞=确死恒活；
#   ② 受保护进程(pid4) → SystemError——吞后判活（这一支该活，语义保留）；
#   ③ 退了未回收僵尸 → 不抛恒回活——句柄表对象在，os.kill 语义测不出死；
#   ④ pid>DWORD → OverflowError 裸崩（判活链不许崩）。
#   后果=.writer.lock 死锚自清/夺锁、claim 自愈、僵尸道锁接管恒失效（尸锁占死到 5min 兜底）。
#   修复=Win 侧改 OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)+GetExitCodeProcess 显式三分。
# 跑法: python3 tests/pid_alive_test.py   （exit 0 = 全绿；宿主无关：POSIX 侧跑共享层+静态层，
#       Windows 专属层按 os.name 判——本锁主战场=Win 宿主，卡面 verify 已钉 MINGW/MSYS/CYGWIN）
import json, os, shutil, subprocess, sys, tempfile, threading, time

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # cc-team 根
# 沙箱账根必须在 import ccards 之前落定（config 于 import 时绑定 TEAM/WLOCK）
SANDBOX = tempfile.mkdtemp(prefix="cc-pidalive-")
os.environ["CC_TEAM_HOME"] = SANDBOX
sys.path.insert(0, SRC)
from ccards import config, locks                                    # noqa: E402

fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, detail)


def check(name, cond, detail=""):
    ok(name) if cond else bad(name, detail)


# ===== L1 纯函数·负路径与非法形态：判死或不炸，绝不给活 =====
for v in (None, "x", 0, -1, 999999.7):
    try:
        r = config.pid_alive(v)
    except Exception as exc:
        bad("L1 负路径不抛(%r)" % (v,), repr(exc))
        continue
    check("L1 负路径判死(%r)" % (v,), r is False)
# 注：True 经 int() 变 1——Win11 pid 1 恒不存在、Linux pid 1=init 恒存在，跨宿主语义分叉，
# 本锁不测 bool 入参（不在调用面：meta 里的 pid 只会是 int/None/str）。

# ===== L2 活性正向：活人必须判活，绝不误夺 =====
check("L2 本进程判活", config.pid_alive(os.getpid()) is True)
p_live = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
check("L2 活子进程判活", config.pid_alive(p_live.pid) is True)

# ===== L3 确死判死（卡面判据同源层）=====
# 999999/1000003 非 4 倍数=Win 侧永不可能分配的 pid；POSIX 侧同理远超实发 pid 空间。
check("L3 死pid 999999 判死", config.pid_alive(999999) is False)
check("L3 死pid 1000003 判死", config.pid_alive(1000003) is False)

# ===== L4 Windows 专属语义（本卡病根本体）=====
if os.name == "nt":
    check("L4 受保护进程 pid4 保守判活(不崩)", config.pid_alive(4) is True)
    p = subprocess.Popen([sys.executable, "-c", "import sys;sys.exit(7)"])
    p.wait()
    time.sleep(0.1)
    # os.kill 时代恒 True（句柄表对象未回收也「活」）——GetExitCodeProcess 后能确死
    check("L4 已退子进程判死(os.kill 测不出僵尸的核心增量)", config.pid_alive(p.pid) is False)
    try:
        r = config.pid_alive(2**32 + 8)
        check("L4 超 DWORD 巨号判死不裸崩", r is False)
    except Exception as exc:
        bad("L4 超 DWORD 巨号判死不裸崩", repr(exc))
    p_live.terminate(); p_live.wait()
else:
    ok("L4 Windows 专属层跳过（非 Win 宿主：本层由卡面 verify 的 MINGW 闸钉宿主，防 WSL 假绿）")
    p_live.terminate(); p_live.wait()

# ===== L5 锁接线·产品语义（写锁两半段，同 selftest #70 谱系）=====
def acquire_in_thread(res, cmd="B_probe"):
    t = threading.Thread(target=_acquire_capture, args=(cmd, res), daemon=True)
    t.start()
    return t


def _acquire_capture(cmd, res):
    try:
        locks.team_lock_acquire(cmd)
        res["ok"] = True
    except SystemExit as e:
        res["die"] = e.code          # 45s 超时 die=没夺回来
    except BaseException as e:
        res["exc"] = repr(e)


def fresh_wlock():
    shutil.rmtree(config.WLOCK, ignore_errors=True)
    os.makedirs(os.path.dirname(config.WLOCK), exist_ok=True)


# 5a 空 meta=初始化中的活锁不被夺（MECH-RTY1 原闸不许倒退）
fresh_wlock()
os.mkdir(config.WLOCK)
res = {}
t = acquire_in_thread(res)
time.sleep(1.5)
check("L5a 空meta活锁不被夺", t.is_alive() and os.path.isdir(config.WLOCK))
# 5b 同窗内落死锚 meta → 必须立刻自清夺回（修复前：t.join(25) 到点线程仍活着=红）
json.dump({"cmd": "ghost", "pid": 999999, "ts": config.now()},
          open(os.path.join(config.WLOCK, "meta.json"), "w", encoding="utf-8"))
t.join(25)
check("L5b 死锚锁按自清规则夺回", bool(res.get("ok")) and not res.get("die") and not t.is_alive(),
      "res=%r" % (res,))
if res.get("ok"):
    locks.team_lock_release()

# 5c 活锚（本进程 pid）绝不被夺——修复矫枉过正反证：确活仍保守
fresh_wlock()
os.mkdir(config.WLOCK)
json.dump({"cmd": "live-holder", "pid": os.getpid(), "ts": config.now()},
          open(os.path.join(config.WLOCK, "meta.json"), "w", encoding="utf-8"))
res2 = {}
t2 = acquire_in_thread(res2)
time.sleep(2.5)
check("L5c 活锚锁不被夺(判活修复不误伤保守档)", t2.is_alive() and not res2)
shutil.rmtree(config.WLOCK, ignore_errors=True)   # 让等待线程下轮 mkdir 即成，daemon 随进程退

# ===== L6 变异自证：旧实现在本锁下必红（守卫红得起来，行为级对照零活文件改动）=====
def old_pid_alive(pid):
    """修复前 pid_alive 逐字副本——证明 L3/L4 断言在旧行为下确红（可区分性=变异锁）。"""
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
        return True


if os.name == "nt":
    check("L6 变异红证·旧实现在 Windows 把死 pid 恒判活(本锁修复前必红的复现)",
          old_pid_alive(999999) is True and old_pid_alive(1000003) is True)
    try:
        old_pid_alive(2**32 + 8)
        bad("L6 变异红证·旧实现巨号裸崩", "未抛 OverflowError")
    except OverflowError:
        ok("L6 变异红证·旧实现巨号裸崩(新档判死不崩=回归锁住崩溃面)")
else:
    ok("L6 变异红证·旧实现 POSIX 侧与新档同行为(可区分性由 Win 宿主层保证，本机跳过)")

# ===== L7 静态锁：nt 分派与 Win32 三分类不许被悄悄摘除 =====
src = open(os.path.join(SRC, "ccards", "config.py"), encoding="utf-8").read()
check("L7 pid_alive 有 nt 分派入口", 'os.name == "nt"' in src and "_win_pid_alive(pid)" in src)
check("L7 Win32 原语在案(0x1000+退出码 259)", "OpenProcess(0x1000" in src and "259" in src)
check("L7 确死错误码在案(87/2 判死分支)", "err in (2, 87)" in src)
check("L7 DWORD 上界闸在案(巨号不裸崩)", "pid > 0xFFFFFFFF" in src)

shutil.rmtree(SANDBOX, ignore_errors=True)
if fails:
    print("PID_ALIVE_FAIL %d 条: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("PID_ALIVE_PASS")
