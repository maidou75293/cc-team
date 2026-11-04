# -*- coding: utf-8 -*-
"""判据通道剥壳锁（2026-11-03 黑窗收口续刀）：_run_verify 对 `bash -c '...'` 套娃卡面
自动剥外层，杜绝双层派生=假闸 rc2 + done-sweep 空转闪窗（HYGIENE 案）。红自证=临时树
摘剥壳段必红。跑法：python -I cc-team/tests/verify-unwrap-test.py"""
import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
fails = []


def ck(cond, msg):
    print(("PASS: " if cond else "FAIL: ") + msg)
    if not cond:
        fails.append(msg)


def run_verify_capture(card, mod=None):
    """真调 _run_verify，抓 subprocess.run 的 argv（不外跑）。"""
    home = tempfile.mkdtemp(prefix="cc-uw-")
    os.environ["CC_TEAM_HOME"] = home
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import ccards.commands as C
    if mod is not None:
        C = mod
    captured = {}

    class R(object):
        returncode = 0
        stdout = b"ok"

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return R()

    real = subprocess.run
    subprocess.run = fake_run
    res = {}
    try:
        res["v"] = C._run_verify(dict(card))
    finally:
        subprocess.run = real
    return captured.get("argv"), res.get("v")


try:
    # V1 套娃卡面=剥外层，内层原样进通道
    argv, _ = run_verify_capture({"verify": "bash -c 'echo hi'"})
    ck(argv and argv[2] == "echo hi" and argv[1] == "-c",
       "V1 双层串剥壳 → 内层原样 %r" % (argv[2:] if argv else None))
    # V2 普通串不误伤（非 bash -c 头形逐字不动）
    plain = "case \"$(uname -s)\" in MINGW*) ;; esac; python3 x.py"
    argv2, _ = run_verify_capture({"verify": plain})
    ck(argv2 and argv2[2] == plain, "V2 非套娃串零改动")
    # V3 多段 bash -c（-c 后带多参/管道头形）不硬剥=宁不剥不猜
    odd = "bash -c echo hi extra"
    argv3, _ = run_verify_capture({"verify": odd})
    ck(argv3 and argv3[2] == odd, "V3 异形 bash 头串不剥（len!=3 保守）")
    # V4 真病灶卡面在册且剥得动：BUG-DEMO-DS-HYGIENE-1
    try:
        g = json.load(open(os.path.join(ROOT, "state", "graph.json"), encoding="utf-8"))
        hv = (g.get("cards") or g).get("BUG-DEMO-DS-HYGIENE-1", {}).get("verify", "")
        import shlex
        parts = shlex.split(hv, posix=True) if hv else []
        ck(len(parts) == 3 and parts[0] == "bash" and parts[1] == "-c" and parts[2] != hv,
           "V4 HYGIENE 真卡面属套娃形且可剥（通道现会自动收敛）")
    except (KeyError, ValueError) as e:
        ck(False, "V4 真卡面解析异常: %r" % e)
    # V5 红自证：摘剥壳段的副本必回归双层原样（=锁有牙齿）
    src = os.path.join(ROOT, "ccards", "commands.py")
    t = open(src, encoding="utf-8").read()
    ck("import shlex" in t and "_parts[2]" in t, "V5a 剥壳段在位")
    tree = ast.parse(t)
    seg = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Attribute)
           and n.func.attr == "split" and isinstance(n.func.value, ast.Name)
           and n.func.value.id == "shlex"]
    ck(len(seg) >= 1, "V5b 剥壳点 shlex.split 调用可定位=%d（摘除实验基座在）" % len(seg))
    # V5c 红真打：摘除剥壳段的副本=套娃原样保留（守卫要验过它红得起来）
    import re
    import shutil
    import importlib
    tmpd = tempfile.mkdtemp(prefix="cc-uw-mut-")
    shutil.copytree(os.path.join(ROOT, "ccards"), os.path.join(tmpd, "ccards"))
    p2 = os.path.join(tmpd, "ccards", "commands.py")
    s2 = open(p2, encoding="utf-8").read()
    s3 = re.sub(r"    cmd = _unwrap_verify\(cmd\)\n", "", s2)
    ck(s3 != s2, "V5c0 摘除文本形命中（否则=摘除实验没做成）")
    open(p2, "w", encoding="utf-8").write(s3)
    for _m in ("ccards", "ccards.commands"):
        sys.modules.pop(_m, None)
    sys.path.insert(0, tmpd)
    try:
        C2 = importlib.import_module("ccards.commands")
        argvX, _ = run_verify_capture({"verify": "bash -c 'echo hi'"}, mod=C2)
        ck(argvX and argvX[2].startswith("bash -c"), "V5c 摘壳副本套娃原样=红可打")
    finally:
        sys.path.remove(tmpd)
        for _m in ("ccards", "ccards.commands"):
            sys.modules.pop(_m, None)
        sys.modules.pop("ccards.config", None)
        importlib.import_module("ccards.commands")  # 归位正版
    C = importlib.import_module("ccards.commands")  # V6 用正版模块句柄（V5c 已归位）
    # V6 比对侧同源（BUG-VERDICT-SHA-UNWRAP-1，FUSE-S5 班 16:2x 实锤）：15:48 剥壳补丁
    # (351b8458) 只改执行侧哈希，cmd_verdict 锁窗比对仍按原始卡面文本哈希——套娃形卡面
    # 两侧 sha 恒不等 → 一切 `bash -c '...'` 卡 verdict PASS 死锁（HYGIENE 三连拒、文本
    # 未变仍报「判据在执行窗内被改」）。规矩=剥壳派生必须单一函数、双侧同源。
    g6 = json.load(open(os.path.join(ROOT, "state", "graph.json"), encoding="utf-8"))
    hv6 = (g6.get("cards") or g6).get("BUG-DEMO-DS-HYGIENE-1", {}).get("verify", "")
    ck(hasattr(C, "_unwrap_verify"),
       "V6a 剥壳派生收敛为单一函数 _unwrap_verify（缺失=双侧各算各的，死锁复发）")
    if hasattr(C, "_unwrap_verify"):
        _m6 = re.search(r"def cmd_verdict\b.*?(?=\ndef [a-zA-Z_])", t, re.S)
        ck(bool(_m6) and "sha1(_unwrap_verify(" in _m6.group(0),
           "V6b cmd_verdict 锁窗 sha 接同源函数（比对侧仍原始文本哈希=套娃卡永不转正）")
        ck(C._unwrap_verify(hv6) != hv6.strip(),
           "V6c 真病灶卡面确属套娃形（同源断言非空转前提）")
        _, res6 = run_verify_capture({"verify": hv6})
        want = hashlib.sha1(C._unwrap_verify(hv6).encode("utf-8")).hexdigest()[:12]
        ck(res6 and res6.get("cmd_sha") == want,
           "V6d 执行侧 cmd_sha 与同源派生一致（落章比对同尺=PASS 可达）")
finally:
    pass

print("=== verify-unwrap fails=%d ===" % len(fails))
print("UNWRAP_PASS" if not fails else "UNWRAP_RED")
sys.exit(0 if not fails else 1)
