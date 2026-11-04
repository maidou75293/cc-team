#!/usr/bin/env python3
# train-hostpin-test.py — BUG-TRAIN-HOSTPIN-1 红绿锁（火车步②①宿主收口 + 判据子进程出口钉 UTF-8）。
# 病根两笔（判定链污染）：
#   ①mergetrain 步②card_verify_step/步①stage1_verify 旧式裸 getenv("CARD_VERIFY_BASH","bash")——
#     env 缺位时 win 任务 PATH 裸 bash=System32 WSL 启动器（12:22:49 REQ-OSS 队列复验现证：
#     fail_detail 带 '/mnt/c' 探针+目录名漂移三连坐=双态判据上毒宿主假红）；修复=收口进
#     _scope_bash→ccards._resolve_verify_bash 单源（与步③/闸同源），缺位=响亮红。
#   ②判据子进程默认 env 未钉出口 UTF-8——内层 cp936 吐 GBK 字节，读侧 utf-8/replace 把
#     U+FFFD+希伯来段（GBK 字节对恰合成合法 UTF-8 序列）落进 events.log **不可逆**；
#     修复=火车收口 mergetrain.judge_env（scope_env 基座）+ _env_pin_exit 边界窗口（步①②
#     run_argv 继承面——REDAUTO 共享链签名零扰动，recycle/recon 他人假件不动，done 闸越域
#     实录后收进卡面域）+ ctrld.child_env 同钉第二面；两面平权，parity 由 T0f/T4c 钉死。
#     已知边界：`python -I` 吞全部 PYTHON* 变量（本机实锤 -I→gbk），该形态脚本出口归各自
#     入口 reconfigure（GBKPRINT 推广面）——本锁的内层探针不用 -I 形态，盯**收口点本身**。
#   ③增补（PM 13:0x 现证）辅账悬置死角：缺账/refailed×交付已在主干时 blocked 圈与 passed
#     队列两入口都扫不到=裸 continue 悬置等人工；修复=①面对表走 _ext_route 外合闭环。
# 手法（双侧形态，卡面要求 a/b/c）：
#   T0 静态闸：火车面不许再出现盲值行/必须走 _scope_bash+judge_env；哨=同检测式在 recycle
#      done-sweep 面（未收口、本卡不碰）确实抓得到=探针能红。
#   T1 S0 gbk 模拟有效哨：无钉探针必须真吐 GBK 字节（防本锁空转假绿）。
#   T2/T3 宿主面：假 System32 桩沙箱（PATH 唯一成员=毒 bash，同 verify-bash-pin-check 手法）——
#      空 env 解析不落毒宿主；显式配毒=钉死失败响亮红且判据子进程**零起**（marker 不落地为证）；
#      撤修复=盲值形态复刻副本（_scope_bash/judge_env 打回旧语义）在同场景必红/必上毒宿主。
#   T4 UTF-8 面：注 gbk 宿主环境跑步②真判据——修复后 fail_detail 严格 UTF-8 无替换符且中文
#      关键词在；scope_env/child_env 同钉；字节级双相（收口 env=UTF-8 字节 / 裸继承=GBK 字节）；
#      撤修复副本必红。
#   T5 增补面：辅账悬置形态×交付已在主干——缺账/refailed 窗内必走外合闭环转正（verdict PASS+
#      stage=merged(外线) 落账）；撤针（_ext_route 打桩 no-op=增补前裸让路世界）必悬置零转正；
#      窗内未外合维持旧退避零空转（防过度修正）；dry=只点名零写。
# 跑法: python -I cc-team/tests/train-hostpin-test.py   exit 0=末行 TRAIN_HOSTPIN_PASS
import io, os, re, shutil, subprocess, sys, tempfile, time, types, importlib.util

try:  # 本锁自身出口同钉（dogfood：PASS 行与中文场景名，cp936 宿主裸 print 即重现症状）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # cc-team 根
NO_WIN = 0x08000000 if os.name == "nt" else 0
NT = os.name == "nt"
POP = object()
fails = []


def ok(tag, extra=""):
    print("PASS:", tag, extra)


def bad(tag, *ctx):
    fails.append(tag)
    print("FAIL:", tag, *[str(c)[:220] for c in ctx])


def skip(tag):
    print("SKIP:", tag)


def spec_load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def src_slice(src, start_marker):
    """从 `def start_marker` 起至下一个顶层 `def `（含）——按函数体切源码做静态闸。"""
    i = src.index("def " + start_marker)
    j = src.find("\ndef ", i + 1)
    return src[i:] if j < 0 else src[i:j]


def under(path, root):
    return path and os.path.normpath(path).lower().startswith(os.path.normpath(root).lower())


def apply_env(**mapping):
    save = {}
    for k, v in mapping.items():
        save[k] = os.environ.get(k)
        if v is POP:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    return save


def restore_env(save):
    for k, v in save.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ── 沙箱：CC_TEAM_HOME 隔离账 + 假 System32 毒桩目录（bash.exe 与 bash 各一枚文本桩） ──
HOME = tempfile.mkdtemp(prefix="cc-train-hostpin-home-")
os.environ["CC_TEAM_HOME"] = HOME                          # 须在导入 ccards 前设
FAKE_SR = tempfile.mkdtemp(prefix="cc-train-hostpin-fakesr-")
STUB = "#!/bin/sh\necho FAKE-HOST-RAN\nexit 0\n"
for _n in ("bash.exe", "bash"):
    _p = os.path.join(FAKE_SR, _n)
    io.open(_p, "w", encoding="utf-8", newline="\n").write(STUB)
    os.chmod(_p, 0o755)

sys.path.insert(0, CT)
mt = spec_load("mt_fixed", os.path.join(CT, "ops", "mergetrain.py"))
ctr = spec_load("ctr_fixed", os.path.join(CT, "ops", "ctrld.py"))
from ccards import commands                                # noqa: E402  (win 候选 monkeypatch 面)

# 撤修复副本=同一模块再载入一份，把收口函数打回旧语义（盲值宿主+裸继承 env），行为=修复前本体
mt_old = spec_load("mt_poison", os.path.join(CT, "ops", "mergetrain.py"))
mt_old._scope_bash = lambda: (os.environ.get("CARD_VERIFY_BASH", "bash"), None)
mt_old.judge_env = lambda: dict(os.environ)
mt_old._env_pin_exit = lambda: (lambda: None)     # 窗口钉打回=放行继承毒值（修复前本体）


def load_old_ctrld():
    src = io.open(os.path.join(CT, "ops", "ctrld.py"), encoding="utf-8").read()
    old = src.replace('    env["PYTHONIOENCODING"] = "utf-8"\n'
                      '    env["PYTHONUTF8"] = "1"',
                      '    env.setdefault("PYTHONIOENCODING", "utf-8")\n'
                      '    env.setdefault("PYTHONUTF8", "1")')
    if old == src:
        return None, "锚丢失：ctrld.child_env 硬钉行改形未同步本锁（收口被谁搬了？）"
    m = types.ModuleType("ctr_poison")
    m.__file__ = os.path.join(CT, "ops", "ctrld.py")
    exec(compile(old, m.__file__, "exec"), m.__dict__)
    return m, None


# ── FakeRecycle：步①宿主/环境断言（不起真子进程，argv/env 全录） ──
class FakeRC(object):
    """REDAUTO 链假件：run_argv 保持 **base 签名 (argv, to)**——本卡零扰动共享链的自证；
    窗口钉生效与否在调用时刻对 os.environ 拍快照断言（子进程 spawn 走的就是继承面）。"""
    def __init__(self):
        self.calls = []
        self.verdicts = []

    def card_cwd(self, c):
        return None

    def run_argv(self, argv, to):
        self.calls.append((list(argv), to,
                           {k: os.environ.get(k) for k in ("PYTHONIOENCODING", "PYTHONUTF8")}))
        return 0, "FAKE-GREEN"

    def via_host(self):
        return "lock/lock"

    def looks_host_mismatch(self, rc, out):
        return False

    def flat_tail(self, out, n=120):
        return " ".join((out or "").split())[-n:]

    def do_verdict(self, cid, result, reason):
        self.verdicts.append((cid, result, reason))
        return True


def drive_stage1(module, fake, logbuf):
    cards = {"T-HPX": {"status": "DONE", "done_at": "2026-11-03T00:00:00+0800",
                       "commit": "f" * 40, "repo": "R", "verify": "echo T-HPX-VERIFY"}}
    state = {"cards": {}}

    def git(repo, *args):
        if args and args[0] == "rev-parse":
            return 0, "sha"
        if args and args[0] == "merge-base":
            return 1, ""                       # 不在主干=会起交卷即验
        return 0, ""
    return module.stage1_verify(cards, state, HOME, True,
                                lambda s: logbuf.append(str(s)), fake, git=git)


def main():
    # ══ T0 静态闸：火车面盲值行/收口引用（哨=同式在 recycle done-sweep 面确实抓得到） ══
    mt_src = io.open(os.path.join(CT, "ops", "mergetrain.py"), encoding="utf-8").read()
    rc_src = io.open(os.path.join(CT, "ops", "recycle.py"), encoding="utf-8").read()
    blind = 'os.environ.get("CARD_VERIFY_BASH", "bash")'
    # 哨=模式自检（防传输层把检测串咬缺=空转假绿）：把本文件源码伪造回旧形态，检测式必须抓到
    if blind in mt_src.replace("_scope_bash()", blind, 1):
        ok("T0s 检测模式自检（伪造旧形态必命中）")
    else:
        bad("T0s 检测模式本体已损坏（传输咬缺=静态闸空转）")
    if blind in mt_src:
        bad("T0a 火车面盲值兜底行回归 mergetrain.py!", "")
    else:
        ok("T0a mergetrain.py 无盲值兜底行",
           "（done-sweep 面盲区 recycle:987 卡面边界不碰=%s）" % ("仍在" if blind in rc_src else "已收口"))
    for fn in ("card_verify_step", "stage1_verify"):
        s = src_slice(mt_src, fn)
        if "_scope_bash()" in s and "_env_pin_exit()" in s and \
           not re.search(r"run_argv\([^)]*env=", s, re.S):
            ok("T0b %s 走 _scope_bash+窗口钉收口且未向共享链注入 env 参" % fn)
        else:
            bad("T0b %s 收口引用缺失或扰动共享链签名" % fn, s[:120])
    if "judge_env()" in src_slice(mt_src, "scope_env"):
        ok("T0c scope_env 基座=judge_env 同收口")
    else:
        bad("T0c scope_env 未走 judge_env", "")
    if "def run_argv(argv, to)" in rc_src:
        ok("T0d recycle.run_argv 保持 base 签名（REDAUTO 共享链零扰动=不越他卡域）")
    else:
        bad("T0d run_argv 签名被改（越域随改回潮，done 闸实录教训）", "")
    ctr_src = io.open(os.path.join(CT, "ops", "ctrld.py"), encoding="utf-8").read()
    ce = src_slice(ctr_src, "child_env")
    if 'env["PYTHONIOENCODING"] = "utf-8"' in ce and 'env["PYTHONUTF8"] = "1"' in ce and \
       'setdefault("PYTHONIOENCODING"' not in ce:
        ok("T0e ctrld.child_env 出口=本地硬钉不保继承")
    else:
        bad("T0e child_env 硬钉形态漂移", ce[:150])
    je = src_slice(mt_src, "judge_env")
    pin2 = 'env["PYTHONIOENCODING"] = "utf-8"'
    if pin2 in je and pin2 in ce and "utf8_exit" not in mt_src and "utf8_exit" not in ctr_src:
        ok("T0f judge_env/child_env 两面同钉平权（收口全在卡面域，无越域文件依赖）")
    else:
        bad("T0f 同钉两面漂移或越域依赖回潮", je[:60], ce[:60])
    st1 = src_slice(mt_src, "stage1_verify")
    if st1.count("_ext_route(") >= 2 and "_ext_merge_closure" in src_slice(mt_src, "_ext_route"):
        ok("T0g ①面外合闭环在册（缺账+refailed 窗内两形态各一针，缝通 closure）")
    else:
        bad("T0g 增补缝漂移（辅账悬置死角回潮）", st1.count("_ext_route("))
    if st1.replace("_ext_route(", "X(", 1).count("_ext_route(") == st1.count("_ext_route(") - 1:
        ok("T0gs T0g 检测式自检（伪造删一针必减数=探针能红）")
    else:
        bad("T0gs 检测式本体损坏（空转假绿风险）", "")

    # ══ T1 S0 gbk 模拟有效哨：无钉探针必须真吐 GBK 字节（模拟失效=T4 全部假绿） ══
    save = apply_env(PYTHONIOENCODING="gbk", PYTHONUTF8="0")
    try:
        r = subprocess.run([sys.executable, "-c", "print('转录目录名漂移')"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=60, creationflags=NO_WIN)
        got = (r.stdout or b"").strip()
        if got == "转录目录名漂移".encode("gbk"):
            ok("T1 S0 gbk 宿主模拟有效（裸探针确实吐 GBK 字节）")
        elif got == "转录目录名漂移".encode("utf-8"):
            bad("T1 S0 gbk 模拟失效（探针仍吐 UTF-8）——T4 为假绿风险，本锁空转")
        else:
            bad("T1 S0 哨字节不可判", got[:80])
    finally:
        restore_env(save)

    # ══ T2 步①宿主收口（stage1_verify，FakeRC 全录 argv/env） ══
    poison_bash = os.path.join(FAKE_SR, "bash.exe") if NT else "/nonexistent/train-hostpin-bash"
    PY_NAME = "python" if NT else ("python3" if shutil.which("python3") or not shutil.which("python") else "python")
    # T2a 空 env + PATH 唯一成员=假 System32：修复必不落毒宿主；撤修复（盲 "bash"）必上毒宿主面
    if NT:
        save = apply_env(PATH=FAKE_SR, SystemRoot=FAKE_SR, CARD_VERIFY_BASH=POP,
                         PYTHONIOENCODING="gbk", PYTHONUTF8="0")
        try:
            f1, logs1 = FakeRC(), []
            g1 = drive_stage1(mt, f1, logs1)
            if len(f1.calls) == 1:
                argv0, env0 = f1.calls[0][0][0], f1.calls[0][2] or {}
                if under(argv0, FAKE_SR):
                    bad("T2a 空 env 解析落假 System32（毒宿主未排除）!", argv0)
                elif not os.path.isfile(argv0):
                    bad("T2a 解析值不可用", argv0)
                elif env0.get("PYTHONIOENCODING") != "utf-8" or env0.get("PYTHONUTF8") != "1":
                    bad("T2a 步①判据子进程出口未钉 UTF-8（gbk 继承被放行）",
                        env0.get("PYTHONIOENCODING"), env0.get("PYTHONUTF8"))
                elif g1.get("passed") == 1:
                    ok("T2a 空 env→Git Bash 候选干净解析 + env 出口钉 UTF-8（不落 System32）")
                else:
                    bad("T2a 计数异常", g1)
            else:
                bad("T2a 判据未起", len(f1.calls))
            f2, logs2 = FakeRC(), []
            drive_stage1(mt_old, f2, logs2)
            o0 = f2.calls[0][0][0] if f2.calls else ""
            oenv = (f2.calls[0][2] or {}) if f2.calls else {}
            if f2.calls and (o0 == "bash" or under(o0, FAKE_SR)) and \
               oenv.get("PYTHONIOENCODING") != "utf-8":
                ok("T2a-哨 撤修复形态必盲落裸 bash/放行 gbk 继承（本锁红得起来）")
            else:
                bad("T2a-哨 撤修复未红（空转假绿风险）", o0, oenv.get("PYTHONIOENCODING"))
        finally:
            restore_env(save)
    else:
        skip("T2a 空 env 毒 PATH 组（win-only：POSIX 零动不涉 SystemRoot 判据）")
    # T2b 显式配毒：修复=零起+响亮 pending「宿主钉死失败」；撤修复=照上毒宿主（有 calls）
    for label, mod, want_red in (("T2b", mt, True), ("T2b-哨", mt_old, False)):
        save = apply_env(CARD_VERIFY_BASH=poison_bash, PATH=FAKE_SR if NT else os.environ.get("PATH"),
                         SystemRoot=FAKE_SR if NT else POP)
        try:
            f, logs = FakeRC(), []
            g = drive_stage1(mod, f, logs)
            if want_red:
                if len(f.calls) == 0 and g.get("pending") == 1 and \
                   any("宿主钉死" in x for x in logs):
                    ok("T2b 显式配毒=判据子进程零起 + pending 响亮红（宁红不猜）")
                else:
                    bad("T2b 显式毒未被拒于起跑前", len(f.calls), g, logs[:1])
            else:
                if len(f.calls) >= 1:
                    ok("T2b-哨 撤修复形态带上毒宿主起判据（本锁红得起来）")
                else:
                    bad("T2b-哨 撤修复未起判据（哨失效）", f.calls)
        finally:
            restore_env(save)
    # T2c win：毒 PATH + Git Bash 候选全缺 = die → 响亮 pending（candidates monkeypatch）
    if NT:
        _orig = commands._git_bash_candidates
        commands._git_bash_candidates = lambda: [os.path.join(FAKE_SR, "nope-bash.exe")]
        save = apply_env(PATH=FAKE_SR, SystemRoot=FAKE_SR, CARD_VERIFY_BASH=POP)
        try:
            f, logs = FakeRC(), []
            g = drive_stage1(mt, f, logs)
            if len(f.calls) == 0 and g.get("pending") == 1 and any("宁红不猜" in x for x in logs):
                ok("T2c 候选全缺=die 响亮 pending（绝不回退裸 bash）")
            else:
                bad("T2c 候选全缺未响亮红", f.calls, g, logs[:1])
        finally:
            commands._git_bash_candidates = _orig
            restore_env(save)
    else:
        skip("T2c 候选全缺组（win-only 分支）")

    # ══ T3 步②真宿主形态（card_verify_step 走真 recycle.run_argv） ══
    if NT:
        save = apply_env(PATH=FAKE_SR, SystemRoot=FAKE_SR, CARD_VERIFY_BASH=POP)
        try:
            rc1, t1 = mt.card_verify_step(HOME, "echo HPX-2A-MARK", spec_load("rc_mod", os.path.join(CT, "ops", "recycle.py")))
            if rc1 == 0 and "HPX-2A-MARK" in (t1 or ""):
                ok("T3a 空 env 步②在干净 Git Bash 真跑绿（毒 PATH 未染宿主）")
            else:
                bad("T3a 步②真跑未绿", rc1, t1[:120])
            rc2, t2 = mt_old.card_verify_step(HOME, "echo HPX-2A-MARK", spec_load("rc_mod2", os.path.join(CT, "ops", "recycle.py")))
            if not (rc2 == 0 and "HPX-2A-MARK" in (t2 or "")):
                ok("T3a-哨 撤修复形态在毒 PATH 下必红/必上毒宿主（本锁红得起来）",
                   "rc=%s tail=%s" % (rc2, (t2 or "")[:60]))
            else:
                bad("T3a-哨 撤修复竟也绿（哨失效=假绿风险）", rc2, t2[:80])
        finally:
            restore_env(save)
    else:
        skip("T3a 毒 PATH 真跑组（win-only：假桩非 ELF，POSIX 下新旧同态无鉴别）")
    # T3b 显式配毒：修复=None+「宿主钉死失败」且 marker 不落地；撤修复=marker 落地或 exec-failed（无钉死语）
    mk = os.path.join(HOME, "hpx3b.marker")
    for label, mod, want in (("T3b", mt, "fixed"), ("T3b-哨", mt_old, "old")):
        if os.path.exists(mk):
            os.remove(mk)
        # 毒径判据=%SystemRoot% 前缀——假 SR 须同时顶掉 SystemRoot 指针才成「WSL 启动器形」（同 verify-bash-pin-check）
        save = apply_env(CARD_VERIFY_BASH=poison_bash, SystemRoot=FAKE_SR if NT else POP)
        try:
            rc, t = mod.card_verify_step(HOME, "touch '%s'; echo MARKED" % mk.replace("\\", "/"),
                                         spec_load("rc_mod_%s" % label, os.path.join(CT, "ops", "recycle.py")))
        finally:
            restore_env(save)
        landed = os.path.exists(mk)
        if want == "fixed":
            if rc is None and "宿主钉死" in (t or "") and not landed:
                ok("T3b 显式配毒=步②钉死失败响亮红且判据零执行（marker 未落地）")
            else:
                bad("T3b 显式毒未被响亮拒", rc, t[:100], "landed=%s" % landed)
        else:
            if landed or "宿主钉死" in (t or ""):
                bad("T3b-哨 撤修复形态未被本锁判红（异常绿）", rc, t[:100])
            else:
                ok("T3b-哨 撤修复形态走毒宿主尝试（exec failed/不可用语，无钉死拒）——红得起来",
                   "rc=%s tail=%s" % (rc, (t or "")[:60]))

    # ══ T4 UTF-8 落账（注 gbk 宿主；步②真判据 + scope_env/child_env 钉 + 字节双相） ══
    phrase = "转录目录名漂移 值对"
    g3_verify = '%s -c "print(\'%s\')"' % (PY_NAME, phrase)
    rcycle = spec_load("rc_mod_t4", os.path.join(CT, "ops", "recycle.py"))
    save = apply_env(PYTHONIOENCODING="gbk", PYTHONUTF8="0", CARD_VERIFY_BASH=POP)
    try:
        rc1, vt1 = mt.card_verify_step(HOME, g3_verify, rcycle)
        if rc1 == 0 and phrase in (vt1 or "") and chr(0xfffd) not in (vt1 or ""):
            ok("T4a 修复后 fail_detail 严格 UTF-8 无替换符且中文关键词在")
        else:
            bad("T4a UTF-8 落账失败", rc1, (vt1 or "")[:120])
        rc2, vt2 = mt_old.card_verify_step(HOME, g3_verify, rcycle)
        if phrase not in (vt2 or "") or chr(0xfffd) in (vt2 or ""):
            ok("T4a-哨 撤修复形态必红（盲宿主上毒=command not found/GBK 乱码任一红相；"
               "GBK 字节相由 T4b-哨 隔离实证）", (vt2 or "")[:60])
        else:
            bad("T4a-哨 撤修复竟干净落账（哨失效空转）", rc2, (vt2 or "")[:100])
        # 字节级双相：收口 env=UTF-8 严格可解；裸继承=GBK 字节（同一命令，钉与不钉各一相）
        b1, berr = mt._scope_bash()
        if berr:
            bad("T4b 宿主解析失败（无法做字节双相）", berr)
        else:
            raw_new = subprocess.run([b1, "-c", g3_verify], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, env=mt.judge_env(),
                                     timeout=120, creationflags=NO_WIN).stdout or b""
            raw_old = subprocess.run([b1, "-c", g3_verify], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, env=dict(os.environ),
                                     timeout=120, creationflags=NO_WIN).stdout or b""
            try:
                dec = raw_new.decode("utf-8")            # 严格解，解不动=红
                strict_ok = phrase in dec and chr(0xfffd) not in dec
            except UnicodeDecodeError as exc:
                strict_ok = False
            if strict_ok:
                ok("T4b judge_env 出口字节严格 UTF-8（判据子进程中文原样）")
            else:
                bad("T4b judge_env 出口非严格 UTF-8", raw_new[:60])
            if phrase.encode("gbk") in raw_old:
                ok("T4b-哨 裸继承同命令确实吐 GBK 字节（事故字节形复现=钉子有货）")
            else:
                bad("T4b-哨 GBK 字节相未复现（模拟空转风险）", raw_old[:60])
        # 收口三面对账：scope_env / child_env 同钉；撤修复形态（judge 直通 + setdefault 旧式）必放行 gbk
        se = mt.scope_env(HOME)
        ce = ctr.child_env()
        if se.get("PYTHONIOENCODING") == "utf-8" and ce.get("PYTHONIOENCODING") == "utf-8" \
           and se.get("PYTHONUTF8") == "1" and ce.get("PYTHONUTF8") == "1":
            ok("T4c scope_env/child_env/judge_env 三面同钉 UTF-8（不赌继承 locale）")
        else:
            bad("T4c 三面钉不齐", se.get("PYTHONIOENCODING"), ce.get("PYTHONIOENCODING"))
        if mt_old.scope_env(HOME).get("PYTHONIOENCODING") == "gbk":
            ok("T4c-哨 撤修复 scope_env 放行继承 gbk（红得起来）")
        else:
            bad("T4c-哨 scope_env 撤修复未放行 gbk（哨失效）")
        cold, aerr = load_old_ctrld()
        if aerr:
            bad("T4c-哨 ctrld 撤修复锚丢失", aerr)
        elif cold.child_env().get("PYTHONIOENCODING") == "gbk":
            ok("T4c-哨 撤修复 child_env setdefault 放行继承 gbk（红得起来）")
        else:
            bad("T4c-哨 child_env 撤修复未放行 gbk（哨失效）", cold.child_env().get("PYTHONIOENCODING"))
    finally:
        restore_env(save)

    # ══ T5 增补面：辅账悬置形态死角（缺账/refailed×交付已在主干→①面对表走外合闭环） ══
    sb5 = os.path.join(HOME, "sb5")
    os.makedirs(os.path.join(sb5, "wt"), exist_ok=True)

    def drive_anc(module, fake, logbuf, anc_rc, pre_state, apply_flag):
        cards = {"T-HPX": {"status": "DONE", "done_at": "2026-11-03T00:00:00+0800",
                           "commit": "f" * 40, "repo": "R", "verify": "echo T-HPX-VERIFY"}}
        state = {"cards": dict(pre_state)}

        def git(repo, *args):
            if args and args[0] == "rev-parse":
                return 0, "sha"
            if args and args[0] == "merge-base":
                return anc_rc, ""        # 0=交付已在主干（外合形态）
            return 0, ""
        g = module.stage1_verify(cards, state, HOME, apply_flag,
                                 lambda s: logbuf.append(str(s)), fake, git=git)
        return g, state

    _sbx, _cln = mt.sandbox_external, mt.sandbox_cleanup
    mt.sandbox_external = lambda repo, tip, cid, git: (sb5, None)
    mt.sandbox_cleanup = lambda repo, root, git: None
    mt_old._ext_route = lambda *a, **k: "skip"   # 撤针=增补前①面裸让路世界
    try:
        # T5a 缺账×外合×apply：闭环必起必转正（verdict PASS+stage=merged(外线) 落账）
        f, lb = FakeRC(), []
        g, stt = drive_anc(mt, f, lb, 0, {}, True)
        if len(f.verdicts) == 1 and f.verdicts[0][:2] == ("T-HPX", "PASS") and \
           g.get("extmerged") == 1 and stt["cards"].get("T-HPX", {}).get("stage") == "merged" \
           and any("EXT-VERIFIED" in x for x in lb):
            ok("T5a 缺账×外合=补锚+外线沙箱复验+verdict PASS 转正（不悬置等人工）")
        else:
            bad("T5a 缺账形态未闭环", g, f.verdicts, lb[:2])
        f2, lb2 = FakeRC(), []
        g2, st2 = drive_anc(mt_old, f2, lb2, 0, {}, True)
        if not f2.verdicts and "T-HPX" not in st2["cards"] and g2.get("extmerged", 0) == 0:
            ok("T5a-哨 撤针必悬置（增补前缺账形态零转正=撤修复必红实证）")
        else:
            bad("T5a-哨 撤针竟转正（哨空转假绿）", f2.verdicts, st2["cards"])
        # T5b refailed 窗内×外合：退避窗不再悬置，即走闭环
        def rf_rec():   # 每相现做新账（closure 就地 update 同引用，复用即污染后相=现证）
            return {"stage": "refailed", "commit": "f" * 40, "repo": "R", "fail_ts": time.time()}
        f3, lb3 = FakeRC(), []
        g3, st3 = drive_anc(mt, f3, lb3, 0, {"T-HPX": rf_rec()}, True)
        if len(f3.verdicts) == 1 and g3.get("extmerged") == 1 and \
           st3["cards"]["T-HPX"]["stage"] == "merged":
            ok("T5b refailed 窗内×外合=退避让位闭环（沙箱复验是终态非空转）")
        else:
            bad("T5b 窗内外合未转正", g3, f3.verdicts, st3["cards"].get("T-HPX"))
        f4, lb4 = FakeRC(), []
        g4, st4 = drive_anc(mt_old, f4, lb4, 0, {"T-HPX": rf_rec()}, True)
        if not f4.verdicts and st4["cards"]["T-HPX"]["stage"] == "refailed" and \
           g4.get("extmerged", 0) == 0:
            ok("T5b-哨 撤针窗内零出路（永悬 refailed 等人工=撤修复必红实证）")
        else:
            bad("T5b-哨 撤针竟有出路（哨空转假绿）", f4.verdicts, st4["cards"])
        # T5c refailed 窗内×未外合：维持旧退避零空转（防过度修正）
        f5, lb5 = FakeRC(), []
        g5, st5 = drive_anc(mt, f5, lb5, 1, {"T-HPX": rf_rec()}, True)
        if not f5.verdicts and not f5.calls and \
           st5["cards"]["T-HPX"]["stage"] == "refailed" and g5.get("extmerged", 0) == 0:
            ok("T5c 窗内未外合=旧退避分毫不动（零重验零转正零清账）")
        else:
            bad("T5c 过度修正：窗内未外合仍动了", f5.verdicts, f5.calls, st5["cards"])
        # T5d dry 形态：只点名零写零判（同环纪律）
        f6, lb6 = FakeRC(), []
        g6, st6 = drive_anc(mt, f6, lb6, 0, {}, False)
        if not f6.verdicts and "T-HPX" not in st6["cards"] and g6.get("extmerged", 0) == 0 \
           and any("(dry)交付已被外手合入" in x for x in lb6):
            ok("T5d dry=只点名零写零判（增补不破坏 dry 纪律）")
        else:
            bad("T5d dry 形态有写/判", f6.verdicts, st6["cards"], lb6[:1])
    finally:
        mt.sandbox_external, mt.sandbox_cleanup = _sbx, _cln

    shutil.rmtree(HOME, ignore_errors=True)
    shutil.rmtree(FAKE_SR, ignore_errors=True)
    if fails:
        print("=== TRAIN_HOSTPIN_FAIL n=%d ===" % len(fails))
        for x in fails:
            print("  RED:", x)
        sys.exit(1)
    print("TRAIN_HOSTPIN_PASS")


if __name__ == "__main__":
    main()
