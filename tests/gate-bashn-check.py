#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate-bashn-check — 发卡写闸「bash -n 判据目标须 .sh」专测（MECH-GATE-BASHN-1，02:5x fuse S3 轮记档转正）。
病根实锤：MECH-TERM-PLAIN-1 卡面曾挂 `bash -n status-web.py`——Python 源过 bash 语法闸必永红
（两种语言），AUTO-VERIFY 每圈空转把卡误归假红。新规：判据文本含 `bash -n <非.sh文件>` 时
create/set/start 三口统一 `_check_verify_bashn` 直接拒并归因；`bash <file>` 直跑
（含 .py 壳如 merge-train-check.py）与 `bash -n *.sh` 不受影响；嵌套在 bash -c 命令串里的
真毒照样抓到（文本级静态扫，不跑 shell）。

跑法：python -I cc-team/tests/gate-bashn-check.py → GATE-BASHN-GREEN n=15
红自证（变异实录，串行跑，不常驻本文件防自匹配假绿）：
  M1 摘闸段——_check_verify_bashn 函数体首行加 return（2026-11-04 REQ-VERIFY-DRYRUN-1 起静态闸
  被试跑前闸与三口两处复用，单摘调用点摘不干净，变异打函数体才全灭）：实测 RED fails=1——
  唯 G15 红（归因被试跑闸报文顶掉）；G01~G04 反被试跑闸兜住仍 rc=1（毒形在 tmp 必缺文件
  rc=127）=两闸互为纵深。恢复 GREEN n=15（本道 2026-11-04 复录，logs/evidence 见
  docs/evidence/req-verify-dryrun-1/mutation-m1.log）。
  M2 拆扫描——_verify_bashn_scan 开头 return []：实测 RED fails=10；
  M3 放宽目标——删 .sh 后缀判定：实测 RED fails=10；三者恢复后 GREEN n=15。
  红集=G01~G04+G10~G15（G11 被污染账红；G13 连坐红：毒卡先过闸把卡 CLAIMED，干净对照撞态机）。
G05~G09/G11/G13 是放行侧对照：闸若误伤直跑/通配/无 -n 形态，它们会红——两侧都锁，不是单向闸。
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

TMP = tempfile.mkdtemp(prefix="gatebashn-")
os.environ["CC_TEAM_HOME"] = TMP          # 必须在 import ccards 之前钉沙箱（同 lane-book-test 手法）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs(os.path.join(TMP, "state"))   # 无 registry.json=道册缺席，_check_lanes 跳过（沙箱通道）
from ccards import cli                       # noqa: E402


def run(*args):
    try:
        return cli.run(list(args))
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def run_cap(*args):
    """跑一条命令，同时收 stdout/stderr——归因文本落在 stderr（die 通道）。"""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = run(*args)
    return rc, out.getvalue(), err.getvalue()


def show_out(cid):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = run("show", cid)
    return rc, out.getvalue()


oks = []


def ck(name, got, want):
    oks.append((name, got == want, "got=%r want=%r" % (got, want)))


# ── 拒绝侧：判据含 bash -n <非.sh> 必须发卡闸红 ────────────────────────────
ck("G01create 挂 bash -n foo.py 必闸红",
   run("create", "GB-PY1", "--title", "t", "--verify", "bash -n foo.py"), 1)
ck("G02create 实锤病例形态（全路径 .py）必闸红",
   run("create", "GB-PY2", "--title", "t",
       "--verify", "bash -n cc-team/board/status-web.py"), 1)
ck("G03create 短选项连写 bash -nx foo.js 必闸红",
   run("create", "GB-PY3", "--title", "t", "--verify", "bash -nx foo.js"), 1)
ck("G04create 嵌套形态 bash -c 'bash -n foo.py' 必闸红（文本含毒即拒）",
   run("create", "GB-PY4", "--title", "t", "--verify", "bash -c 'bash -n foo.py'"), 1)

# ── 放行侧：直跑形态与 *.sh 语法闸不受影响 ────────────────────────────────
# REQ-VERIFY-DRYRUN-1 适配（2026-11-04）：三口现挂「发卡即考试」试跑闸——放行侧这些 .sh/直跑
# 目标在临时目录里本就不存在（rc=127 No such file），补 --files 声明同名缺失路径走 R2
# red-first 豁免；静态闸断言本体零改动（G01~G04/G10/G12 毒形仍在试跑之前被静态闸拒，归因不互糊）。
ck("G05create bash -n foo.sh 放行",
   run("create", "GB-OK1", "--title", "t", "--verify", "bash -n foo.sh",
       "--files", "foo.sh"), 0)
ck("G06create bash -n *.sh 通配放行",
   run("create", "GB-OK2", "--title", "t",
       "--verify", "bash -n cc-team/tests/*.sh", "--files", "cc-team/tests/*.sh"), 0)
ck("G07create bash merge-train-check.py 直跑 .py 壳放行",
   run("create", "GB-OK3", "--title", "t",
       "--verify", "bash cc-team/tests/merge-train-check.py",
       "--files", "cc-team/tests/merge-train-check.py"), 0)
ck("G08create 本卡现形判据（bash -c + python3 -I）放行",
   run("create", "GB-OK4", "--title", "t", "--verify",
       "bash -c 'cd \"$(git rev-parse --show-toplevel)\" && "
       "python3 -I cc-team/tests/gate-bashn-check.py'",
       "--files", "cc-team/tests/gate-bashn-check.py"), 0)
ck("G09create bash -c 'ls -n notes.txt' 不误伤（-n 属 ls 非 bash）",
   run("create", "GB-OK5", "--title", "t", "--verify", "bash -c 'ls -n notes.txt'",
       "--files", "notes.txt"), 0)

# ── set / start 同闸口 ────────────────────────────────────────────────────
run("create", "GB-KEEP", "--title", "t", "--verify", "bash -n keep.sh", "--files", "keep.sh")
ck("G10set 换毒判据 bash -n bad2.py 必闸红", run("set", "GB-KEEP", "--verify", "bash -n bad2.py"), 1)
rc_keep, txt_keep = show_out("GB-KEEP")
ck("G11被拒的 set 后卡面仍是旧判据（账上零污染）",
   ("keep.sh" in txt_keep) and ("bad2" not in txt_keep), True)
run("create", "GB-ST", "--title", "t", "--lanes", "m1", "--verify", "bash -n ok.sh",
    "--files", "ok.sh,ok4.sh")
ck("G12start 补毒判据 bash -n bad3.py 必闸红",
   run("start", "GB-ST", "--owner", "m1", "--verify", "bash -n bad3.py"), 1)
ck("G13start 对照（干净判据）放行——证明 G12 之红只归因于闸，不是环境噪音",
   run("start", "GB-ST", "--owner", "m1", "--verify", "bash -n ok4.sh"), 0)

# ── 零残留 + 归因 ─────────────────────────────────────────────────────────
ck("G14被拒的 GB-PY1 账上零残留", show_out("GB-PY1")[0] != 0, True)
rc_e, _o, err_e = run_cap("create", "GB-MSG", "--title", "t", "--verify", "bash -n foo.py")
ck("G15拒绝信息带归因（点名目标文件+病例出处）",
   ("foo.py" in err_e) and ("MECH-TERM-PLAIN-1" in err_e) and (".sh" in err_e), True)

bad = [t for t, ok, _ in oks if not ok]
for t, ok, hint in oks:
    print("%s %s%s" % ("PASS" if ok else "FAIL", t, ("｜" + hint) if not ok else ""))
if bad:
    print("GATE-BASHN-RED fails=%d" % len(bad))
    sys.exit(1)
print("GATE-BASHN-GREEN n=%d" % len(oks))
