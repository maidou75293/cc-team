#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""win-stdin-hook-test.py — MECH-HOOK-STDIN-WIN-1 Windows 原生侧复跑锁。

镜像 selftest 33①/34②/36⑤/79a~79f 的判据语义（rc/NOTE/EXIT-RELEASE/状态逐项对齐），
把「Stop hook stdin JSON 在 Win 宿主真实可读」钉成可重跑的实证：select() 在 nt 上只认
socket，check_exit.hook_input 旧版对管道 stdin 抛 OSError(10038/10093) 被 except 吞掉
=sid 恒 None，①②NOTE 放行与 79b-f 散场交账在 Windows 原生控制面全线失效（卡面判据
「防 WSL 假绿」即为此设）。本锁只在 nt 跑（POSIX 面归 selftest.sh；混宿主=假绿/假红错位）。

用法：
  python3 cc-team/tests/win-stdin-hook-test.py                # 跑本树修复版
  python3 cc-team/tests/win-stdin-hook-test.py --script <路径>  # 红检：指未修复旧版
  红检基线取法（不动工作树）：
    git archive HEAD cc-team | tar -x -C /tmp/hook-old && \
    python3 cc-team/tests/win-stdin-hook-test.py --script /tmp/hook-old/cc-team/ops/check_exit.py
  修复态预期：stdin 依赖线（①放行/②放行/79b/79c/79e/79f）全绿；
  旧码态预期：仅这几条红、兼容线（⑤/79a/79d）照绿——「恰红只红其位」=守卫红得起来。

尾行 WIN_STDIN_HOOK_PASS fails=0=绿；否则 WIN_STDIN_HOOK_FAIL fails=N，rc=1。
"""
import argparse, json, os, shutil, subprocess, sys, tempfile

def die(msg):
    print("HARNESS-ABORT:", msg)
    sys.exit(2)

if os.name != "nt":
    die("本锁测 Windows 原生语义(Win32 select 替代/Toolhelp32 族谱)，非 nt 宿主拒判"
        "——POSIX 面归 selftest.sh，混跑=假绿")

ap = argparse.ArgumentParser()
ap.add_argument("--script", default="", help="被测 check_exit.py 路径（缺省=本树）")
a = ap.parse_args()
HERE = os.path.dirname(os.path.abspath(__file__))
CC = os.path.dirname(HERE)                                  # cc-team 根
CE = a.script or os.path.join(CC, "ops", "check_exit.py")
CARD = os.path.join(CC, "cards_dag.py")
if not os.path.isfile(CE):
    die("被测脚本不存在: %s" % CE)
PY = sys.executable

TEAM = tempfile.mkdtemp(prefix="winhook-team-")
REPO = tempfile.mkdtemp(prefix="winhook-repo-")
fails = []

def ok(name):
    print("PASS: %s" % name)

def bad(name, detail=""):
    print("FAIL: %s %s" % (name, detail))
    fails.append(name)

def base_env(**kw):
    e = dict(os.environ)
    e["CC_TEAM_HOME"] = TEAM
    e.pop("CLAW_SESSION_ID", None)
    e.update(kw)
    return e

def card(*args, **envkw):
    r = subprocess.run([PY, "-I", CARD] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env=base_env(**envkw), timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def hook(owner, payload=None, devnull=False):
    """payload=None 且非 devnull=喂空管道(写完即关)，devnull=无 stdin 手跑档。"""
    kw = {}
    if payload is not None:
        kw["input"] = payload
    elif devnull:
        kw["stdin"] = subprocess.DEVNULL
    else:
        kw["input"] = ""
    r = subprocess.run([PY, CE, "--owner", owner, "--repo", REPO],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=base_env(), timeout=90, **kw)
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def status(cid):
    g = json.load(open(os.path.join(TEAM, "state", "graph.json"), encoding="utf-8"))
    return ((g.get("cards") or {}).get(cid) or {}).get("status")

def claimjson(cid):
    p = os.path.join(TEAM, "claims", cid, "claim.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}

def reap_pid(cid, owner, me):
    """selftest 79b/79c 驱动段等价：把 claim 锚与道锁 meta 的 pid 改成驱动进程=hook 之父。"""
    for p in (os.path.join(TEAM, "claims", cid, "claim.json"),
              os.path.join(TEAM, "state", "lane-%s.lock" % owner, "meta.json")):
        try:
            j = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        j["pid"] = me
        with open(p, "w", encoding="utf-8") as f:
            json.dump(j, f, ensure_ascii=False)

def recycled_log():
    p = os.path.join(TEAM, "logs", "recycle.log")
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return ""

# ---- 环境：临时台账 + 临时 git 仓（init+空提交），check_exit 走 --repo 判据 ----
if subprocess.run(["git", "-C", REPO, "init", "-q"], timeout=60).returncode != 0:
    die("git init 临时仓失败")
subprocess.run(["git", "-C", REPO, "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "init", "--allow-empty"], timeout=60)

# ---- ① (selftest 33)：异会话退出不拦(NOTE 放行)/本会话照结 ----
card("create", "0240", "--title", "t", "--verify", "true")
rc, _ = card("claim", "0240", "--owner", "ss1", CLAW_SESSION_ID="SID-A")
if rc != 0:
    die("claim 0240 失败")
rc, out = hook("ss1", '{"session_id":"SID-B"}')
if rc == 0 and "NOTE" in out:
    ok("①旧会话残卡不拦新会话退出(NOTE 交 recycle)")
else:
    bad("①异会话应 NOTE 放行 rc=0", "got rc=%s out=%s" % (rc, out[:120]))
rc, out = hook("ss1", '{"session_id":"SID-A"}')
if rc == 2:
    ok("①本会话残卡照 block(会话键结账)")
else:
    bad("①本会话应 block", "got rc=%s" % rc)

# ---- ② (selftest 34)：换绑新会话后旧 session 退出不结新卡 ----
card("release", "0240")
card("claim", "0240", "--owner", "ss1", CLAW_SESSION_ID="SID-B")
rc, out = hook("ss1", '{"session_id":"SID-A"}')
if rc == 0:
    ok("②旧会话退出不拦新会话在制卡")
else:
    bad("②旧 session 误拦", "got rc=%s out=%s" % (rc, out[:120]))
rc, out = hook("ss1", '{"session_id":"SID-B"}')
if rc == 2:
    ok("②新会话自己的账照结")
else:
    bad("②新会话未结账", "got rc=%s" % rc)
card("release", "0240")

# ---- ⑤ (selftest 36)：兼容档零变化——空 stdin/非 JSON/无 session_id 字段=旧 --owner 档 ----
card("create", "0244", "--title", "t", "--verify", "true")
card("claim", "0244", "--owner", "ss4")
if claimjson("0244").get("session_id") == "-":
    ok("⑤env 未设落 '-' 占位")
else:
    bad("⑤占位 '-' 未落", str(claimjson("0244").get("session_id")))
for tag, kw in (("空 stdin", dict(devnull=True)),
                ("非 JSON", dict(payload="not-json-at-all")),
                ("JSON 无 session_id", dict(payload='{"hook_event_name":"Stop"}'))):
    rc, _ = hook("ss4", **kw)
    if rc == 2:
        ok("⑤%s 退回 --owner 模式照 block" % tag)
    else:
        bad("⑤%s 应 block" % tag, "got rc=%s" % rc)
card("release", "0244")
rc, _ = hook("ss9", devnull=True)
if rc == 0:
    ok("⑤退回模式无 claim 照旧放行")
else:
    bad("⑤退回模式误拦空泳道", "got rc=%s" % rc)

# ---- 79a：散场档但锚 pid 非本会话族谱(假死 pid=999998，双侧必死)→照 block 不偷账 ----
card("create", "0751", "--title", "散场负锁", "--verify", "true")
card("claim", "0751", "--owner", "ex75a", "--pid", "999998")
rc, out = hook("ex75a", '{"session_id":"SID-75A","stop_hook_active":true,"hook_event_name":"Stop"}')
if rc == 2 and status("0751") == "CLAIMED" and "EXIT-RELEASE 0751" not in recycled_log():
    ok("79a 锚非本会话族谱→散场档照 block(不偷账)，无 EXIT-RELEASE 痕")
else:
    bad("79a", "rc=%s status=%s" % (rc, status("0751")))

# ---- 79b：族谱直证(驱动进程=hook 之父)+stop_hook_active → release 交账回 OPEN ----
card("create", "0752", "--title", "散场正锁", "--verify", "true", "--lanes", "ex75b")
card("claim", "0752", "--owner", "ex75b", "--pid", "999998")
reap_pid("0752", "ex75b", os.getpid())
rc, out = hook("ex75b", '{"session_id":"SID-75B","stop_hook_active":true,"hook_event_name":"Stop"}')
if rc == 0 and "EXIT-RELEASE 0752" in out and "EXIT-RELEASE 0752" in recycled_log() \
        and status("0752") == "OPEN" \
        and not os.path.isdir(os.path.join(TEAM, "claims", "0752")) \
        and not os.path.exists(os.path.join(TEAM, "state", "lane-ex75b.lock")):
    ok("79b 族谱证成→当场交账回 OPEN(EXIT-RELEASE 双写+双锁清)")
else:
    bad("79b", "rc=%s status=%s out=%s" % (rc, status("0752"), out[:160]))

# ---- 79c：SessionEnd 档同样触发自查交账 ----
card("create", "0753", "--title", "散场终结钩", "--verify", "true")
card("claim", "0753", "--owner", "ex75c", "--pid", "999998")
reap_pid("0753", "ex75c", os.getpid())
rc, out = hook("ex75c", '{"session_id":"SID-75C","hook_event_name":"SessionEnd"}')
if rc == 0 and status("0753") == "OPEN" and "EXIT-RELEASE 0753" in recycled_log():
    ok("79c SessionEnd 当场交账(终结钩不空等)")
else:
    bad("79c", "rc=%s status=%s out=%s" % (rc, status("0753"), out[:160]))

# ---- 79d：无 stdin 手跑=旧档照 block；首次 Stop(无 stop_hook_active)不吃散场档 ----
card("create", "0754", "--title", "首停照拦", "--verify", "true")
card("claim", "0754", "--owner", "ex75d", "--pid", "999998")
rc, _ = hook("ex75d", devnull=True)
if rc == 2:
    ok("79d 无 stdin 手跑=旧档照 block")
else:
    bad("79d 无 stdin", "got rc=%s" % rc)
rc, out = hook("ex75d", '{"hook_event_name":"Stop","session_id":"SID-75D"}')
if rc == 2 and "散场自查" in out and status("0754") == "CLAIMED":
    ok("79d 首次 Stop 不缴在制卡，block 文点名散场通道")
else:
    bad("79d 首停", "rc=%s status=%s" % (rc, status("0754")))
card("release", "0754")

# ---- 79e：无锚 pid 但 sid 同值直证 → 散场交账（回收环救不了的批在此路被救） ----
card("create", "0755", "--title", "无锚散场", "--verify", "true")
card("claim", "0755", "--owner", "ex75e", CLAW_SESSION_ID="SID-75E")
rc, out = hook("ex75e", '{"session_id":"SID-75E","stop_hook_active":true,"hook_event_name":"Stop"}')
if rc == 0 and status("0755") == "OPEN" and "EXIT-RELEASE 0755" in recycled_log():
    ok("79e 无锚 pid 但 sid 同值→散场交账放行")
else:
    bad("79e", "rc=%s status=%s out=%s" % (rc, status("0755"), out[:160]))

# ---- 79f：异会话 NOTE 先于交账——散场档也不碰别人会话的账 ----
card("create", "0756", "--title", "异会话不碰", "--verify", "true")
card("claim", "0756", "--owner", "ex75f", CLAW_SESSION_ID="SID-OWN")
rc, out = hook("ex75f", '{"session_id":"SID-OTHER","stop_hook_active":true,"hook_event_name":"Stop"}')
if rc == 0 and "NOTE" in out and status("0756") == "CLAIMED":
    ok("79f 异会话散场不拦也不交(先 NOTE 后自查的顺序锁)")
else:
    bad("79f", "rc=%s status=%s out=%s" % (rc, status("0756"), out[:160]))
card("release", "0756")

shutil.rmtree(TEAM, ignore_errors=True)
shutil.rmtree(REPO, ignore_errors=True)
if fails:
    print("WIN_STDIN_HOOK_FAIL fails=%d :: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("WIN_STDIN_HOOK_PASS fails=0（①②/⑤/79a~f Win 原生全绿，CE=%s）" % CE)
