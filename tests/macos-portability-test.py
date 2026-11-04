#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macos-portability-test.py — macOS 移植圈红绿锁（REQ-OSS-MACOS-PORT-1，2026-11-04）。

病根账（第七圈 run 41816330476，macOS 26.6.2 实锤 33 条红，逐因归位）：
  因1 `touch -d "N ago"`=GNU 专属，BSD touch(macOS) 只有 -t → 全部时间回拨夹具静默落空，
     孤儿锁/空目录/recycle/lane/笔记闸门/83/89/展示行/adversary 态B 连片红（主因，占大头）。
  因2 `date -d '60 minutes ago'`=GNU 专属 → 89 progress 陈旧名牌行时间戳为空。
  因3 `timeout N`=GNU coreutils 件，macOS 无此令 → 65 watch --follow 挂死判据没跑成。
  因4 BSD `wc -l` 出数带 8 位宽填充 → "1"≠"       1"，watch --card 过滤假红。
  因5 `sed -i 'script'`=GNU 原地形，BSD -i 强制备份后缀（把脚本串当后缀报错）→ adversary 清残渣失效。
  因6 ops/check_exit.py::_proc_ancestors 只有 win(Toolhelp32)+linux(/proc) 两支——macOS 两无
     → 族谱恒取不到 → 79b/79c 散场交账全灭（HOOK_RC=2 及其五连带=同一因）。
     修=无 /proc 的 POSIX 宿主走 os.getppid()+`ps -o ppid= -p`（POSIX ps 关键字，BSD/GNU 同形）。
  因7 ops/recycle.py::_host_alias 用裸 `"win" in v` 子串判族——"posix/darWIN" 含 win →
     macOS 本机被认成对面宿主，host/via 冤案防线(REDAUTO-HOST-1①)在 macOS 永不触发 → 90 族三红。
     修=精确族映射：posix 前缀/linux/darwin 归 wsl 族；win 族只认 nt 前缀/win32/windows 与
     win|gitbash 精确名（弃用裸 "win" 子串=正是 darwin 撞号位）。

纪律（卡面）：禁宿主特判豁免——本锁的 macOS 专属段（前提探针 M 族）在非 macOS 显式 SKIP
并计数、不静默过；可移植子集（P 族）三宿主必跑。差分对（撤修复必红）：
  · 回退因7 → P1 红；回退因6 → P2/P3 红（macOS 全红、Linux 经 ps 探针同红）；
  · 回退因1/2/3/5 任一（测试脚本 GNU 形复活）→ P4 静态扫红；夹具原语坏 → P5 红。

跑法：python -I cc-team/tests/macos-portability-test.py → 末行 CC_MACOS_PASS；红=末行
CC_MACOS_FAIL 且 rc=1。全程沙箱（tempfile），不碰真实台账、不触网。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cc-team 根
sys.path.insert(0, ROOT)
# 被测模块 import 期读 CC_TEAM_HOME——先钉临时沙箱（同 lane-book-test 手法），绝不指真实正账。
os.environ["CC_TEAM_HOME"] = tempfile.mkdtemp(prefix="cc-macport-")

from ops import check_exit                                   # noqa: E402
from ops import recycle as recycle_mod                       # noqa: E402

IS_MAC = sys.platform == "darwin"
OKS = SKIPS = 0
FAILS = []


def ok(name):
    global OKS
    OKS += 1
    print("PASS: %s" % name)


def bad(name, detail=""):
    FAILS.append(name)
    print("FAIL: %s%s" % (name, (" —— " + detail) if detail else ""))


def check(cond, name, detail=""):
    if cond:
        ok(name)
    else:
        bad(name, detail)


def skip(name, why):
    global SKIPS
    SKIPS += 1
    print("SKIP(显式): %s —— %s" % (name, why))


# ============ P1 因7 差分对：_host_alias 精确族映射 ============
# 期望值=「90 族在 macOS 的正确归位」的机器可读版；任何回旧式宽松子串=红。
ALIAS = [
    ("nt/win32", "win"), ("win", "win"), ("gitbash", "win"), ("git-bash", "win"),
    ("posix/linux", "wsl"), ("posix/darwin", "wsl"), ("darwin", "wsl"),
    ("wsl", "wsl"), ("linux", "wsl"), ("Wsl", "wsl"),
    ("", None), (None, None), ("plan9", None), ("posix/irix", "wsl"),
]
bad1 = ["%r→%r(期望%r)" % (v, recycle_mod._host_alias(v), want)
        for v, want in ALIAS if recycle_mod._host_alias(v) != want]
check(not bad1, "P1 _host_alias 族映射含 posix/darwin=wsl（撤因7修复此条必红）",
      "; ".join(bad1))
check(recycle_mod._host_alias("posix/darwin") == "wsl",
      "P1b darWIN 子串不再被认成 win 侧（第七圈 90 族本体）")

# ============ P2 因6 差分对：族谱取到且自洽（三宿主同跑） ============
sp, chain = check_exit._proc_ancestors()
me, ppid = os.getpid(), os.getppid()
check(sp in ("win", "wsl"), "P2a _proc_ancestors 空间标签在册（macOS 无此支=None 即红）",
      "got=%r" % (sp,))
check(bool(chain) and me in chain, "P2b 链含自身 pid", "chain=%r" % (chain,))
check(sp is not None and ppid in chain, "P2c 链含直接父 pid（79b 族谱直证的前提）",
      "ppid=%r chain=%r" % (ppid, chain))

# ============ P3 因6 原语实测：POSIX ps 问 ppid ============
if os.name == "nt":
    # win 产品侧走 Toolhelp32；msys ps 的 pid 空间与 win32 pid 是否同表宿主间无定论，
    # 在此比=拿存疑通道钉结论（假红风险）→ 显式 SKIP；macOS/Linux 上本条真跑（P2 的另一半）。
    skip("P3 _ps_ppid 实测", "Windows 侧判活权威=Toolhelp32，msys ps pid 空间不可作比")
elif shutil.which("ps"):
    got3 = check_exit._ps_ppid(me)
    check(got3 == ppid, "P3 `ps -o ppid= -p` 取得父 pid（BSD/GNU 同形，撤因6修复此条必红）",
          "got=%r want=%r" % (got3, ppid))
else:
    skip("P3 _ps_ppid 实测", "本宿主 PATH 无 ps——产品走 ps 支在此宿主不可达，显式不静默")

# ============ P4 因1/2/3/5 差分对：CI 圈测试脚本 GNU 形静态扫 ============
# 扫注释行以外的一切出现——fix 被回退（GNU 形复活）即红；注释里讲病根原话不误伤。
BAN = [
    ("GNU touch 无 BSD 形（用 ago/os.utime）", "touch -d "),
    ("GNU date -d（用 python 现算时间戳）", "date -d "),
    ("GNU date --date", "date --date"),
    ("GNU-only sed -i（BSD 要备份后缀；用 tmp+mv）", "sed -i"),
    ("GNU timeout（macOS 无此令；用 to 后台闹钟）", "timeout "),
    ("GNU grep -P（BSD 无 -P）", "grep -P"),
    ("grep -oP", "-oP"),
    ("GNU stat -c（BSD 是 -f）", "stat -c"),
]
files = ["selftest.sh", "adversary.sh", "all.sh"]
hits = []
for f in files:
    p = os.path.join(ROOT, "tests", f)
    for ln, line in enumerate(open(p, encoding="utf-8").read().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for why, pat in BAN:
            # P4 自身函数名/助手名排除：`ago ` 与 `to 15` 形态不含被禁子串——逐字扫即可。
            if pat in line:
                hits.append("tests/%s:%d [%s]: %s" % (f, ln, why, line.strip()[:80]))
check(not hits, "P4 CI 圈 shell 脚本零 GNU-only 形（回退任一因此条必红）",
      "; ".join(hits[:4]))

# ============ P5 因1 原语实测：os.utime 回拨（ago 夹具的底座） ============
tdir = tempfile.mkdtemp(prefix="cc-macport-mt-")
fpath = os.path.join(tdir, "f.json")
open(fpath, "w").write("{}")
t_back = 1800
t = time.time() - t_back
os.utime(fpath, (t, t))
d_age = time.time() - os.stat(fpath).st_mtime
check(abs(d_age - t_back) <= 90, "P5a 文件 mtime 回拨=1800s 正负 90s 内", "age=%.0f" % d_age)
d_dir = os.path.join(tdir, "dir.lock")
os.makedirs(d_dir)
os.utime(d_dir, (t, t))
check(abs((time.time() - os.stat(d_dir).st_mtime) - t_back) <= 90,
      "P5b 目录 mtime 回拨可用（claims/<id> 目录形夹具的前提）")
shutil.rmtree(tdir, ignore_errors=True)

# ============ M 族：macOS 专属前提探针（非 macOS 显式 SKIP，不静默过） ============
why_mac = "断言对象=macOS/BSD 用户地实态，本宿主(%s)不可测" % sys.platform
if not IS_MAC:
    for n in ("M1 BSD touch 拒 -d", "M2 BSD date 拒 -d", "M3 BSD sed 裸 -i 报错",
              "M4 无 timeout 令"):
        skip(n, why_mac)
else:
    tb = os.path.join(tempfile.mkdtemp(prefix="cc-macport-m-"), "x")
    open(tb, "w").write("x")
    r1 = subprocess.run(["touch", "-d", "2 minutes ago", tb], capture_output=True, text=True)
    check(r1.returncode != 0, "M1 BSD touch -d 确不存在（P4 禁之的前提；若 macOS 转 GNU 化此条=前提变，重审）",
          "rc=%d" % r1.returncode)
    r2 = subprocess.run(["date", "-d", "60 minutes ago", "+%s"], capture_output=True, text=True)
    check(r2.returncode != 0, "M2 BSD date -d 确不存在", "rc=%d" % r2.returncode)
    r3 = subprocess.run(["sed", "-i", "/x/d", tb], capture_output=True, text=True)
    check(r3.returncode != 0, "M3 BSD sed 裸 -i 确报错（后缀强制）", "rc=%d" % r3.returncode)
    check(shutil.which("timeout") is None, "M4 timeout 令确缺位（因3 的实锤）")
    shutil.rmtree(os.path.dirname(tb), ignore_errors=True)

print("checks ok=%d fails=%d skips=%d mac=%s" % (OKS, len(FAILS), SKIPS, IS_MAC))
if FAILS:
    print("CC_MACOS_FAIL fails=%d" % len(FAILS))
    sys.exit(1)
print("CC_MACOS_PASS ok=%d skips=%d" % (OKS, SKIPS))
