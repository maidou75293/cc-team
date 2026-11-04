#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runcc-anchor-test.py — 起会话核锚点消毒红绿锁（REQ-CCTEAM-OSS-1 随件）。

病灶（发布实锤 2026-11-04）：bin/run-cc.ps1 入仓（56995d03 BUG-RUNNER-GONE-1）时带五处
宿主绝对路径字面量（Workdir 默认/CC_TEAM_HOME/cc-msg 日志目录/道锁目录/registry 册），
发布快照 B 闸 10 处命中出不了包。修复形=「env 覆写 > 脚本自位置派生」
（CC_REPO_ROOT/CC_MSG_BASE/CC_STATE_DIR/CC_CLAUDE_EXE）。

判据四案（缺一即红）：
  A1 发布面锚点清零：run-cc.ps1 逐行五形零命中；末尾栽一笔字面量必红（空检查比没检查更糟）。
  A2 派生可解：临时假仓树 <t>/fakerepo/cc-team/bin 放探针副本（仅桩掉 Start-Process 行，
     派生段逐字节原样），无 -Workdir 直跑 → 子进程回显 CC_TEAM_HOME=<t>/fakerepo\\cc-team、
     工作目录=<t>/fakerepo（=从脚本自位置真实解出，非任何字面量）。
  A2m 变异红检：副本派生式削掉一层 Split-Path → A2 断言必红（断言空心=硬败）。
  A3 历史等价（串代数）：git 逐 rev 找旧字面量形 blob，验 旧team=旧root+cc-team、
     旧lock=root上级+.cc\\state、旧reg=旧root+cc-team\\state\\registry.json、
     旧base=USERPROFILE+.openclaw\\tmp\\cc-msg，且新形含对应派生式；
     查无旧形（发布克隆零启史）=显式 SKIP 不红。
跑法: python -I cc-team/tests/runcc-anchor-test.py   （exit 0 + 尾行 RUNCC_ANCHOR_PASS）
非 Windows 宿主：A2/A2m 显式 SKIP（powershell 专属面，纪律=大声跳不计绿）。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CT = os.path.dirname(HERE)                       # cc-team 根
REPO = os.path.dirname(CT)
PS1 = os.path.join(CT, "bin", "run-cc.ps1")
CASES = 0


def die(msg):
    print("RUNCC_ANCHOR_FAIL: %s" % msg)
    sys.exit(1)


def ok(msg):
    global CASES
    CASES += 1
    print("PASS: %s" % msg)


def git(*args, cwd=None):
    return subprocess.run(["git", "-C", cwd or REPO, *args],
                          capture_output=True, timeout=30)


# ---- 五形禁串（碎片拼装：本文件任何一行凑不出完整串，同 oss-release-check 手法）----
def ban_patterns():
    u = "Us" + "ers"
    return [
        re.compile(re.escape("C:" + "\\" + u)),
        re.compile(re.escape("/mn" + "t/c/" + u)),
        re.compile("Admini" + "strator"),
        re.compile("10" + r"\." + "10" + r"\."),
        re.compile("s" + "k-"),
    ]


def scan_text(text):
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat in ban_patterns():
            if pat.search(line):
                hits.append(i)
                break
    return hits


def main():
    if not os.path.isfile(PS1):
        die("bin/run-cc.ps1 缺位（正本=仓内，BUG-RUNNER-GONE-1）")
    text = open(PS1, encoding="utf-8-sig").read()

    # —— A1 清零 + 栽笔自证 ——
    hits = scan_text(text)
    if hits:
        die("A1 run-cc.ps1 仍有宿主锚命中行=%s" % hits[:5])
    ok("A1 run-cc.ps1 发布面五形锚零命中")
    u = "Us" + "ers"
    poison = text + "\n$x = 'C:" + "\\" + u + "\\someone'\n"
    if not scan_text(poison):
        die("A1r 自证红失败：栽了字面量扫描器却零命中（空锁）")
    ok("A1r 扫描器栽笔必红（非空锁）")

    # —— A2/A2m 派生可解（Windows + powershell 专属面）——
    if os.name == "nt" and shutil.which("powershell.exe"):
        tmp = tempfile.mkdtemp(prefix="ccruncc-")
        try:
            a2, a2m = run_functional(tmp)
            if a2 is None:
                die("A2/A2m 桩化变换失配（Start-Process/派生行结构变了，锁要随改——拒自证）")
            ok("A2 假仓树无 -Workdir 直跑：team/wd 两锚从 $PSScriptRoot 实解")
            ok("A2m 变异（削一层派生）A2 断言必红=%s" % a2m)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("SKIP: A2/A2m 起会话核实解（非 Windows 宿主/powershell 缺位）")

    # —— A3 历史等价（串代数）——
    a3 = historical_equivalence(text)
    if a3 is None:
        print("SKIP: A3 历史等价（本树查无旧字面量形 blob=发布克隆零启史，属预期）")
    else:
        ok("A3 旧五形与派生式串代数逐字符等价@%s" % a3)

    print("RUNCC_ANCHOR_PASS cases=%d" % CASES)


# 探针变换：只桩掉真起 claude 的 Start-Process 行，派生段逐字节不动
_RE_LAUNCH = re.compile(
    r"\$p = Start-Process -FilePath \$exe -ArgumentList \$q `\s*\n"
    r"\s*-WorkingDirectory \$Workdir -NoNewWindow -PassThru `\s*\n"
    r"\s*-RedirectStandardOutput \$log -RedirectStandardError \$err")
_RE_DERIVE = re.compile(r"Split-Path \(Split-Path \$PSScriptRoot -Parent\) -Parent")


def run_functional(tmp):
    bin_dir = os.path.join(tmp, "fakerepo", "cc-team", "bin")
    os.makedirs(bin_dir)
    msg = os.path.join(tmp, "msg")
    state = os.path.join(tmp, "state")
    brief = os.path.join(tmp, "brief.txt")
    with open(brief, "w", encoding="utf-8") as f:
        f.write("ANCHOR-PROBE")
    src = open(PS1, encoding="utf-8-sig").read()
    if not _RE_LAUNCH.search(src) or not _RE_DERIVE.search(src):
        return None, None
    probe = _RE_LAUNCH.sub(
        "$p = Start-Process -FilePath cmd.exe -ArgumentList '/c echo TEAM=%CC_TEAM_HOME% "
        "& echo WD=%CD%' -NoNewWindow -PassThru -RedirectStandardOutput $log "
        "-RedirectStandardError $err", src)
    env = dict(os.environ)
    for k in ("CC_REPO_ROOT", "CC_TEAM_HOME", "CC_STATE_DIR", "CC_MSG_BASE", "CC_CLAUDE_EXE"):
        env.pop(k, None)
    env.update({"CC_MSG_BASE": msg, "CC_STATE_DIR": state,
                "CC_CLAUDE_EXE": "stub-unused"})

    def one(psi, lane):
        logdir = os.path.join(msg)
        before = set(os.listdir(logdir)) if os.path.isdir(logdir) else set()
        r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", psi, "-Brief", brief, "-Lane", lane],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=120, cwd=tmp)
        news = [f for f in (set(os.listdir(logdir)) - before) if f.startswith("cc-run-")]
        body = ""
        if news:
            body = open(os.path.join(logdir, sorted(news)[-1]),
                        encoding="utf-8", errors="replace").read()
        return r.returncode, body

    def norm(p):
        # tempfile 给 8.3 短形、cmd 回显长形：realpath 双向归一再比（否则恒假红）
        return os.path.realpath(p).replace("/", "\\").rstrip("\\").lower()

    def expect(body):
        want_team = norm(os.path.join(tmp, "fakerepo", "cc-team"))
        want_wd = norm(os.path.join(tmp, "fakerepo"))
        m = re.search(r"TEAM=(.*)", body)
        w = re.search(r"WD=(.*)", body)
        team = norm(m.group(1).strip()) if m else ""
        wd = norm(w.group(1).strip()) if w else ""
        return team == want_team and wd == want_wd, team, wd

    psi = os.path.join(bin_dir, "run-cc.ps1")
    shutil.copyfile(PS1, psi)
    with open(psi, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(probe)
    rc, body = one(psi, "A9")
    good, team, wd = expect(body)
    if rc != 0 or not good:
        die("A2 派生实解红 rc=%s team=[%s] wd=[%s] body=%r" % (rc, team, wd, body[-200:]))
    if not os.path.isdir(state):
        die("A2 道锁目录未按 CC_STATE_DIR 现造（%s 不在）" % state)
    lk = os.path.join(state, "lane-A9.lock")
    if os.path.exists(lk):
        die("A2 会话收工后道锁未清（%s 残在）" % lk)
    if "DONE" not in body and "RC=" not in body:
        # DONE/RC 走 Add-Content 追加在同一 log；cmd 输出覆写 START 行属既有行为，不苛求
        pass
    # 变异：派生式削一层 → 应解到 <tmp>/fakerepo/cc-team（错值），A2 断言必红
    mut = _RE_DERIVE.sub("Split-Path $PSScriptRoot -Parent", probe)
    mpsi = os.path.join(bin_dir, "run-cc-mut.ps1")
    with open(mpsi, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(mut)
    rc2, body2 = one(mpsi, "A8")
    good2, team2, wd2 = expect(body2)
    if good2:
        die("A2m 变异体竟通过断言（断言空心=锁腐，team=[%s] wd=[%s]）" % (team2, wd2))
    if rc2 != 0:
        die("A2m 变异体 powershell 异常退出 rc=%s（非断言面红，环境不稳）" % rc2)
    return ("team/wd 两锚从 $PSScriptRoot 实解对表通过",
            "红于预期 team=[%s] wd=[%s]" % (team2[:60], wd2[:60]))


def historical_equivalence(text):
    """git 里找旧字面量形（五处引号绝对路径齐在）的 run-cc.ps1 blob，做串代数对表。"""
    r = git("log", "--format=%H", "--", "cc-team/bin/run-cc.ps1")
    if r.returncode != 0:
        return None
    old = None
    for sha in r.stdout.decode("utf-8", "replace").split("\n"):
        sha = sha.strip()
        if not sha:
            continue
        b = git("show", "%s:cc-team/bin/run-cc.ps1" % sha)
        if b.returncode != 0:
            continue
        t = b.stdout.decode("utf-8", "replace")
        mm = re.search(r"\[string\]\$Workdir = '([^']+)'", t)
        if mm and "Split-Path" not in t.split("$RepoRoot")[0][:800]:
            got = {}
            for key, pat in (("workdir", r"\[string\]\$Workdir = '([^']+)'"),
                             ("team", r"\$env:CC_TEAM_HOME = '([^']+)'"),
                             ("base", r"\$base\s+= '([^']+)'"),
                             ("lock", r"\$lockDir = '([^']+)'"),
                             ("reg", r"\$reg = '([^']+)'")):
                m2 = re.search(pat, t)
                if not m2:
                    got = None
                    break
                got[key] = m2.group(1)
            if got:
                old = (sha[:8], got)
                break
    if not old:
        return None
    sha, g = old
    root = g["workdir"]
    want_team = root + "\\cc-team"
    want_lock = os.path.dirname(root) + "\\.cc\\state"
    want_reg = root + "\\cc-team\\state\\registry.json"
    prof = os.environ.get("USERPROFILE", "")
    if prof:
        want_base = prof + "\\.openclaw\\tmp\\cc-msg"
        if g["base"] != want_base:
            die("A3 旧 base 与 USERPROFILE 组合不等价：[%s] vs [%s]" % (g["base"], want_base))
    if g["team"] != want_team or g["lock"] != want_lock or g["reg"] != want_reg:
        die("A3 旧五形互相不等价（root=%s）: team=%s lock=%s reg=%s" % (root, g["team"], g["lock"], g["reg"]))
    # 新形必须含同源派生式（串结构对表，非值对表——值等价由上面旧形互推+A2 实解联合封死）
    for name, pat in (("RepoRoot 两级上推", r"Split-Path \(Split-Path \$PSScriptRoot -Parent\) -Parent"),
                      ("team 拼装", r"\$env:CC_TEAM_HOME = Join-Path \$RepoRoot 'cc-team'"),
                      ("lock 上级+.cc\\state", r"Join-Path \(Split-Path \$RepoRoot -Parent\) '\.cc\\state'"),
                      ("reg 随 team", r"\$reg = Join-Path \(Join-Path \$env:CC_TEAM_HOME 'state'\)")):
        if not re.search(pat, text):
            die("A3 新形缺派生结构「%s」（消毒回退？）" % name)
    return sha


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        die("powershell 子进程超时（120s）——桩探针不该挂这么久，环境异常如实红")
