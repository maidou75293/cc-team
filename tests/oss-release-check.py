#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oss-release-check.py — 发布包常驻锁（REQ-OSS-ENGINE-1，业主 seq=20/21 验收判据）。

三段判据（缺一即红，末行单行裁决，卡面完成判据 grep 此行）：
  A 敏感清零：发布集（ccards/ ops/ tests/ cards_dag.py README.md pyproject.toml LICENSE）
    五形实扫零命中 + 栽字面量红自证（沙箱副本植一笔必红、撤除必绿——空检查比没检查更糟）。
  B 发布边界+一键：白名单核对（对 oss_cli.RELEASE_DIRS/FILES 的导出物逐件对账；
    现账 state/logs/claims/briefs/docs/board/facade/inbox/spike/registry/config 禁入包）
    + 干净沙箱模拟 clone → cc init → 自检 ALL_PASS rc=0。
  C 现网默认等价：路径锚与数值/名称默认全部**回抓主干 git 基线**（git show main:…）逐一对表，
    主干基线取用两形兼容：字面量形（参数化前形态）直对，字面量缺位（config 外置常态）
    →exec 主干 config 副本取派生值对表；两形皆不可读=显式红。
    本文件自身不落任何宿主字面量（字面量只活在 git 对象库的内存副本里）；
    另跑配置优先级功能检（env > 文件层 > 内置默认）。

WSL 专属面（盘根探测/PS 派生形/#32-2 互解族）在 Windows 宿主无法实测时显式打
「待该侧验收」，禁静默吞。

用法：python -I cc-team/tests/oss-release-check.py
末行：OSS_RELEASE_PASS / OSS_RELEASE_FAIL: <原因>
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

# 公仓 CI 第四圈实锤（11-04 windows runner）：本步以 `python -I` 起跑，-I=-E 不读
# PYTHONIOENCODING，首行 print「A 敏感实扫」中文即 charmap 崩（ubuntu 同期全绿=非隔离步
# 吃 env 生效反证）。出口钉 utf-8=ctrld/dispatch 同款（MECH-GBKDECODE-BOMB-1 推广面）。
# 其 -I 子程（oss_cli selftest/export）各件出口自钉，见 ccards/oss_cli.py 同注。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CT = os.path.dirname(HERE)                      # cc-team 根
# 主仓布局（REPO=cc-team 上级）与独立发行仓（REPO=cc-team 即 git 根）两形自适应；
# git 不可用（如裸导出目录）时回退旧算式，行为与历史一致。
_r = subprocess.run(["git", "-C", CT, "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
REPO = (_r.stdout or "").strip() if _r.returncode == 0 else os.path.dirname(CT)
sys.path.insert(0, CT)
from ccards import config as _cfg               # noqa: E402  单配置入口（本锁的 C 判据数据源）
from ccards import oss_cli                      # noqa: E402  发布白名单唯一权威（B 判据对账对象）

# ---- 五形禁串：分片拼装，防本文件自扫命中（源码任何一行都凑不出完整串） ----
_FRAG = "Us" + "ers"
BAN_PATTERNS = [
    ("win-user-root", re.compile(re.escape("C:" + "\\" + _FRAG))),
    ("wsl-user-root", re.compile(re.escape("/mn" + "t/c/" + _FRAG))),
    ("host-user", re.compile(re.escape("Admini" + "strator"))),
    ("intranet-seg", re.compile(re.escape("10" + "." + "10"))),
    ("secret-prefix", re.compile(re.escape("s" + "k-"))),
]
RELEASE_FILES = list(oss_cli.RELEASE_FILES)
SKIP_DIR = "__pycache__"


def _iter_release(root):
    for d in oss_cli.RELEASE_DIRS:
        base = os.path.join(root, d)
        for cur, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x != SKIP_DIR]
            for fn in sorted(files):
                if fn.endswith((".pyc", ".pyo")):
                    continue
                yield os.path.relpath(os.path.join(cur, fn), root).replace(os.sep, "/")
    for fn in RELEASE_FILES:
        if os.path.isfile(os.path.join(root, fn)):
            yield fn


def scan(root):
    hits = []
    for rel in _iter_release(root):
        try:
            text = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for name, pat in BAN_PATTERNS:
                if pat.search(line):
                    hits.append("%s:%d(%s)" % (rel, i, name))
    return hits


def git_show(ref_path):
    r = subprocess.run(["git", "-C", REPO, "show", "main:" + ref_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def _main_cfg():
    """把主干 config.py 副本 exec 成隔离模块=「字面量缺位时以 config 派生值对表」的主干侧（PM 13:0x REJECT 修复清单第1条）。

    交付并入后主干 dispatch.py 已是 config 形，旧版只认 `"锚", "字面量"` 正则恒 None→锁自毁。
    主干 config.py 模块级仅 stdlib import+纯派生（无写副作用），可安全现执；
    TEAM 钉=本树 TEAM——跨树对表只考「派生逻辑之差」，仓位置之差不是漂移（否则道树对主检出路径恒假红）。
    主干无 config.py（11-02 前形态）返回 None，调用方回落字面量形；两形皆不可读=显式红，禁静默吞。"""
    src = git_show("cc-team/ccards/config.py")
    if not src:
        return None
    sbx = tempfile.mkdtemp(prefix="ossmain-")
    try:
        d = os.path.join(sbx, "ccards")
        os.makedirs(d)
        p = os.path.join(d, "config.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
        import importlib.util
        spec = importlib.util.spec_from_file_location("_oss_main_cfg_", p)
        mod = importlib.util.module_from_spec(spec)
        prev = os.environ.get("CC_TEAM_HOME")
        os.environ["CC_TEAM_HOME"] = _cfg.TEAM
        try:
            spec.loader.exec_module(mod)
        finally:
            if prev is None:
                os.environ.pop("CC_TEAM_HOME", None)
            else:
                os.environ["CC_TEAM_HOME"] = prev
        return mod
    finally:
        shutil.rmtree(sbx, ignore_errors=True)


def norm(p):
    if p is None:
        return None
    s = str(p).replace("\\", "/")
    s = re.sub(r"^/mn" + "t/([A-Za-z])/", lambda m: m.group(1).upper() + ":/", s)
    s = re.sub(r"/+$", "", s)
    return s.lower() if os.name == "nt" else s


def num_pairs(text):
    """(大写环境变量锚, 同行其后首个数值字面量) 全收集——主干侧与现侧同式对表用。"""
    return dict(re.findall(r'"([A-Z][A-Z0-9]{2,}_[A-Z0-9_]+)",\s*"?([0-9]+(?:\.[0-9]+)?)"?', text))


def str_default(text, env):
    m = re.search(r'"%s",\s*"([^"]+)"' % re.escape(env), text)
    return m.group(1) if m else None


def _red_selfproof():
    """栽一笔必红、撤了必绿：只动临时沙箱副本，绝不碰真树（红检残留教训）。"""
    sbx = tempfile.mkdtemp(prefix="ossred-")
    try:
        shutil.copytree(os.path.join(CT, "ccards"), os.path.join(sbx, "ccards"))
        tgt = os.path.join(sbx, "ccards", "config.py")
        with open(tgt, "a", encoding="utf-8") as f:
            f.write("\n# PLANTED-" + "Admini" + "strator" + "-probe\n")
        planted = scan(sbx)
        with open(tgt, "w", encoding="utf-8") as f:
            f.write(open(os.path.join(CT, "ccards", "config.py"), encoding="utf-8").read())
        restored = scan(sbx)
        return bool(planted) and not restored, planted[:2]
    finally:
        shutil.rmtree(sbx, ignore_errors=True)


def _stage_b():
    """导出白名单核对 + 干净沙箱 init 全链。"""
    sbx = tempfile.mkdtemp(prefix="ossexp-")
    try:
        out = os.path.join(sbx, "pkg")
        r = subprocess.run([sys.executable, "-I", os.path.join(CT, "ccards", "oss_cli.py"),
                            "export", "--out", out], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return False, "export rc=%s: %s" % (r.returncode, (r.stderr or r.stdout).strip()[-160:])
        names = set(os.listdir(out))
        need = set(oss_cli.RELEASE_DIRS) | set(RELEASE_FILES) | {"RELEASE-MANIFEST.txt"}
        missing = sorted(need - names)
        banned_dirs = {"state", "logs", "claims", "briefs", "docs", "board", "facade",
                       "inbox", "spike", "registry.json", "config.toml", SKIP_DIR}
        # 隐藏项默认全禁（防 .env/.gitignore 之类混包）；白名单显式列名的放行——
        # .gitattributes（Windows autocrlf 可用性判据）与 .github/CI 属后者（REQ-OSS-PREP-1 2026-11-03）。
        allowdots = set(oss_cli.RELEASE_DIRS) | set(oss_cli.RELEASE_FILES)
        leaked = sorted((names & banned_dirs) |
                        {x for x in names if x.startswith(".") and x not in allowdots})
        if missing or leaked:
            return False, "包边界违规 missing=%s leaked=%s" % (missing or "-", leaked or "-")
        nfiles = sum(len(fs) for _, _, fs in os.walk(out))
        # 模拟 clone 后一键：init --dir 新工程，末行必须 ALL_PASS 且 rc=0
        proj = os.path.join(sbx, "proj")
        os.makedirs(proj)
        r2 = subprocess.run([sys.executable, "-I", os.path.join(out, "ccards", "oss_cli.py"),
                             "init", "--dir", proj], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        tail = (r2.stdout or "").strip().splitlines()
        last = tail[-1] if tail else ""
        if r2.returncode != 0 or last != "ALL_PASS":
            return False, "沙箱 init rc=%s 末行=%r" % (r2.returncode, last[-80:])
        # 骨架齐件核对
        want = [os.path.join("cc-team", x) for x in
                ("state", "logs", "claims", "config.toml", "cards_dag.py", "registry.json")]
        miss2 = [w for w in want if not os.path.exists(os.path.join(proj, w))]
        if miss2:
            return False, "init 骨架缺件=%s" % miss2
        return True, "pkg files=%d; init ALL_PASS@clone" % nfiles
    finally:
        shutil.rmtree(sbx, ignore_errors=True)


def _stage_c():
    """默认等价：路径锚+数值+任务名全部回抓主干基线对表；配置优先级功能检。

    主干基线两形兼容（PM 13:0x REJECT 修复清单第1条——旧版只认字面量形，
    交付并入后恒空=锁自毁，外线转正永循环 EXT-PEND）：
      形一（参数化前/有人把字面量种回主干）：正则取 `"锚", "字面量"` 直对；
      形二（config 外置后的常态）：主干 config 副本现执取派生值对表；
      两形皆不可读 → 显式红（禁静默吞）。"""
    bad, n = [], 0
    od = git_show("cc-team/ops/dispatch.py")
    orr = git_show("cc-team/ops/recycle.py")
    if not od or not orr:
        # 独立发行仓（HEAD 里本文件顶层形态存在、无 cc-team/ 主干前缀可对表）=C 合法降级 SKIP，
        # 防 GitHub CI 假红；主仓形态仍显式红（禁静默吞锁自毁）。
        probe = subprocess.run(["git", "-C", REPO, "cat-file", "-e", "HEAD:ops/dispatch.py"],
                               capture_output=True)
        if probe.returncode == 0:
            return "SKIP", 0, "独立发行仓：无主仓主干基线可对表（A/B 照强制；CI 形态合法）"
        return False, 0, "git show 主干基线不可达（锁必须在源仓跑）"
    mc = _main_cfg()
    # 比较器自证：norm/num_pairs 若退化成恒等/恒空，下面的对表全绿也是假绿（空检查比没检查更糟）
    n += 2
    if norm("x:/aa") == norm("x:/ab"):
        bad.append("norm 比较器失效（异值判等）")
    if num_pairs('"DISPATCH_SMOKE_X1", 7') != {"DISPATCH_SMOKE_X1": "7"}:
        bad.append("num_pairs 解析失效（取不到演示锚）")
    # 1) 路径锚（normpath 双向形态归一后逐字对表；两形兼容）
    for tag, env, attr in (("hb", "DISPATCH_HB", "CC_DISP_HB"),
                           ("proj", "DISPATCH_PROJ", "CC_PROJ"),
                           ("code", "DISPATCH_CODE_ROOT", "CC_CODE")):
        n += 1
        m = re.search(r'"%s",\s*"([^"]+)"' % re.escape(env), od)
        base = m.group(1) if m else (str(getattr(mc, attr)) if mc and getattr(mc, attr, None) else None)
        cur = getattr(_cfg, attr, None)
        if base is None or cur is None:
            bad.append("锚 %s 两形皆不可读（字面量缺位且主干派生=%s）"
                       % (tag, "主干不可达" if mc is None else "缺名"))
        elif norm(base) != norm(cur):
            bad.append("锚 %s 主干=%r 现=%r" % (tag, base, cur))
    m_sl = re.search(r'"([A-Za-z]--[^"]*?wf-)%s"', od)
    if m_sl:
        n += 1
        for lane in ("slot-21", "m9", "zz-7"):
            if _cfg.proj_dir_name(_cfg.CC_CODE, lane) != m_sl.group(1) + lane:
                bad.append("转录目录名漂移 lane=%s" % lane)
    elif "_cfg.proj_dir_name(" in od and mc is not None and callable(getattr(mc, "proj_dir_name", None)):
        # 形二：主干已走 proj_dir_name 单源——主干侧编码 vs 本树编码对表（改编码=主干先并才自洽）
        n += 1
        for lane in ("slot-21", "m9", "zz-7"):
            a = _cfg.proj_dir_name(_cfg.CC_CODE, lane)
            b = mc.proj_dir_name(mc.CC_CODE, lane)
            if a != b:
                bad.append("转录编码漂移 lane=%s（本树形与主干形不一致）" % lane)
    else:
        bad.append("主干转录前缀两形皆不可解析")
    # 2) 数值默认（主干侧 (env锚,数值) 全集 vs 现侧全集，同名必同值；单边缺名=改锚未同步=红）
    for ref, cur_path in (("cc-team/ops/dispatch.py", "dispatch.py"),
                          ("cc-team/ops/recycle.py", "recycle.py"),
                          ("cc-team/ops/ctrld.py", "ctrld.py"),
                          ("cc-team/ops/fuse.py", "fuse.py")):
        o, c = git_show(ref), open(os.path.join(CT, "ops", cur_path), encoding="utf-8").read()
        op, cp = num_pairs(o), num_pairs(c)
        for k in sorted(op):
            if k in ("DISPATCH_HB",):
                continue
            n += 1
            if k not in cp:
                bad.append("数值锚 %s 现侧缺（参数化漏改）" % k)
            elif float(op[k]) != float(cp[k]):
                bad.append("数值漂移 %s 主干=%s 现=%s" % (k, op[k], cp[k]))
    # 3) 计划任务名默认（两形：主干字面量缺位→主干 config 派生值；皆不可读=红不吞）
    for env, attr in (("CTRLD_TASK_NAME", "CC_TASK_CTRLD"),
                      ("BOOT_TASK_NAME", "CC_TASK_BOOT")):
        otxt = git_show("cc-team/ops/ctrld.py") + git_show("cc-team/tests/boot_check.sh")
        d = str_default(otxt, env)
        if d is None and mc is not None and getattr(mc, attr, None):
            d = str(getattr(mc, attr))
        n += 1
        if d is None:
            bad.append("任务名 %s 两形皆不可解析（字面量缺位且主干派生缺名）" % env)
        elif d != getattr(_cfg, attr, None):
            bad.append("任务名漂移 %s 主干=%r 现=%r" % (env, d, getattr(_cfg, attr, None)))
    # 4) 配置优先级功能检：env > 文件层 > 内置默认（沙箱现写现删，不碰真树）
    sbx = tempfile.mkdtemp(prefix="osscfg-")
    try:
        cfgp = os.path.join(sbx, "cfg.toml")
        with open(cfgp, "w", encoding="utf-8") as f:
            f.write("[dispatch]\nlane_cap = 3\n")
        code = ("import sys; sys.path.insert(0, %r); from ccards import config;"
                "print(config.num_setting('dispatch.lane_cap','DISPATCH_CAP',6))" % CT)
        def run(env):
            e = dict(os.environ); e.update(env)
            r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                               text=True, env=e, encoding="utf-8", errors="replace")
            return (r.stdout or "").strip()
        n += 3
        if run({"CC_CONFIG": cfgp}) != "3":
            bad.append("文件层未生效（期望 lane_cap=3）")
        if run({"CC_CONFIG": cfgp, "DISPATCH_CAP": "4"}) != "4":
            bad.append("env 未压过文件层（期望 4）")
        if run({"CC_CONFIG": os.path.join(sbx, "absent.toml")}) != "6":
            bad.append("缺文件时内置默认丢失（期望 6）")
    finally:
        shutil.rmtree(sbx, ignore_errors=True)
    return (not bad), n, "; ".join(bad) if bad else ""


def main():
    fails = []
    # ---- A ----
    hits = scan(CT)
    if hits:
        fails.append("A 五形命中 %d 处: %s" % (len(hits), ",".join(hits[:6])))
        print("A 敏感实扫: FAIL hits=%d" % len(hits))
    else:
        print("A 敏感实扫: 发布集 %d 文件 0 命中" % len(list(_iter_release(CT))))
    ok, sample = _red_selfproof()
    print("A 栽红自证: %s（植笔样本=%s）" % ("红得起/撤得绿" if ok else "FAIL 空检查", sample))
    if not ok:
        fails.append("A 红自证失效（守卫红了才值钱）")
    # ---- B ----
    okb, det = _stage_b()
    print("B 边界+一键: %s — %s" % ("OK" if okb else "FAIL", det))
    if not okb:
        fails.append("B " + str(det))
    # ---- C ----
    okc, nc, why = _stage_c()
    if okc == "SKIP":
        print("C 默认等价: SKIP — %s" % why)
    else:
        print("C 默认等价: %s — 对表面 %d 项%s" % ("OK" if okc else "FAIL", nc,
                                                  ("；红因: " + why) if why else ""))
        if not okc:
            fails.append("C " + why)
    # ---- WSL 专属面声明（禁静默吞）----
    if os.name == "nt":
        print("待该侧验收(WSL): 盘根探测/CC_PS WSL 形/boot_check 挂载扫/selftest #32-2 互解族")
    else:
        print("待该侧验收(Windows): 计划任务名实注面（boot_bringup_register --live 模板）")

    if fails:
        print("OSS_RELEASE_FAIL: " + " | ".join(fails))
        return 1
    print("OSS_RELEASE_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
