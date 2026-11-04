#!/usr/bin/env python3
# verify-bash-pin-check.py — MECH-VERIFYPIN-FALLBACK-1 红绿锁（卡面判据本体）。
# 病根：ccards/commands.py::_run_verify 旧缺省盲值 "bash"——win 任务形 PATH 下
#       which("bash") 命中 C:\Windows\System32\bash.exe（WSL 启动器），判据在毒宿主跑
#       （交接①活证 d0b97b29/6b6d0be0；MECH-VFY1 注释自证 11-01 劫持而默认值本身即毒径）。
# 手法（卡面规格）：沙箱 PATH 头插「假 System32」桩——把 SystemRoot 指到临时目录、
#       桩放该目录且 PATH 唯一成员，**旧逻辑必上桩跑**（R1 现场自证，防空检查）；
#       新逻辑断言不落毒宿主：取 Git Bash 或 die（宁红不猜），对照组无毒=正常不误杀。
#       变异红路：撤 SystemRoot 排除段→P1 红；撤候选回退→P2 红；撤显式毒检→P3 红；
#       盲值兜底行回归→P6 文本闸红（撤修复不赖环境，自造现场）。
# 跑法：python3 -I cc-team/tests/verify-bash-pin-check.py（exit 0=全绿；独立临时沙箱，
#       不碰正账；POSIX 宿主自动改跑「零动」分支组，win 分支组跳过并如实打 SKIP）
import io, os, shutil, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # cc-team 根
try:      # 宿主控制台 cp936 会炸 ↔/中文混排——输出流钉 UTF-8（双侧兼容，同 state_std 先例）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, str(detail)[:200])


def skip(name):
    print("SKIP:", name)


_home = tempfile.mkdtemp(prefix="cc-vbpin-home-")          # CC_TEAM_HOME 沙箱（不碰主仓账）
os.environ["CC_TEAM_HOME"] = _home
sys.path.insert(0, SRC)
from ccards import commands                                # noqa: E402


def capture(fn):
    """跑一次解析：返回 (rc, 值或None, 输出)。die=SystemExit(1) 同 shell 语义。"""
    buf = io.StringIO()
    o, e = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        v = fn()
        rc = 0
    except SystemExit as ex:
        v, rc = None, (ex.code if isinstance(ex.code, int) else 1)
    except BaseException as ex:
        v, rc, _ = None, 98, buf.write("EXC %r" % ex)
    finally:
        sys.stdout, sys.stderr = o, e
    return rc, v, buf.getvalue()


def under(path, root):
    return path and os.path.normpath(path).lower().startswith(os.path.normpath(root).lower())


# ── 沙箱现场：假 System32（毒）+ clean_bin（正常）各放 bash.exe 桩 ──
_fake_sr = tempfile.mkdtemp(prefix="cc-vbpin-fakesr-")
_clean = tempfile.mkdtemp(prefix="cc-vbpin-clean-")
for _d in (_fake_sr, _clean):
    with open(os.path.join(_d, "bash.exe"), "wb") as f:
        f.write(b"#!/bin/sh\nexit 0\n")
_poison = os.path.join(_fake_sr, "bash.exe")
_cleanstub = os.path.join(_clean, "bash.exe")

_ENV_KEYS = ("PATH", "SystemRoot", "CARD_VERIFY_BASH")
_save = {k: os.environ.get(k) for k in _ENV_KEYS}


def scenario(path_dirs, systemroot, explicit=None):
    os.environ["PATH"] = os.pathsep.join(path_dirs)
    if systemroot is not None:
        os.environ["SystemRoot"] = systemroot
    if explicit is None:
        os.environ.pop("CARD_VERIFY_BASH", None)
    else:
        os.environ["CARD_VERIFY_BASH"] = explicit


def restore_env():
    for k, v in _save.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


try:
    if os.name == "nt":
        # P1 缺省+毒宿主在 PATH 头：必须不落毒桩——取 Git Bash 或 die（卡面判据本体）
        scenario([_fake_sr], _fake_sr)
        rc, val, out = capture(commands._resolve_verify_bash)
        if shutil.which("bash") and under(shutil.which("bash"), _fake_sr):
            ok("R1 现场自证：本沙箱 which('bash') 命中假 System32 桩（旧逻辑必上毒宿主=空检查防住）")
        else:
            bad("R1 现场自证：毒桩未被 which('bash') 命中，沙箱失效", shutil.which("bash"))
        _have_git = any(os.path.isfile(c.replace("/", os.sep)) for c in commands._git_bash_candidates())
        if rc == 0 and val:
            if under(val, _fake_sr):
                bad("P1 缺省毒径被采用（盲值兜底回归）!", val)
            elif os.path.isfile(val):
                ok("P1 缺省+毒 PATH→回退干净 Git Bash（%s）" % val)
            else:
                bad("P1 回退值不是可用文件", val)
        elif rc == 1 and not _have_git:
            ok("P1 缺省+毒 PATH+本机确无 Git Bash→die 宁红不猜（合法红）")
        elif rc == 1:
            bad("P1 本机有 Git Bash 却 die（候选回退被变异成直死）!", out[:120])
        else:
            bad("P1 异常 rc", (rc, out[:120]))

        # P2 缺省+毒+Git Bash 候选全缺 = die（撤候选回退变异必红于此）
        _orig_cands = commands._git_bash_candidates
        commands._git_bash_candidates = lambda: [os.path.join(_clean, "nope-bash.exe")]
        try:
            scenario([_fake_sr], _fake_sr)
            rc, val, out = capture(commands._resolve_verify_bash)
            if rc == 1 and "宁红不猜" in out:
                ok("P2 候选全缺=die（宁红不猜不猜宿主）")
            else:
                bad("P2 候选全缺未 die", (rc, str(val)[:80], out[:120]))
        finally:
            commands._git_bash_candidates = _orig_cands

        # P3 显式 CARD_VERIFY_BASH 指毒桩 = die 不改派（撤显式毒检变异必红于此）
        scenario([_fake_sr], _fake_sr, explicit=_poison)
        rc, val, out = capture(commands._resolve_verify_bash)
        if rc == 1 and "宁红不猜" in out:
            ok("P3 显式配而指毒=die（人手错配必须喊，不静默改派）")
        else:
            bad("P3 显式毒配未被拒", (rc, str(val)[:80], out[:120]))

        # P4 对照组 unset 无毒=正常不误杀（PATH 只含 clean_bin）
        scenario([_clean], _fake_sr)
        rc, val, out = capture(commands._resolve_verify_bash)
        if rc == 0 and val and under(shutil.which(val) or val, _clean):
            ok("P4 对照：缺省+无毒 PATH→正常取用（健康宿主不误杀）%s" % val)
        else:
            bad("P4 对照组异常", (rc, str(val)[:80], out[:120]))

        # P5 对照组显式配干净值=原样透传（配而不得=红的旧语义不回归破坏）
        scenario([_clean], _fake_sr, explicit=_cleanstub)
        rc, val, out = capture(commands._resolve_verify_bash)
        if rc == 0 and val == _cleanstub:
            ok("P5 对照：显式干净值原样透传 %s" % val)
        else:
            bad("P5 显式干净值被错拒/改写", (rc, str(val)[:80], out[:120]))

        # P6 文本闸：盲值兜底行不许回归 _run_verify（MECH-VERIFYPIN-FALLBACK-1 本体锚）
        _txt = io.open(os.path.join(SRC, "ccards", "commands.py"), encoding="utf-8").read()
        if 'os.environ.get("CARD_VERIFY_BASH", "bash")' in _txt:
            bad("P6 盲值兜底行回归 commands.py!", "")
        elif "_resolve_verify_bash()" in _txt and "def _run_verify" in _txt:
            ok("P6 commands.py 无盲值兜底行且 _run_verify 走 _resolve_verify_bash")
        else:
            bad("P6 收口锚丢失", "")
    else:
        # POSIX 零动组：win 规矩一毫不沾（P1-P6 打 SKIP，win 面由 win 宿主复跑覆盖）
        for n in ("P1 缺省毒回退", "P2 候选全缺die", "P3 显式毒die", "P4 对照无毒", "P5 显式透传", "P6 盲值文本闸"):
            skip("%s（win-only 分支，POSIX 宿主）" % n)
        scenario([os.environ.get("PATH", "/usr/bin")], None)
        rc, val, out = capture(commands._resolve_verify_bash)
        if rc == 0 and val == "bash":
            ok("U1 POSIX 缺省=原样 'bash'（零动）")
        else:
            bad("U1 POSIX 缺省漂移", (rc, str(val)[:60], out[:120]))
        _b = shutil.which("bash")
        if _b:
            scenario([os.environ.get("PATH", "/usr/bin")], None, explicit=_b)
            rc, val, out = capture(commands._resolve_verify_bash)
            if rc == 0 and val == _b:
                ok("U2 POSIX 显式干净值透传（零动）")
            else:
                bad("U2 POSIX 显式透传破坏", (rc, str(val)[:60]))
            scenario([os.environ.get("PATH", "/usr/bin")], r"C:\Windows", explicit=_b)
            rc, val, out = capture(commands._resolve_verify_bash)
            if rc == 0 and val == _b:
                ok("U3 POSIX 不受 SystemRoot/毒径逻辑沾染（win 规矩零外溢）")
            else:
                bad("U3 SystemRoot 检查外溢 POSIX!", (rc, str(val)[:60]))
        else:
            skip("U2/U3（POSIX 宿主无 bash，显式面无法自证）")
finally:
    restore_env()

shutil.rmtree(_home, ignore_errors=True)
shutil.rmtree(_fake_sr, ignore_errors=True)
shutil.rmtree(_clean, ignore_errors=True)
print("=== verify-bash-pin fails=%d ===" % len(fails))
print("VERIFY_BASH_PIN_PASS" if not fails else "VERIFY_BASH_PIN_FAIL: " + "; ".join(fails[:8]))
sys.exit(0 if not fails else 1)
