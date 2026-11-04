#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cc_env_detect_test.py — 「一键使用」的 CC 环境检测红绿锁（REQ-OSS-PREP-1）。

锁四件事（全部离线，假 PATH/打桩，绝不触网、绝不真跑安装器）：
 1) _cc_status：空 PATH=未装；伪 claude 在 PATH=能发现并解析版本行。
 2) _cc_install_cmd：三宿主×通道降级矩阵全部命中**官方 URL**（claude.ai/install.ps1|sh、
    npm @anthropic-ai/claude-code）；无通道=None（不许编造镜像源）。
 3) 默认路径零执行：claude 缺失且未给 --install-agent 时，init 只打印指引、
    返回码不受影响（引擎=agent 无关，台账照常 ALL_PASS），_spawn_install 一次都没被调。
 4) 显式安装宁红不猜：--install-agent 时代执行一次（明示命令），装后复检找不到=FAIL 返回 1；
    装成=PASS 并提示 OAuth 登录。

跑法：python -I tests/cc_env_detect_test.py → 末行 CC_ENV_DETECT_PASS（all.sh 第五套）。
"""
import contextlib
import io
import os
import shutil
import stat
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ccards.oss_cli as oc  # noqa: E402

FAILS = []


def ok(name):
    print("PASS: %s" % name)


def bad(name, detail=""):
    FAILS.append(name)
    print("FAIL: %s%s" % (name, (" —— " + detail) if detail else ""))


def check(cond, name, detail=""):
    ok(name) if cond else bad(name, detail)


# ---------- 1) _cc_status ----------
empty = tempfile.mkdtemp(prefix="ccenv-empty-")
try:
    exe, ver = oc._cc_status(path=empty)
    check(exe is None and ver is None, "1.1 空 PATH=未检测到 claude")
finally:
    shutil.rmtree(empty, ignore_errors=True)

bindir = tempfile.mkdtemp(prefix="ccenv-bin-")
try:
    if sys.platform.startswith("win"):
        shim = os.path.join(bindir, "claude.bat")
        with open(shim, "w", encoding="utf-8") as f:
            f.write("@echo off\r\necho 9.9.9 (Claude Code)\r\n")
    else:
        shim = os.path.join(bindir, "claude")
        with open(shim, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\necho "9.9.9 (Claude Code)"\n')
        os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    exe, ver = oc._cc_status(path=bindir)
    check(exe is not None and (ver or "").startswith("9.9.9"), "1.2 伪 claude 可发现+版本行解析",
          "exe=%r ver=%r" % (exe, ver))
finally:
    shutil.rmtree(bindir, ignore_errors=True)

# ---------- 2) _cc_install_cmd 降级矩阵 ----------
def mkwhich(available):
    s = set(available)
    return lambda name, *a, **k: ("/usr/bin/" + name) if name in s else None

c = oc._cc_install_cmd(platform="win32", which=mkwhich(["powershell"]))
check(c and "install.ps1" in " ".join(c) and "claude.ai" in " ".join(c), "2.1 Windows=PowerShell 官方 irm")
c = oc._cc_install_cmd(platform="win32", which=mkwhich(["npm"]))
check(c and "claude-code" in " ".join(c) and c[0] == "npm", "2.2 Windows 无 powershell=退 npm 官方包")
c = oc._cc_install_cmd(platform="linux", which=mkwhich(["curl", "bash"]))
check(c and "install.sh" in " ".join(c) and "claude.ai" in " ".join(c), "2.3 POSIX=curl 官方 install.sh")
c = oc._cc_install_cmd(platform="linux", which=mkwhich(["wget", "bash"]))
check(c and "install.sh" in " ".join(c), "2.4 无 curl=退 wget 官方同址")
c = oc._cc_install_cmd(platform="linux", which=mkwhich(["npm"]))
check(c and c[0] == "npm", "2.5 无 curl/wget=退 npm")
c = oc._cc_install_cmd(platform="linux", which=mkwhich([]))
check(c is None, "2.6 无任何官方通道=None（禁编造源）")

# ---------- 3)/4) cmd_init 行为锁（打桩 _cc_status/_spawn_install，不碰网） ----------
class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, cmdv):
        self.calls.append(list(cmdv))
        return SimpleNamespace(returncode=0)


def run_init(status_ret, install_agent, install_cmd=["python", "-c", "pass"]):
    spy = Spy()
    orig = (oc._cc_status, oc._spawn_install, oc._cc_install_cmd)
    oc._cc_status = lambda path=None: status_ret
    oc._spawn_install = spy
    oc._cc_install_cmd = lambda platform=None, which=None: list(install_cmd) if install_cmd else None
    buf = io.StringIO()
    d = tempfile.mkdtemp(prefix="ccenv-init-")
    try:
        with contextlib.redirect_stdout(buf):
            rc = oc.cmd_init(SimpleNamespace(dir=d, force_config=False,
                                             install_agent=install_agent))
    finally:
        shutil.rmtree(d, ignore_errors=True)
        oc._cc_status, oc._spawn_install, oc._cc_install_cmd = orig
    return rc, buf.getvalue(), spy.calls

rc, out, calls = run_init((None, None), False)
check(rc == 0 and calls == [], "3.1 缺 agent 未显令=只指引零执行、init 不因此红",
      "rc=%s calls=%s" % (rc, calls))
check("--install-agent" in out and "claude.ai" in out, "3.2 指引含官方 URL 与 --install-agent 出口")

rc, out, calls = run_init((None, None), True)
check(len(calls) == 1, "4.1 --install-agent=明示后恰执行一次", "calls=%s" % calls)
check("显式执行官方安装" in out and " ".join(["python", "-c", "pass"]) in out,
      "4.2 执行前把完整命令打出来")
check(rc == 1 and "终端 agent Claude Code" in out, "4.3 装后复检找不到=FAIL（宁红不猜）", "rc=%s" % rc)

rc, out, calls = run_init(("C:/fake/claude.exe", "2.1.314 (Claude Code)"), False)
check(rc == 0 and calls == [] and "2.1.314" in out and "[warn]" not in out,
      "5.1 已装=PASS 行含版本号、无缺失告警", "rc=%s" % rc)

rc, out, calls = run_init((None, None), True, install_cmd=None)
check(rc == 1 and calls == [] and "没有可执行的官方安装通道" in out,
      "5.2 --install-agent 但无通道=红且不瞎跑", "rc=%s calls=%s" % (rc, calls))

print("CC_ENV_DETECT_PASS" if not FAILS else "CC_ENV_DETECT_FAIL %d" % len(FAILS))
sys.exit(0 if not FAILS else 1)
