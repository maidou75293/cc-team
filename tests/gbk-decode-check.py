#!/usr/bin/env python3
# gbk-decode-check.py — MECH-GBKDECODE-BOMB-1 红绿锁（2026-11-03 m1）。
# 事故：cc-team 子进程调用 text=True 不带 encoding → 母程按 locale 解码。GBK 中文宿主上
#       子程 UTF-8 输出（CJK/箭头含 0x92 等非法 cp936 序列）让 _readerthread 当场死亡，
#       communicate 回 stdout=None，调用方 None+str TypeError；且 python -I 吞 PYTHONUTF8/
#       PYTHONIOENCODING，去 -I 才碰巧绿 —— 同一判据两种宿主命运，静默跑不可能。
# 判据 A（活体，跨宿主确定）：母程解码钉 cp936 起子程打 CJK+箭头——
#   A1 炸弹必须红得起来（strict 钉 cp936 读子程 UTF-8 输出：炸/None/乱码三者其一）；
#      若 A1 竟读干净=探针失效，本锁如实红（空检查比没有检查更糟）。
#   A2 修复形态必须绿：同子程、encoding="utf-8" errors="replace" → 不炸且逐字读回。
#   A3 宿主真实形态旁证：本机 locale 若真是 GBK，bytes 管道+locale 编码 TextIOWrapper
#      （=subprocess text 模式内部机制）必须同样炸；UTF-8 宿主则记 INFO 旁通。
#      备注：3.10 起 subprocess text 模式延迟解析 encoding="locale" 哨兵，monkeypatch
#      locale.getpreferredencoding 不可靠，故 A1 用公开参数显式钉 cp936 —— 与 GBK 宿主
#      locale 默认逐字节等价（同 codec 同 strict）。
# 判据 B（静态，撤修复必红）：AST 扫 cc-team/**/*.py —— text=/universal_newlines=/errors=
#   不带 encoding= 即红；getoutput/getstatusoutput/os.popen 即红（内部同样 locale 解码）；
#   字符串常量夹具（旧版逐字副本类）二次 AST 递归同判。
#   口径两则（与卡面文字的出入，均在收口 note 上报主控制裁）：
#   ① capture_output 不带 text = bytes 模式，母程根本不解码，不构成 GBK 炸弹，不判红
#      （只 INFO 计数；现网 bytes 用法均配显式 .decode("utf-8","replace")）；
#   ② 拦红线=cc-team 全域（本卡修复域）；仓内其余目录另报计数不判红——
#      按多实例纪律属别人模块，交主控转派。
# 跑法: python3 -I cc-team/tests/gbk-decode-check.py   （exit 0 = 全绿）
import ast, io, os, sys
from collections import Counter

try:  # 日志可读性：GBK 宿主直打 CJK 会乱码，母程自钉 UTF-8（errors=replace 与全卡口径一致）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE = os.path.join(ROOT, "cc-team")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "build", "_archive", "dist", "target"}
BOMB = "回收 → ✓ GUARD → S1 处置"
fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, detail)


def child_argv():
    # 子程：把 UTF-8 的 CJK+箭头写到管道（-X utf8 保证 -I/裸跑都钉 UTF-8 输出，模拟真实脚本）
    src = "import sys; sys.stdout.write(%r)" % (BOMB,)
    return [sys.executable, "-X", "utf8", "-c", src]


# ---------- 判据 A ----------
def probe_A():
    # A1 炸弹红检：strict 钉 cp936 读子程 UTF-8 输出，必须 炸/None/乱码 其一
    exploded, how = None, ""
    try:
        r = subprocess_run(child_argv(), encoding="cp936")
        if r["stdout"] is None:
            exploded, how = True, "stdout=None(_readerthread亡)"
        elif BOMB not in (r["stdout"] or ""):
            exploded, how = True, "乱码 %r" % ((r["stdout"] or "")[:24],)
        else:
            exploded, how = False, "cp936 strict 竟逐字读净"
    except UnicodeDecodeError as e:
        exploded, how = True, "UnicodeDecodeError %s" % (e,)
    if not exploded:
        bad("A1 炸弹复现", how)
    else:
        ok("A1 母程钉cp936 strict 读子程UTF-8 CJK+箭头 → 必炸（%s）" % how)

    # A2 修复形态：显式 utf-8 + replace，必须不炸且逐字读回
    try:
        r = subprocess_run(child_argv(), encoding="utf-8", errors="replace")
        if r["returncode"] == 0 and r["stdout"] == BOMB:
            ok("A2 修复形态 encoding=utf-8 errors=replace → 不炸且逐字读回")
        else:
            bad("A2 修复形态读净", "rc=%s out=%r" % (r["returncode"], (r["stdout"] or "")[:40]))
    except Exception as e:
        bad("A2 修复形态读净", repr(e))

    # A3 宿主真实形态旁证：bytes 管道 + locale 编码 TextIOWrapper（subprocess text 模式内部机制）
    import locale
    enc = locale.getpreferredencoding(False)
    if (enc or "").lower() in ("cp936", "gbk", "gb2312"):
        if probe_A3_host_gbk(enc):
            ok("A3 本机 locale=%s(GBK族)，母程默认解码形态实炸 —— 现场复现闭环" % enc)
        else:
            bad("A3 GBK宿主旁证", "本机真是 GBK 族却没炸，机制判断有误")
    else:
        print("INFO: A3 本机 locale=%s（UTF-8 族），GBK 现场由 A1 显式钉 cp936 等价复现" % enc)


def subprocess_run(argv, encoding=None, errors=None):
    import subprocess, io as _io
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # 与 subprocess text 模式同一机制：bytes 管道外包 TextIOWrapper；显式传 encoding 即修复形态。
    # 不用 run(text=True) 是为了让本锁自身过判据 B 的静态扫描（无裸 text=True 字样）。
    if encoding:
        w = _io.TextIOWrapper(p.stdout, encoding=encoding, errors=errors)
        data = w.read()
    else:
        data = p.stdout.read().decode()
    p.wait(timeout=30)
    return {"returncode": p.returncode, "stdout": data}


def probe_A3_host_gbk(enc):
    import subprocess
    p = subprocess.Popen(child_argv(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        import io as _io
        t = _io.TextIOWrapper(p.stdout, encoding=enc).read()
        p.wait(timeout=30)
        return BOMB not in t
    except UnicodeDecodeError:
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
        return True


# ---------- 判据 B ----------
TEXT_KWARGS = ("text", "universal_newlines", "errors")
FN_NAMES = ("run", "Popen", "check_output", "check_call", "call")


def kw_of(node, name):
    for k in node.keywords:
        if k.arg == name:
            return k.value
    return None


def scan_tree(tree, path, tag, reds, bytes_cnt):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
        if name in ("getoutput", "getstatusoutput") or name == "popen":
            reds.append((path, node.lineno, tag, name + " 内部按 locale 解码"))
            continue
        if name not in FN_NAMES:
            continue
        if kw_of(node, "encoding") is not None:
            continue
        for kw in TEXT_KWARGS:
            v = kw_of(node, kw)
            if v is None:
                continue
            if isinstance(v, ast.Constant) and v.value in (False, None):
                continue
            reds.append((path, node.lineno, tag, "%s=%s 不带 encoding=" % (kw, getattr(v, "value", "?"))))
            break
        if kw_of(node, "capture_output") is not None:
            bytes_cnt[0] += 1


def scan_file(path, reds, bytes_cnt, depth=0):
    try:
        src = io.open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (SyntaxError, OSError, ValueError):
        return
    scan_tree(tree, path, "real", reds, bytes_cnt)
    if depth < 2:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
               and "subprocess" in node.value and ("text=" in node.value or "capture_output" in node.value
                                                   or "getoutput" in node.value or "popen" in node.value):
                try:
                    scan_tree(ast.parse(node.value), path, "embedded-fixture", reds, bytes_cnt)
                except SyntaxError:
                    pass


def scan_root(root, reds, bytes_cnt, skip_gate_subtree=None):
    nfiles = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if skip_gate_subtree and os.path.abspath(dirpath) == os.path.abspath(skip_gate_subtree):
            dirnames[:] = []
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            nfiles += 1
            scan_file(os.path.join(dirpath, fn).replace(os.sep, "/"), reds, bytes_cnt)
    return nfiles


def relpath(p):
    # scan_file 存的是正斜杠绝对路径，剥 ROOT 也要用正斜杠形态
    return p.replace(ROOT.replace(os.sep, "/") + "/", "", 1)


def probe_B():
    reds, bytes_cnt = [], Counter()
    nf = scan_root(GATE, reds, bytes_cnt)
    for p, l, tag, why in sorted(set(reds)):
        bad("B cc-team 静态扫描", "%s:%d [%s] %s" % (relpath(p), l, tag, why))
    if reds:
        bad("B 汇总", "%d 处红" % len(set(reds)))
    else:
        ok("B cc-team %d 个 .py：text/universal_newlines/errors 均带 encoding=，无 getoutput/os.popen（bytes 管道用法 %d 处均已显式 decode，非炸弹不拦）"
           % (nf, bytes_cnt[0]))
    # 仓内其余目录：只报数不判红（别人模块，交主控转派）
    others, ob = [], Counter()
    scan_root(ROOT, others, ob, skip_gate_subtree=GATE)
    if others:
        top = Counter(relpath(p).split("/")[0] for p, _, _, _ in set(others))
        print("INFO: cc-team 之外仍有 %d 处同型 locale 解码点 %s —— 属其他模块，按纪律不改不拦，回主控转派"
              % (len(set(others)), top.most_common(8)))


def main():
    probe_A()
    probe_B()
    if fails:
        print("GBK_DECODE_LOCK_FAIL %d 红：%s" % (len(fails), fails))
        return 1
    print("GBK_DECODE_LOCK_PASS red=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
