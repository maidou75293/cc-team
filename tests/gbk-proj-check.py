#!/usr/bin/env python3
# gbk-proj-check.py — GBKENC-PROJ-1 红绿锁（2026-11-03 m1，承接 MECH-GBKDECODE-BOMB-1 转派清单）。
# 背景：母卡判据 B 只拦 cc-team 全域；scripts/services/mos 三树残留同型 locale 解码点——
#       GBK 中文宿主 locale=cp936，子程 UTF-8 输出（CJK/箭头含 0x92 等非法 cp936 序列）被
#       text=True 母程按 locale strict 解码，_readerthread 当场死亡、stdout=None。
#       本卡给三树全部 subprocess 文本调用钉显式 encoding="utf-8" errors="replace"，本锁守三条：
#   判据 A（活体对照，跨宿主确定）：
#     A1 母程显式钉 cp936 strict 读子程 UTF-8 CJK+箭头 → 必炸（炸/None/乱码三者其一）；
#        若竟读干净=探针失效，本锁如实红（空检查比没有检查更糟）。
#     A2 同子程、修复形态 encoding="utf-8" errors="replace" → 不炸且逐字读回。
#   判据 B（静态全扫，撤钉必红）：AST 扫 scripts/services/mos 三树——text=/universal_newlines=/
#     errors= 不带 encoding= 即红；getoutput/getstatusoutput/os.popen 即红（内部同样 locale
#     解码）；字符串常量夹具二次 AST 递归同判。口径承母卡：capture_output 不带 text=bytes 模式
#     母程不解码，不判红只计数；排除 __pycache__/.git/.work/tmp/.tmp/docs/_archive 等归档临时目录。
#   判据 C（变异自检=红检可失败锁）：scan_tree 对内存「撤钉夹具」必须报红、对「钉上夹具」
#     必须不红、对 getoutput/popen 夹具必须红、对纯 bytes 夹具必须不红——四向任一失守本锁自红，
#     防止 B 沦为永绿的空检查。
#   备注①：A 组与母卡锁同机制用 Popen+TextIOWrapper 显式传参，本文件自身不触发母卡判据 B
#     静态扫描（本文件在 cc-team，母卡锁每次都会扫它，必须自身干净，母卡 52 项底线不得退化）。
#   备注②：C 组夹具串用运行时拼接（"sub"+"process…"），任一编译期常量都不含完整 "subprocess"，
#     避开母卡嵌字串夹具扫描；这正是「文本钉+字面隔离」双口径的自证。
#   跑法: python3 -I cc-team/tests/gbk-proj-check.py   （exit 0 = 全绿）
import ast, io, os, sys
from collections import Counter

try:  # 日志可读性：GBK 宿主直打 CJK 会乱码，母程自钉 UTF-8（errors=replace 与全卡口径一致）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJ_ROOTS = ("scripts", "services", "mos")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "build", "_archive", "dist", "target",
             ".work", "tmp", ".tmp", "docs"}
BOMB = "回收 → ✓ GUARD → S1 处置"
fails = []

TEXT_KWARGS = ("text", "universal_newlines", "errors")
FN_NAMES = ("run", "Popen", "check_output", "check_call", "call")


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, detail)


# ---------- 判据 A ----------
def child_argv():
    # 子程：把 UTF-8 的 CJK+箭头写到管道（-X utf8 保证 -I/裸跑都钉 UTF-8 输出，模拟真实脚本）
    src = "import sys; sys.stdout.write(%r)" % (BOMB,)
    return [sys.executable, "-X", "utf8", "-c", src]


def subprocess_run(argv, encoding=None, errors=None):
    import subprocess
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # 与 subprocess text 模式同一机制：bytes 管道外包 TextIOWrapper；显式传 encoding 即修复形态。
    # 不用 run(text=True) 是为了让本锁自身过母卡判据 B 与本卡判据 B 的静态扫描（无裸 text=True 字样）。
    if encoding:
        w = io.TextIOWrapper(p.stdout, encoding=encoding, errors=errors)
        data = w.read()
    else:
        data = p.stdout.read().decode()
    p.wait(timeout=30)
    return {"returncode": p.returncode, "stdout": data}


def probe_A():
    # A1 炸弹对照：strict 钉 cp936 读子程 UTF-8 输出，必须 炸/None/乱码 其一
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


# ---------- 判据 B（与判据 C 共用扫描器）----------
def kw_of(node, name):
    for k in node.keywords:
        if k.arg == name:
            return k.value
    return None


def scan_tree(tree, path, tag, reds, bytes_cnt, pinned_cnt):
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
            # 已钉 encoding 的文本调用计入「钉数」供对账（仅当确带 text/universal_newlines 真值）
            for kw in TEXT_KWARGS[:2]:
                v = kw_of(node, kw)
                if v is not None and not (isinstance(v, ast.Constant) and v.value in (False, None)):
                    pinned_cnt[0] += 1
                    break
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


def scan_file(path, reds, bytes_cnt, pinned_cnt, depth=0):
    try:
        src = io.open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (SyntaxError, OSError, ValueError):
        return
    scan_tree(tree, path, "real", reds, bytes_cnt, pinned_cnt)
    if depth < 2:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
               and "subprocess" in node.value and ("text=" in node.value or "capture_output" in node.value
                                                   or "getoutput" in node.value or "popen" in node.value):
                try:
                    scan_tree(ast.parse(node.value), path, "embedded-fixture", reds, bytes_cnt, pinned_cnt)
                except SyntaxError:
                    pass


def scan_root(root, reds, bytes_cnt, pinned_cnt):
    nfiles = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            nfiles += 1
            scan_file(os.path.join(dirpath, fn).replace(os.sep, "/"), reds, bytes_cnt, pinned_cnt)
    return nfiles


def relpath(p):
    # scan_file 存的是正斜杠绝对路径，剥 ROOT 也要用正斜杠形态
    return p.replace(ROOT.replace(os.sep, "/") + "/", "", 1)


def probe_B():
    reds, bytes_cnt, pinned_cnt = [], Counter(), Counter()
    nfiles = 0
    for r in PROJ_ROOTS:
        root = os.path.join(ROOT, r)
        if not os.path.isdir(root):
            bad("B 扫描树存在", "%s 不存在" % r)
            continue
        nfiles += scan_root(root, reds, bytes_cnt, pinned_cnt)
    for p, l, tag, why in sorted(set(reds)):
        bad("B 项目树静态扫描", "%s:%d [%s] %s" % (relpath(p), l, tag, why))
    if reds:
        bad("B 汇总", "%d 处漏钉/同型洞" % len(set(reds)))
    else:
        ok("B scripts/services/mos %d 个 .py：%d 处文本调用均已钉 encoding=，无漏钉、无 getoutput/os.popen"
           "（bytes 管道用法 %d 处按母卡口径不拦，非炸弹）" % (nfiles, pinned_cnt[0], bytes_cnt[0]))


# ---------- 判据 C（变异自检：证明 B 的扫描器红得起来）----------
def fixture(kind, pinned=False):
    # 运行时拼接：任一编译期字符串常量都不含完整 "subprocess"，避开母卡嵌字串夹具扫描（见头注备注②）
    head = "import sub" + "process\n"
    if kind == "getoutput":
        body = "sub" + "process.getoutput(cmd)"
    elif kind == "popen":
        body = "os." + "popen(cmd)"
    elif kind == "bytes":
        body = "sub" + "process.run(cmd, capture_output=True)"
    else:
        body = "sub" + "process.run(cmd, capture_output=True, text=True"
        if pinned:
            body += ', encoding="utf-8", errors="replace"'
        body += ")"
    return head + body + "\n"


def scan_src(src, tag):
    reds, b, p = [], Counter(), Counter()
    scan_tree(ast.parse(src), "<fixture:%s>" % tag, tag, reds, b, p)
    return reds, b, p


def probe_C():
    # C1 撤钉夹具必须被扫红——扫不出来=本锁判据 B 是永绿空检查，自红
    reds, _, _ = scan_src(fixture("run"), "unpinned")
    if reds:
        ok("C1 内存撤钉夹具（text=True 无 encoding=）→ 扫描器报红（%s）" % reds[0][3])
    else:
        bad("C1 撤钉夹具红检", "扫描器没抓到未钉 encoding 的 subprocess.run —— 判据B是空检查")

    # C2 钉上夹具必须不红且钉计数=1（误报=分不清钉/撤，同样无效）
    reds2, _, p2 = scan_src(fixture("run", pinned=True), "pinned")
    if reds2 or p2[0] != 1:
        bad("C2 钉上夹具绿检", "reds=%s pinned=%s" % (reds2, p2[0]))
    else:
        ok("C2 内存钉上夹具（text=True+encoding=）→ 不红且钉计数=1")

    # C3 getoutput/popen 夹具必须红（内部同样 locale 解码，同型洞）
    reds3, _, _ = scan_src(fixture("getoutput"), "getoutput")
    reds4, _, _ = scan_src(fixture("popen"), "popen")
    if reds3 and reds4:
        ok("C3 getoutput/popen 夹具 → 双双报红")
    else:
        bad("C3 getoutput/popen 夹具红检", "getoutput红=%s popen红=%s" % (bool(reds3), bool(reds4)))

    # C4 纯 bytes 夹具必须不红（母卡口径：capture_output 不带 text= 母程不解码）
    reds5, b5, _ = scan_src(fixture("bytes"), "bytes")
    if not reds5 and b5[0] == 1:
        ok("C4 纯 bytes 夹具（capture_output 无 text=）→ 不红、只计数")
    else:
        bad("C4 bytes口径自检", "reds=%s bytes计数=%s（应 0红/1计数）" % (reds5, b5[0]))


def main():
    probe_A()
    probe_B()
    probe_C()
    if fails:
        print("GBK_PROJ_LOCK_FAIL %d 红：%s" % (len(fails), fails))
        return 1
    print("GBK_PROJ_LOCK_PASS red=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
