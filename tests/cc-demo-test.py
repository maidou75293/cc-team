#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cc-demo-test.py — REQ-CC-DEMO-1 红绿锁：`cc demo` 一键自检的守卫。

差分对（守卫要红得起来的house规矩，同 oss-release-check / fuse-check 式）：
  T1 正跑必绿：rc=0、末行恰 DEMO_PASS、六环节 OK 行全在——且**零外部触碰**：
     把 CC_TEAM_HOME 指向带指纹的诱饵账根，跑完逐文件哈希必全等（a 验收=不碰真实账
     的机器化形态；真实账当然更够不着，因为 cmd_demo 本进程根本不 import ccards）。
  T2 断点必红：--break-at 逐环节注入 → rc≠0、末行恰点名 DEMO_FAIL: <环节名>、
     前序环节 OK 行在场、本环节 OK 行不在场、沙箱目录必已被自毁（成败必自毁=
     幂等 b 的硬断言）、诱饵账根仍全等。
  T3 连跑必绿：同一入口第二遍仍 DEMO_PASS（mkdtemp 全新、无固定路径=可重复）。
  T4 enforce 前瞻：CARD_TYPE_EPOCH/CARD_3FORM_EPOCH 拨到过去（三口闸 enforce 生效
     日到了的形态）→ 演示卡带全 --type/--src/--story/--plan/判据三要素，照跑绿。
  T5 静态不联网：demo 段全部源码（cmd_demo/_demo_*/_DEMO_LANE_SCRIPT）无
     http/urllib/socket/requests 字面；git 全本地子命令（无 remote/clone/fetch/push）。

跑法：python -I cc-team/tests/cc-demo-test.py   末行 CC_DEMO_PASS=锁绿。
"""
import hashlib
import importlib.util
import inspect
import os
import re
import subprocess
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "..", "ccards", "oss_cli.py")
STEPS = ("建沙箱", "立卡", "泳道并行", "交付合入", "机器验收", "自毁")

FAILS = []


def check(label, cond, detail=""):
    if cond:
        print("PASS: %s" % label)
    else:
        print("FAIL: %s%s" % (label, (" ｜" + detail) if detail else ""))
        FAILS.append(label)


def make_decoy():
    """诱饵账根：CC_TEAM_HOME 指它 + 指纹在册。demo 若敢写它一笔，指纹必不等。"""
    root = tempfile.mkdtemp(prefix="cc-demo-decoy-")
    os.makedirs(os.path.join(root, "state"), exist_ok=True)
    with open(os.path.join(root, "state", "graph.json"), "w", encoding="utf-8") as f:
        f.write('{"cards": {}}')
    return root


def fingerprint(root):
    fp = {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as f:
                fp[os.path.relpath(p, root).replace("\\", "/")] = hashlib.sha1(f.read()).hexdigest()
    return fp


def run_demo(extra_args=(), env_extra=None):
    decoy = make_decoy()
    before = fingerprint(decoy)
    env = dict(os.environ, CC_TEAM_HOME=decoy)
    env.pop("CC_AUTO_VERIFY", None)  # 会话注入不漏进被测面（lane 子程自己钉 =1）
    env.pop("CARD_MAINLINE", None)   # 演示沙箱主干=缺省 main 探测，宿主配置不连坐
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, "-I", CLI, "demo"] + list(extra_args),
                       env=env, cwd=decoy, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    after = fingerprint(decoy)
    shutil.rmtree(decoy, ignore_errors=True)
    return p.returncode, (p.stdout or "") + (p.stderr or ""), before == after


def last_line(out):
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# ── T1 正跑必绿 + 零外部触碰 ─────────────────────────────────────────────────
rc, out, untouched = run_demo()
check("T1 rc=0", rc == 0, "rc=%s" % rc)
check("T1 末行=DEMO_PASS", last_line(out) == "DEMO_PASS", "末行=%r" % last_line(out))
for i, name in enumerate(STEPS, 1):
    check("T1 环节OK行[%d %s]" % (i, name), "[%d/%d] %s OK" % (i, len(STEPS), name) in out)
check("T1 无 DEMO_FAIL 字样", "DEMO_FAIL" not in out)
check("T1 诱饵账根零触碰（不碰真实账）", untouched)

# ── T2 逐环节注入必红（差分对：摘掉任一环节的失败上报，这里就红） ────────────
for j, name in enumerate(STEPS):
    rc, out, untouched = run_demo(["--break-at", name])
    tail = last_line(out)
    check("T2[%s] rc≠0" % name, rc != 0, "rc=%s" % rc)
    check("T2[%s] 末行点名环节" % name, tail.startswith("DEMO_FAIL: %s" % name), "末行=%r" % tail)
    if j > 0:
        prev = STEPS[j - 1]
        check("T2[%s] 前序OK在场" % name, "[%d/%d] %s OK" % (j, len(STEPS), prev) in out)
    check("T2[%s] 本环节OK不在场" % name, ("[%d/%d] %s OK" % (j + 1, len(STEPS), name)) not in out)
    m = re.search(r"沙箱=(\S+)", out)
    if m:
        check("T2[%s] 失败也自毁零残留" % name, not os.path.isdir(m.group(1)), m.group(1))
    check("T2[%s] 诱饵账根零触碰" % name, untouched)

# ── T3 幂等可重复：第二遍照绿 ────────────────────────────────────────────────
rc, out, untouched = run_demo()
check("T3 二遍 rc=0", rc == 0, "rc=%s ｜tail=%s" % (rc, last_line(out)))
check("T3 二遍末行=DEMO_PASS", last_line(out) == "DEMO_PASS")
check("T3 二遍诱饵账根零触碰", untouched)

# ── T4 enforce 前瞻：三口闸生效日形态照绿（演示卡规格带全） ──────────────────
rc, out, untouched = run_demo(env_extra={"CARD_TYPE_EPOCH": "2020-01-01",
                                         "CARD_3FORM_EPOCH": "2020-01-01"})
check("T4 enforce 档 rc=0", rc == 0, "rc=%s" % rc)
check("T4 enforce 档末行=DEMO_PASS", last_line(out) == "DEMO_PASS", "末行=%r" % last_line(out))
check("T4 enforce 档零触碰", untouched)

# ── T5 静态：demo 段源码无联网面 + 子命令在册 ────────────────────────────────
spec = importlib.util.spec_from_file_location("cc_oss_cli_demo_probe", CLI)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
src = "\n".join([inspect.getsource(mod.cmd_demo), mod._DEMO_LANE_SCRIPT] +
                [inspect.getsource(f) for _n, f in sorted(vars(mod).items())
                 if _n.startswith("_demo_") and callable(f)])
for banned in ("http://", "https://", "urllib", "socket", "requests",
               "clone", "fetch", "push", "remote"):
    check("T5 demo 段无联网字样 %r" % banned, banned not in src)
check("T5 demo 子命令在册", '"demo"' in inspect.getsource(mod.main))
check("T5 环节名与锁面对齐（STEPS 漂移即红）", tuple(mod.DEMO_STEPS) == STEPS)

print("CC_DEMO_PASS" if not FAILS else "CC_DEMO_FAIL %d: %s" % (len(FAILS), "; ".join(FAILS)))
sys.exit(0 if not FAILS else 1)
