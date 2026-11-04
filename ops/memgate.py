#!/usr/bin/env python3
# cc-team/ops/memgate.py — 宿主内存水位探针（MECH-MEMGATE-1，业主 2026-11-02 卡）。
# 背景：本宿实测 CPU 28 核只用 10%，但 64G 只剩 1.9G —— 并行上限是内存不是 CPU。
#   五道工人 + e2e 浏览器 + 后端容器同跑，再盲扩到 10 道 = 内存耗尽、测试假红、
#   车队看似满负荷实为僵尸。分工：dispatch.py 在发新道前查此水位，低于地板
#   本圈不发新单喊 MEMGATE-SKIP（只续心跳与道内在制）；recycle.py 同面只报警
#   喊 MEMGATE-WARN 不拦事（回收本身是救援动作）。与 MECH-FLEET-ALARM-1 互补=
#   那条报闲，这条防撑。
# 读数口径：Win32_OperatingSystem.FreePhysicalMemory（KB）= 宿主真实空闲物理内存。
#   WSL 里 /proc/meminfo 是 VM 账本不反映宿主，禁用；探针走 powershell.exe（WSL
#   与 Git Bash 两侧路径都试）。测不到返回 None——探针失败≠水位高，调用侧按
#   fail-open 放行并喊 MEMGATE-PROBE-FAIL（防撑闸不该把车队闸死成静默）。
# 阈值：地板默认 8192MB（卡面建议 8G 起步实测调），env MEMGATE_MIN_FREE_MB 覆盖。
# 自测后门：env MEMGATE_FORCE_MB=<int> 伪造水位给 dispatch.py --selftest 上红绿锁；
#   生产别设，日志 note 里会标「伪」留痕。
import os, shutil, subprocess
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

POWERSHELL_CANDIDATES = [
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",   # WSL 视角
    "/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",       # Git Bash 视角
]
DEFAULT_MIN_FREE_MB = 8192


def min_free_mb():
    try:
        return int(os.environ.get("MEMGATE_MIN_FREE_MB") or DEFAULT_MIN_FREE_MB)
    except ValueError:
        return DEFAULT_MIN_FREE_MB


def find_powershell():
    w = shutil.which("powershell.exe")
    if w:
        return w
    for p in POWERSHELL_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def host_free_mem_mb():
    """宿主空闲物理内存 MB；None=测不到。MEMGATE_FORCE_MB 在=伪读数（selftest 专用）。"""
    forced = os.environ.get("MEMGATE_FORCE_MB")
    if forced not in (None, ""):
        try:
            return int(forced)
        except ValueError:
            return None
    ps = find_powershell()
    if ps is None:
        return None
    try:
        r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command",
                            "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, creationflags=NO_WIN)
        digits = "".join(ch for ch in (r.stdout or "") if ch.isdigit())
        return int(digits) // 1024 if digits else None
    except Exception:
        return None


def status():
    """返回 (block: bool, note: str)。note 带数字与阈值，低水位时 block=True。"""
    free = host_free_mem_mb()
    thr = min_free_mb()
    if free is None:
        return False, "MEMGATE-PROBE-FAIL：宿主空闲物理内存测不到（fail-open 照发；请检查 powershell.exe 可达性）"
    forced = os.environ.get("MEMGATE_FORCE_MB")
    src = "伪" if forced not in (None, "") else "实测"
    if free < thr:
        return True, "空闲内存 %dMB(%s) < 发单地板 %dMB" % (free, src, thr)
    return False, "空闲内存 %dMB(%s) >= 发单地板 %dMB" % (free, src, thr)
