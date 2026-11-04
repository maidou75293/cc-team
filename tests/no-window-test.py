# -*- coding: utf-8 -*-
"""黑窗口零锁（业主 2026-11-03「为什么弹出很多个黑窗口，能避免」→ PM 直做两刀之验收）。
两刀=① ops/ccards 全部 subprocess 派生点补 creationflags=NO_WIN；② ctrld install 任务动作钉 pythonw。
本锁只管①：行为级（真调函数、假探针抓 kwargs）+ AST 覆盖扫（新增派生点漏旗必红）。
红自证=临时树摘旗复扫必检出（守卫要验过它红得起来）。
跑法：python -I cc-team/tests/no-window-test.py"""
import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # cc-team/
OPS = os.path.join(ROOT, "ops")
EXPECTED = 0x08000000 if os.name == "nt" else 0
fails = []


def ck(cond, msg):
    print(("PASS: " if cond else "FAIL: ") + msg)
    if not cond:
        fails.append(msg)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Fake(object):
    """抓 kwargs 的探针替身；返回码/输出给无害值。"""

    def __init__(self, sink):
        self.sink = sink

    def __call__(self, argv, **kw):
        self.sink.update(kw)
        r = type("R", (), {})()
        r.returncode = 0
        if kw.get("text") or kw.get("universal_newlines"):
            r.stdout, r.stderr = "ok", ""      # 文本通道调用方按 str 处理
        else:
            r.stdout, r.stderr = b"ok", b""    # bytes 通道（config.git 有 .decode）
        return r


class FakeP(Fake):
    def __call__(self, argv, **kw):
        self.sink.update(kw)
        return type("P", (), {"pid": 4242, "returncode": None})()


# ---- NW0：常量定义与 EXPECTED 对齐（防有人把 NO_WIN 改成 0 蒙绿）----
ctrld = None
try:
    sys_path_home = tempfile.mkdtemp(prefix="cc-nw-home-")
    os.environ["CC_TEAM_HOME"] = sys_path_home
    cfg = load("nw_config", os.path.join(ROOT, "ccards", "config.py"))
    ck(getattr(cfg, "NO_WIN", None) == EXPECTED, "NW0 config.NO_WIN=%s 对齐期望 %s" % (getattr(cfg, "NO_WIN", None), hex(EXPECTED)))
    ctrld = load("nw_ctrld", os.path.join(OPS, "ctrld.py"))
    os.makedirs(os.path.join(sys_path_home, "logs"), exist_ok=True)
    dsp = load("nw_dispatch", os.path.join(OPS, "dispatch.py"))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    cmd = importlib.import_module("ccards.commands")  # 包内相对导入，不能按文件路径装载
except Exception as e:
    ck(False, "NW0 模块装载炸: %r" % e)
    sys.exit(1)

# ---- NW1：ctrld.run_ring（环拍派生=每 60s/300s 一闪的黑窗主源）----
# BUG-CTRLD-RINGSTALL-1 后 run_ring= Popen(+临时文件+wait 有界)；nt 支必带 creationflags。
# 探针抓 Popen kwargs；后续 wait 假件抛错走 RING-ERR 兜面=正常，旗已在调用瞬间入账。
try:
    real = subprocess.Popen
    sink = {}
    subprocess.Popen = FakeP(sink)
    try:
        ctrld.run_ring(sys_path_home, {"name": "nwtest", "argv": ["true"], "timeout": 5})
    finally:
        subprocess.Popen = real
    ck(sink.get("creationflags") == EXPECTED, "NW1 run_ring 传 creationflags=%s" % hex(EXPECTED))
except Exception as e:
    ck(False, "NW1 炸: %r" % e)

# ---- NW2：ctrld.board_spawn（看板子进程常驻窗）----
try:
    real = subprocess.Popen
    sink = {}
    subprocess.Popen = FakeP(sink)
    try:
        ctrld.board_spawn(sys_path_home)
    finally:
        subprocess.Popen = real
    ck(sink.get("creationflags") == EXPECTED, "NW2 board_spawn 传旗")
except Exception as e:
    ck(False, "NW2 炸: %r" % e)

# ---- NW3：dispatch.run_gate（done/claim 每次派生账校验的闪窗）----
try:
    real = subprocess.run
    sink = {}
    subprocess.run = Fake(sink)
    try:
        dsp.run_gate("show", "NW-NONEXIST")
    finally:
        subprocess.run = real
    ck(sink.get("creationflags") == EXPECTED, "NW3 run_gate 传旗")
except Exception as e:
    ck(False, "NW3 炸: %r" % e)

# ---- NW4：ccards.config.git（勾卡/验货 git 子进程）----
try:
    real = subprocess.run
    sink = {}
    subprocess.run = Fake(sink)
    try:
        cfg.git(ROOT, "rev-parse", "HEAD")
    finally:
        subprocess.run = real
    ck(sink.get("creationflags") == EXPECTED, "NW4 config.git 传旗")
except Exception as e:
    ck(False, "NW4 炸: %r" % e)

# ---- NW5：commands._run_verify（verdict 复跑判据=done 风暴时最密的闪窗源）----
try:
    real = subprocess.run
    sink = {}
    subprocess.run = Fake(sink)
    try:
        cmd._run_verify({"verify": "echo nw"})
    finally:
        subprocess.run = real
    ck(sink.get("creationflags") == EXPECTED, "NW5 _run_verify 传旗")
except SystemExit as e:
    ck(False, "NW5 die 出口（判据通道未达 run）: %s" % e)
except Exception as e:
    ck(False, "NW5 炸: %r" % e)

# ---- NW6：AST 覆盖扫——ops/ccards 每个 run/Popen 调用点必须带 creationflags ----
FUNCS = {"run", "Popen", "call", "check_output", "check_call"}


def scan_files(paths):
    bad = []
    for p in paths:
        try:
            tree = ast.parse(open(p, encoding="utf-8").read(), filename=p)
        except Exception as e:
            bad.append("%s 解析炸 %r" % (p, e))
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            hit = (isinstance(f, ast.Attribute) and f.attr in FUNCS
                   and isinstance(f.value, ast.Name) and f.value.id in ("subprocess", "_sp")) \
                or (isinstance(f, ast.Name) and f.id == "Popen"
                    and "Popen = subprocess.Popen" in open(p, encoding="utf-8").read())
            # 裸 run( 不追：那是各文件自己的业务同名函数（如 check_exit.run(ctx)），
            # 其真派生都写 subprocess.run( 已被上一分支覆盖；误伤=假阳=锁没人敢信。
            if not hit:
                continue
            kw = {k.arg for k in n.keywords}
            if "creationflags" not in kw and not any(getattr(k, "arg", None) is None for k in n.keywords):
                bad.append("%s:%d" % (os.path.relpath(p, ROOT), n.lineno))
    return bad


targets = [os.path.join(OPS, x) for x in sorted(os.listdir(OPS)) if x.endswith(".py")]
targets += [os.path.join(ROOT, "ccards", x) for x in
            ("commands.py", "config.py") if os.path.exists(os.path.join(ROOT, "ccards", x))]
# 15:2x 业主「跑 python 没必要弹黑窗」实锤补漏：board 批算环（status-web 每 8s 派生
# team-status/curl/PS + token-compare）当初不在扫射面内=风暴头凶，此后覆盖板同锁。
targets += [os.path.join(ROOT, "board", x) for x in sorted(os.listdir(os.path.join(ROOT, "board")))
            if x.endswith(".py")]
targets = [t for t in targets if os.path.basename(t) != "lane_registry.py" or True]
bad = scan_files(targets)
ck(not bad, "NW6 覆盖扫漏旗=%d %s" % (len(bad), bad[:4]))

# ---- NW7：红自证——摘旗变异必被 NW6 检出（摘掉它=本锁空枪）----
tmp = tempfile.mkdtemp(prefix="cc-nw-mut-")
try:
    src = os.path.join(OPS, "ctrld.py")
    mut = os.path.join(tmp, "ctrld.py")
    t = open(src, encoding="utf-8").read()
    assert "creationflags=NO_WIN" in t
    t2 = t.replace(", creationflags=NO_WIN", "", 1)
    open(mut, "w", encoding="utf-8").write(t2)
    got = scan_files([mut])
    ck(len(got) >= 1, "NW7 红能力自证：摘 1 旗变异被检出 %d 处 %s" % (len(got), got[:1]))
except Exception as e:
    ck(False, "NW7 变异自证炸: %r" % e)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(sys_path_home, ignore_errors=True)

print("=== no-window fails=%d ===" % len(fails))
print("NO_WINDOW_PASS" if not fails else "NO_WINDOW_RED")
sys.exit(0 if not fails else 1)
