#!/usr/bin/env python3
# cc-team/tests/fuse-check.py — REQ-FLEET-FUSE-1 断流保险丝红绿锁。
# 六场景各注入一段断流，断言**对应自动处置发生**；再用 FUSE_NOACT 摘掉该段处置重跑，
# 断言处置**不出现**（=「撤任一处置必红」：断言盯的是处置本体，处置没了断言必红）。
# 全程沙箱：CC_TEAM_HOME=临时台账家、主干仓=临时 git 仓、重启/唤醒/探针=桩脚本落 marker。
# 不碰真账、不重启真服务、不拉真 PM 会话（真链路实跑归 dry 一圈留证据，见证据文档）。
# 用法：python -I cc-team/tests/fuse-check.py          # 场景全绿=PASS（卡面判据）
#       python -I cc-team/tests/fuse-check.py --units  # 只跑纯判据单元面
import json, os, re, shutil, subprocess, sys, tempfile, time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
TEAM = os.path.dirname(HERE)                       # cc-team 根
FUSE = os.path.join(TEAM, "ops", "fuse.py")
CARDS = os.path.join(TEAM, "cards_dag.py")
PY = sys.executable
RESULTS = []


def ck(name, cond, note=""):
    RESULTS.append((name, bool(cond), note))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, ("｜" + note) if note else ""))


# ---------------- 沙箱基座 ----------------

def sandbox(tag):
    base = tempfile.mkdtemp(prefix="fuse-sb-%s-" % tag)
    repo = os.path.join(base, "repo")
    home = os.path.join(repo, "cc-team")
    for d in ("state", "logs", "ops"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    g(["init", "-b", "main", repo])
    g(["-C", repo, "config", "user.name", "sb"], check=False)
    g(["-C", repo, "config", "user.email", "sb@x"], check=False)
    w(os.path.join(repo, "README.md"), "seed\n")
    g(["-C", repo, "add", "."])
    g(["-C", repo, "commit", "-m", "seed"])
    # 桩：重启=写 marker；唤醒=写 marker+reason；探针=打固定 epoch
    stubs = os.path.join(base, "stubs")
    os.makedirs(stubs, exist_ok=True)
    w(os.path.join(stubs, "restart.py"),
      "import os,time\n"
      "open(os.environ['FUSE_STUB_RESTART'],'a',encoding='utf-8').write('RESTART %.3f\\n'%time.time())\n"
      "raise SystemExit(int(os.environ.get('FUSE_STUB_RESTART_RC') or 0))\n")
    w(os.path.join(stubs, "wake.py"),
      "import os\n"
      "open(os.environ['FUSE_STUB_WAKE'],'a',encoding='utf-8')\\\n"
      "    .write('WAKE|%s\\n'%os.environ.get('PM_WAKE_REASON','').replace('\\n',' '))\n"
      "raise SystemExit(int(os.environ.get('FUSE_STUB_WAKE_RC') or 0))\n")
    w(os.path.join(stubs, "probe.py"),
      "import os\nprint(os.environ.get('FUSE_STUB_PROC_START','0'))\n")
    return {"base": base, "repo": repo, "home": home, "stubs": stubs}


def w(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def r(p):
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return ""


def g(args, check=True):
    p = subprocess.run(["git"] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("git %s rc=%d %s" % (" ".join(args), p.returncode, (p.stderr or p.stdout)[:200]))
    return p.returncode, (p.stdout or "").strip()


def gate(sb, *args):
    env = dict(os.environ)
    env["CC_TEAM_HOME"] = sb["home"]
    p = subprocess.run([PY, "-I", CARDS] + [str(x) for x in args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def make_done_card(sb, cid, commit):
    repo = sb["repo"]
    rc, out = gate(sb, "create", cid, "--title", "沙箱卡%s" % cid, "--verify", "true",
                   "--lanes", "m1", "--status", "OPEN")
    assert rc == 0, out
    rc, out = gate(sb, "claim", cid, "--owner", "m1")
    assert rc == 0, out
    m = re.search(r"e(\d+)", out)
    ep = m.group(1) if m else None
    args = ["done", cid, "--owner", "m1", "--repo", repo, "--commit", commit]
    if ep:
        args += ["--epoch", ep]
    rc, out = gate(sb, *args)
    assert rc == 0 and "DONE" in out, out


def fuse_env(sb, extra=None):
    env = dict(os.environ)
    env.update({
        "CC_TEAM_HOME": sb["home"],
        "PYTHONIOENCODING": "utf-8",
        "FUSE_MAIN_REPO": sb["repo"],
        "FUSE_MAIN_REF": "main",
        "FUSE_HB": os.path.join(sb["base"], "hb.txt"),
        "FUSE_LOGS": os.path.join(sb["home"], "logs", "dispatch.log"),
        "FUSE_RESTART_CMD": '"%s" -I "%s"' % (PY, os.path.join(sb["stubs"], "restart.py")),
        "FUSE_PM_WAKE_CMD": '"%s" -I "%s"' % (PY, os.path.join(sb["stubs"], "wake.py")),
        "FUSE_PROBE_START_CMD": '"%s" -I "%s"' % (PY, os.path.join(sb["stubs"], "probe.py")),
        "FUSE_STUB_RESTART": os.path.join(sb["base"], "restart.calls"),
        "FUSE_STUB_WAKE": os.path.join(sb["base"], "wake.calls"),
        # 节流/冷却全放 0：场景内多次跑圈不互相挡（红检复用同沙箱）
        "FUSE_RESTART_THROTTLE_MIN": "0",
        "FUSE_WAKE_MIN_INTERVAL_MIN": "0",
    })
    if not os.path.isfile(env["FUSE_HB"]):
        w(env["FUSE_HB"], "%s hb\n" % time.strftime("%F %T"))   # 默认 S1 绿，隔离他段；场景已布的陈旧心跳不许覆盖
    if extra:
        env.update(extra)
    # 自卡：段级痕(seg_event)必须挂真实卡号——伪卡号 FUSE 会被 ops/reconcile_events.py
    # 判据一对成永久幽灵卡（00:23 真账实锤）。沙箱账先造同名自卡，锁才能验到这条通路。
    if not os.path.exists(os.path.join(sb["base"], ".selfcard")):
        rc, out = gate(sb, "create", "REQ-FLEET-FUSE-1", "--title", "断流保险丝(沙箱自账)",
                       "--verify", "true", "--lanes", "fuse", "--status", "OPEN")
        assert rc == 0, out
        w(os.path.join(sb["base"], ".selfcard"), "1")
    return env


def run_fuse(sb, extra=None, noact=None):
    env = fuse_env(sb, extra)
    if noact:
        env["FUSE_NOACT"] = noact
    p = subprocess.run([PY, "-I", FUSE, "--apply"], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=240)
    return p.returncode, ((p.stdout or "") + "\n" + (p.stderr or ""))


def run_fuse_dry(sb, extra=None):
    """不带 --apply 的一圈：按契约必须「只报不落账」。"""
    env = fuse_env(sb, extra)
    p = subprocess.run([PY, "-I", FUSE], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=240)
    return p.returncode, ((p.stdout or "") + "\n" + (p.stderr or ""))


def events(sb, needle):
    return [l for l in r(os.path.join(sb["home"], "state", "events.log")).splitlines()
            if needle in l and "|fuse|" in l]


def ev_count(sb, needle):
    """按「新增痕」断言用——同沙箱多圈跑，历史痕不清零，出现次数只增不减；
    红检=比较计数不增，绝不能用「不存在」（旧圈留过=永远存在，断言会假红）。"""
    return len(events(sb, needle))


def alerts_keys(sb):
    try:
        d = json.load(open(os.path.join(sb["home"], "state", "alerts.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return []
    a = (d or {}).get("alerts") or {}
    return [k for k, e in a.items() if not e.get("ack") or (e.get("last") or "") > e["ack"]]


def card(sb, cid):
    try:
        d = json.load(open(os.path.join(sb["home"], "state", "graph.json"), encoding="utf-8"))
        return (d.get("cards") or {}).get(cid) or {}
    except (OSError, ValueError):
        return {}


def lines_of(p):
    try:
        return [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]
    except OSError:
        return []


def card_ids(sb):
    """沙箱账全部卡号（幽灵卡锁的分母）。"""
    try:
        d = json.load(open(os.path.join(sb["home"], "state", "graph.json"), encoding="utf-8"))
        return set((d.get("cards") or d.get("nodes") or {}).keys())
    except (OSError, ValueError):
        return set()


# ---------------- 五场景 ----------------

def sc_s1(sb):
    """注入：派发心跳 4min 没续 → 断言自动重启发生；重启失败 → 叫 PM 发生；撤处置必红。"""
    hb = sb_env_hb(sb)
    w(hb, "%s hb\n" % time.strftime("%F %T"))
    os.utime(hb, (time.time() - 240, time.time() - 240))
    rc, out = run_fuse(sb)
    n_r = len(lines_of(os.path.join(sb["base"], "restart.calls")))
    ck("S1 心跳断流→自动重启发生（撤必红锁）",
       n_r >= 1 and events(sb, "FUSE-RESTART ok"), "restart=%s out=%s" % (n_r, tail(out)))
    # 重启失败 → 叫 PM
    w(os.path.join(sb["base"], "restart.calls"), "")
    rc, out = run_fuse(sb, {"FUSE_STUB_RESTART_RC": "1"})
    ck("S1 重启失败→叫PM（救不动逐级叫人）",
       events(sb, "FUSE-RESTART-FAIL") and "WAKE|S1" in r(os.path.join(sb["base"], "wake.calls")),
       tail(out))
    # 红检：摘 s1 → 重启与叫PM都不发生（按增量断言，历史痕不算）
    c0r = ev_count(sb, "FUSE-RESTART ok")
    c0w = ev_count(sb, "FUSE-S1-PM-WAKE")
    w(os.path.join(sb["base"], "restart.calls"), "")
    w(os.path.join(sb["base"], "wake.calls"), "")
    rc, out = run_fuse(sb, noact="s1")
    ck("S1 撤处置必红（NOACT=s1 无重启无叫PM）",
       not lines_of(os.path.join(sb["base"], "restart.calls"))
       and not lines_of(os.path.join(sb["base"], "wake.calls"))
       and ev_count(sb, "FUSE-RESTART ok") == c0r and ev_count(sb, "FUSE-S1-PM-WAKE") == c0w,
       tail(out))
    # dry 只报不落账锁（00:12 首圈真账 dry 实锤此洞：dry 也写了红灯+events 痕，修后上锁）：
    # 缺省一圈=检测照报，但 零处置/零 fuse 事件行/零告警键。
    os.utime(sb_env_hb(sb), (time.time() - 240, time.time() - 240))
    e_all = len(events(sb, "FUSE"))
    a_keys = len(alerts_keys(sb))
    w(os.path.join(sb["base"], "restart.calls"), "")
    rc, out = run_fuse_dry(sb)
    ck("S1 dry 只报不落账（零处置零事件零红灯，圈迹照打）",
       "DRY[s1]" in out and not lines_of(os.path.join(sb["base"], "restart.calls"))
       and len(events(sb, "FUSE")) == e_all and len(alerts_keys(sb)) == a_keys, tail(out))


def sb_env_hb(sb):
    return os.path.join(sb["base"], "hb.txt")


def sc_s2(sb):
    """注入：同卡连续 2 次 WORKER-DIED → 断言挂 STOPLOSS(停盲派)+叫PM；单死不误伤；撤必红。"""
    rc, out = gate(sb, "create", "REQ-T-DEATH", "--title", "即死沙箱卡", "--verify", "true",
                   "--lanes", "m1", "--status", "OPEN")
    assert rc == 0, out
    rc, out = gate(sb, "claim", "REQ-T-DEATH", "--owner", "m1")
    assert rc == 0, out
    dl = os.path.join(sb["home"], "logs", "dispatch.log")
    now = time.time()
    t1 = time.strftime("%F %T", time.localtime(now - 50 * 60))
    t2 = time.strftime("%F %T", time.localtime(now - 2 * 60))
    w(dl, "%s WORKER-DIED REQ-T-DEATH@m1：起班 pid=111 双册查无——死因留痕 x.log；立刻 release 回队\n"
         "%s WORKER-DIED REQ-T-DEATH@m1：起班 pid=222 双册查无——死因留痕 y.log；立刻 release 回队\n" % (t1, t2))
    rc, out = run_fuse(sb)
    c = card(sb, "REQ-T-DEATH")
    ck("S2 连续2即死→STOPLOSS挂起停盲派（撤必红锁）",
       c.get("status") == "HOLD" and "STOPLOSS:fuse" in str(c.get("hold") or "")
       and events(sb, "FUSE-S2-STOPLOSS") and any("STOPLOSS(FUSE-S2)" in k for k in alerts_keys(sb)),
       "status=%s out=%s" % (c.get("status"), tail(out)))
    ck("S2 STOPLOSS 后叫 PM 发生",
       "S2 停毒" in r(os.path.join(sb["base"], "wake.calls")), tail(out))
    evl = r(os.path.join(sb["home"], "state", "events.log")).splitlines()
    ck("S2 STOPLOSS 自 set 痕带 self=fuse 显标（gate→env→cmd_set 端到端；摘标记必红）",
       any(len(l.split("|")) >= 5 and l.split("|")[3].strip() == "PM" and "|set" in l
           and l.rstrip().endswith("self=fuse") for l in evl), tail(out))
    # 红检：摘 s2 → 新卡同样二连死但不处置
    rc, out = gate(sb, "create", "REQ-T-DEATH2", "--title", "即死2", "--verify", "true",
                   "--lanes", "m1", "--status", "OPEN")
    assert rc == 0, out
    rc, out = gate(sb, "claim", "REQ-T-DEATH2", "--owner", "m1")
    assert rc == 0, out
    with open(dl, "a", encoding="utf-8") as f:
        f.write("%s WORKER-DIED REQ-T-DEATH2@m1：pid=333 死\n"
                "%s WORKER-DIED REQ-T-DEATH2@m1：pid=444 死\n" % (t1, t2))
    w(os.path.join(sb["base"], "wake.calls"), "")
    c0 = ev_count(sb, "FUSE-S2-STOPLOSS")
    rc, out = run_fuse(sb, noact="s2")
    c2 = card(sb, "REQ-T-DEATH2")
    ck("S2 撤处置必红（NOACT=s2 不挂STOPLOSS不叫PM）",
       c2.get("status") == "CLAIMED" and ev_count(sb, "FUSE-S2-STOPLOSS") == c0
       and "REQ-T-DEATH2" not in r(os.path.join(sb["base"], "wake.calls")),
       "status=%s" % c2.get("status"))
    # 单死不误伤 + 成功成迹打断「连续」。DEATH3 走 m3：DEATH2 此刻仍 CLAIMED 占着
    # m1 一道一活锁，m1 上再 claim 会被静默拒（rc!=0 不 assert 就是把好锁测成假红）。
    rc, out = gate(sb, "create", "REQ-T-DEATH3", "--title", "即死3", "--verify", "true",
                   "--lanes", "m3", "--status", "OPEN")
    assert rc == 0, out
    rc, out = gate(sb, "claim", "REQ-T-DEATH3", "--owner", "m3")
    assert rc == 0, out
    with open(dl, "a", encoding="utf-8") as f:
        f.write("%s WORKER-DIED REQ-T-DEATH3@m3：pid=555 死\n" % t2)
    rc, out = run_fuse(sb)
    ck("S2 单次死亡不触停毒（不过度处置）",
       card(sb, "REQ-T-DEATH3").get("status") == "CLAIMED", tail(out))
    with open(dl, "a", encoding="utf-8") as f:
        f.write("%s WORKER-DIED REQ-T-DEATH3@m3：pid=556 死\n" % t1)
    ev = os.path.join(sb["home"], "state", "events.log")
    with open(ev, "a", encoding="utf-8") as f:
        f.write("%s|REQ-T-DEATH3|CLAIMED->DONE|m3|commit=abc\n"
                % datetime.fromtimestamp(now - 10 * 60).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"))
    rc, out = run_fuse(sb)
    ck("S2 死间有成迹(DONE)则连续被打断（不触停毒）",
       card(sb, "REQ-T-DEATH3").get("status") == "CLAIMED", tail(out))


def sc_s3(sb):
    """注入：交卷未并（b 净 / c 撞 / 主干脏三形）→ 断言能合自动合、冲突亮待合红灯叫PM、撤必红。"""
    repo = sb["repo"]
    # b 类：lane1 改 a.txt=one
    g(["-C", repo, "checkout", "-b", "lane1"])
    w(os.path.join(repo, "a.txt"), "one\n")
    g(["-C", repo, "add", "a.txt"]); g(["-C", repo, "commit", "-m", "b类交付(REQ-T-B)"])
    rc, h1 = g(["-C", repo, "rev-parse", "HEAD"])
    # HOSTPIN 交卷锚闸=在道上交卷（实 tip=自报才算真）；回 main 另起一行
    make_done_card(sb, "REQ-T-B", h1)
    g(["-C", repo, "checkout", "main"])
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ok_merge = g(["-C", repo, "merge-base", "--is-ancestor", h1, "main"], check=False)[0] == 0
    ck("S3 交卷未并+b净 → 自动 merge 进主干（撤必红锁）",
       ok_merge and events(sb, "FUSE-S3-MERGED"), "ancestor=%s out=%s" % (ok_merge, tail(out)))
    ck("S3 自动并线=merge --no-ff 合规模（零force：主干末提交即 merge 痕）",
       g(["-C", repo, "log", "-1", "--pretty=%s"], check=False)[1].startswith("merge(REQ-T-B)"),
       g(["-C", repo, "log", "-1", "--pretty=%s"], check=False)[1])
    # c 类：泳道分支改 a.txt，且主干随后也改 a.txt（真冲突形——主干单向领先不算撞）
    rc, h_main = g(["-C", repo, "rev-parse", "main"])
    g(["-C", repo, "checkout", "-b", "lane2", h_main])
    w(os.path.join(repo, "a.txt"), "TWO-CONFLICT\n")
    g(["-C", repo, "add", "a.txt"]); g(["-C", repo, "commit", "-m", "c类交付(REQ-T-C)"])
    rc, h2 = g(["-C", repo, "rev-parse", "HEAD"])
    g(["-C", repo, "checkout", "main"])
    w(os.path.join(repo, "a.txt"), "MAIN-WON\n")
    g(["-C", repo, "add", "a.txt"]); g(["-C", repo, "commit", "-m", "主干推进(造真冲突)"])
    g(["-C", repo, "checkout", "lane2"])
    make_done_card(sb, "REQ-T-C", h2)   # 在 lane2 上交卷（实 tip=自报）
    g(["-C", repo, "checkout", "main"])
    w(os.path.join(sb["base"], "wake.calls"), "")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    still = g(["-C", repo, "merge-base", "--is-ancestor", h2, "main"], check=False)[0] != 0
    ck("S3 冲突 → 不硬合（主干零污染，a.txt 仍=MAIN-WON）",
       still and open(os.path.join(repo, "a.txt"), encoding="utf-8").read().strip() == "MAIN-WON", "")
    ck("S3 冲突 → 待合红灯挂起 + 叫 PM",
       any("待合·撞车(FUSE-S3) REQ-T-C" in k for k in alerts_keys(sb))
       and events(sb, "FUSE-S3-BLOCKED") and "S3" in r(os.path.join(sb["base"], "wake.calls")),
       tail(out))
    # 龄未到位不动手（30min 阈的另一侧：全卡龄<9999 → 只报窗口不处置）
    c0 = ev_count(sb, "FUSE-S3-BLOCKED")
    w(os.path.join(sb["base"], "wake.calls"), "")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "9999"})
    ck("S3 龄<阈走正常候合窗口（不提前并线/不提前挡车）",
       "正常候合窗口" in out and ev_count(sb, "FUSE-S3-BLOCKED") == c0
       and not lines_of(os.path.join(sb["base"], "wake.calls")), tail(out))
    # 红检：主干工作树脏 → 能合也不赌（保护未提交改动）
    g(["-C", repo, "checkout", "-b", "lane3", "main"])
    w(os.path.join(repo, "z.txt"), "z\n")
    g(["-C", repo, "add", "z.txt"]); g(["-C", repo, "commit", "-m", "净交付(REQ-T-D)"])
    rc, h3 = g(["-C", repo, "rev-parse", "HEAD"])
    make_done_card(sb, "REQ-T-D", h3)
    g(["-C", repo, "checkout", "main"])
    # 脏形=**被跟踪文件**有在途人工改动（未跟踪杂物不算脏——真账仓常年有 tmp/logs，
    # 当脏=自动并线永动不了；此处必须与 fuse 守卫同一语义，否则锁测的是夹具不是产品）
    with open(os.path.join(repo, "a.txt"), "a", encoding="utf-8") as f:
        f.write("PM 在途未提交改动\n")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ck("S3 主干树脏 → 暂缓自动并线叫PM（不赌人肉在途改动）",
       g(["-C", repo, "merge-base", "--is-ancestor", h3, "main"], check=False)[0] != 0
       and events(sb, "FUSE-S3-MERGE-SKIP-DIRTY"), tail(out))
    g(["-C", repo, "checkout", "--", "a.txt"])
    # 红检：摘 s3 → 树干净了 REQ-T-D 也照样不并（处置本体被摘）
    c0m = ev_count(sb, "FUSE-S3-MERGED")
    c0d = ev_count(sb, "FUSE-S3-MERGE-SKIP-DIRTY")
    w(os.path.join(sb["base"], "wake.calls"), "")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"}, noact="s3")
    ck("S3 撤处置必红（NOACT=s3 不并线不留新处置痕）",
       g(["-C", repo, "merge-base", "--is-ancestor", h3, "main"], check=False)[0] != 0
       and ev_count(sb, "FUSE-S3-MERGED") == c0m and ev_count(sb, "FUSE-S3-MERGE-SKIP-DIRTY") == c0d,
       tail(out))


def sc_s4(sb):
    """注入：常驻件进程起时 早于 命脉文件最后提交 → NOT-ENERGIZED 报警+自动重启（白名单）；撤必红。"""
    home, repo = sb["home"], sb["repo"]
    g(["-C", repo, "checkout", "main"])
    w(os.path.join(home, "ops", "dispatch.py"), "# 命脉 v1\n")
    g(["-C", repo, "add", "cc-team/ops/dispatch.py"]); g(["-C", repo, "commit", "-m", "命脉v1"])
    # 进程“启动”在两小时前
    w(os.path.join(home, "logs", "ctrld.heartbeat"),
      "%s ctrld mode=live pid=424242 board=- rings=dispatch/60s\n" % time.strftime("%F %T"))
    env_probe = {"FUSE_STUB_PROC_START": "%.0f" % (time.time() - 2 * 3600),
                 "FUSE_RESIDENTS_JSON": json.dumps(
                     [{"name": "cc-dispatch", "hb": "logs/ctrld.heartbeat", "whitelist": True,
                       "lifelines": ["ops/dispatch.py"]}])}
    # 修复已进主干（提交在进程起时之后）
    w(os.path.join(home, "ops", "dispatch.py"), "# 命脉 v2 修复已进主干\n")
    g(["-C", repo, "add", "cc-team/ops/dispatch.py"]); g(["-C", repo, "commit", "-m", "命脉v2"])
    rc, out = run_fuse(sb, env_probe)
    ck("S4 旧进程未通电→NOT-ENERGIZED 红灯+自动重启（撤必红锁）",
       any("NOT-ENERGIZED(FUSE-S4)" in k for k in alerts_keys(sb))
       and events(sb, "FUSE-S4-NOT-ENERGIZED")
       and lines_of(os.path.join(sb["base"], "restart.calls")), tail(out))
    # 重启后再探=通电（进程起时=现在）→ 绿
    w(os.path.join(sb["base"], "restart.calls"), "")
    rc, out = run_fuse(sb, dict(env_probe, FUSE_STUB_PROC_START="%.0f" % (time.time() + 60)))
    ck("S4 重启后进程晚于提交=通电（不重复报警处置）",
       not lines_of(os.path.join(sb["base"], "restart.calls")) and "生效锁绿" in out, tail(out))
    # 红检：摘 s4 → 不重启
    rc, out = run_fuse(sb, env_probe, noact="s4")
    ck("S4 撤处置必红（NOACT=s4 不自动重启）",
       not lines_of(os.path.join(sb["base"], "restart.calls")), tail(out))


def sc_sk(sb):
    """自杀闸门红绿锁（BUG-FUSE-SELFKILL-1，2026-11-04 四连案 00:28/01:00/01:05/01:15）：
    注入 S1 派发心跳断流，但名册常驻心跳 pid=**活进程** ⇒ 处置必须 REFUSED（重启桩零调用，
    旧配方 Stop;Start=杀自己爹+复起被吞）；同断流换 pid=死号 ⇒ 复活重启放行。
    差分对=撤守卫第一断言必红，无需另注故障。"""
    hb2 = os.path.join(sb["home"], "logs", "ctrld.heartbeat")
    os.makedirs(os.path.dirname(hb2), exist_ok=True)
    w(hb2, "%s ctrld mode=live pid=%d board=- rings=x/60s\n" % (time.strftime("%F %T"), os.getpid()))
    hbf = sb_env_hb(sb)
    w(hbf, "%s hb\n" % time.strftime("%F %T"))
    os.utime(hbf, (time.time() - 240, time.time() - 240))
    rc, out = run_fuse(sb)
    ck("SK 名册引擎活⇒REFUSE-SELFKILL（重启桩零调用+处置留痕）",
       not lines_of(os.path.join(sb["base"], "restart.calls")) and "REFUSE-SELFKILL" in out
       and "重启=REFUSED" in out, tail(out))
    # 同断流+名册 pid=死号 ⇒ 复活重启放行（闸门不得变成「永远不动手」）
    w(hb2, "%s ctrld mode=live pid=424242 board=- rings=x/60s\n" % time.strftime("%F %T"))
    os.utime(hbf, (time.time() - 240, time.time() - 240))
    rc, out = run_fuse(sb)
    ck("SK 名册引擎死⇒复活重启放行（断流自愈通道未封死）",
       bool(lines_of(os.path.join(sb["base"], "restart.calls"))), tail(out))


def sc_s5(sb):
    """注入：账上有候补 + PM 静默 → 经唤醒通道叫PM；唤后仍不应 → 业主红条数据落地面；撤必红。"""
    # 候补=一张 DONE 未并卡（复用 S3 链）；空道=registry 4 道无人领
    repo = sb["repo"]
    g(["-C", repo, "checkout", "-b", "lane9", "main"])
    w(os.path.join(repo, "q.txt"), "q\n")
    g(["-C", repo, "add", "q.txt"]); g(["-C", repo, "commit", "-m", "候补(REQ-T-P)"])
    rc, hq = g(["-C", repo, "rev-parse", "HEAD"])
    make_done_card(sb, "REQ-T-P", hq)
    g(["-C", repo, "checkout", "main"])
    w(os.path.join(home_registry(sb)),
      json.dumps({"projects": [{"lanes": [{"lane": l} for l in ("m1", "m2", "m3", "m4")]}]}))
    # 候补在场但 PM 不静默（阈值正常）→ 不动
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "9999"})
    ck("S5 PM 在岗(静默未超阈)不叫（不误叫）",
       not lines_of(os.path.join(sb["base"], "wake.calls")) and "S5 候补=1" in out, tail(out))
    # 产出脉搏（业主 2026-11-03：检测必须看「线在不在出活」；纯观测行，撤了此线必红）：
    ck("S5 产出脉搏行在场（1h交卷/收口/在制/候补/无牌五数，观测不动账）",
       "S5 产出 1h 交卷=" in out and "在制=" in out and "候补=" in out and "无牌=" in out,
       tail(out))
    # PM 静默=0（本沙箱 create/done 刚写过 who=PM 的痕，用 0 阈把「静默」放开，
    #   判据路径与真 25min 完全同形——分钟级真等时归巡逻现网）
    w(os.path.join(sb["base"], "wake.calls"), "")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "9999", "FUSE_PM_SILENT_MIN": "0"})
    ck("S5 候补在+PM静默→经PM-WAKE通道叫PM（撤必红锁）",
       any("WAKE|S5" in l for l in lines_of(os.path.join(sb["base"], "wake.calls")))
       and events(sb, "FUSE-S5-PM-WAKE"), tail(out))
    # 升级面：唤后仍无 PM 动作 → 业主红条数据面。沙箱 PM 痕=本场景刚写的（龄数秒），
    # 故把唤醒锚拨到「比 PM 痕更新」并让升级窗=0（30min 真窗=同一判定，纯参数差；
    # 等真 30min 归巡逻现网，不在测试里睡）。
    st_p = os.path.join(sb["home"], "state", "fuse.json")
    st = json.loads(r(st_p) or "{}")
    st["last_pm_wake"] = time.time()
    w(st_p, json.dumps(st))
    w(os.path.join(sb["base"], "wake.calls"), "")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "9999", "FUSE_PM_SILENT_MIN": "0",
                            "FUSE_PM_ESCALATE_MIN": "0"})
    ck("S5 唤后仍无PM动作→业主红条数据面(alerts键+owner-red.json+痕)",
       any("业主:需要人工介入 FUSE-S5" in k for k in alerts_keys(sb))
       and os.path.isfile(os.path.join(sb["home"], "state", "fuse-owner-red.json"))
       and events(sb, "FUSE-S5-OWNER-RED"), tail(out))
    # 红检：摘 s5 → 不再叫也不再挂（按增量）
    c0w = ev_count(sb, "FUSE-S5-PM-WAKE")
    c0r = ev_count(sb, "FUSE-S5-OWNER-RED")
    st = json.loads(r(st_p) or "{}")
    st["last_pm_wake"] = 0
    w(st_p, json.dumps(st))
    w(os.path.join(sb["base"], "wake.calls"), "")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "9999", "FUSE_PM_SILENT_MIN": "0"}, noact="s5")
    ck("S5 撤处置必红（NOACT=s5 不叫PM不挂业主红）",
       not lines_of(os.path.join(sb["base"], "wake.calls"))
       and ev_count(sb, "FUSE-S5-PM-WAKE") == c0w and ev_count(sb, "FUSE-S5-OWNER-RED") == c0r,
       tail(out))


def sc_s6(sb):
    """注入：alerts 无主红灯超龄 → 一级叫 PM 班、二级直达业主信箱[待拍板]；ack 清标；撤处置必红。"""
    ap = os.path.join(sb["home"], "state", "alerts.json")
    fp = os.path.join(sb["home"], "state", "fuse.json")
    inboxp = os.path.join(sb["home"], "inbox", "PM-REPLIES.md")
    wakep = os.path.join(sb["base"], "wake.calls")

    def mk(key, age_min, ack=None):
        d = json.loads(r(ap) or "{}")
        a = d.setdefault("alerts", {})
        first = (datetime.now().astimezone() - timedelta(minutes=age_min)).isoformat(timespec="seconds")
        a[key] = {"msg": key, "first": first, "count": 1, "ack": ack}
        w(ap, json.dumps(d, ensure_ascii=False))

    def st_set(mut):
        st = json.loads(r(fp) or "{}")
        mut(st)
        w(fp, json.dumps(st))

    # ① 一级：35min 无主 → 经唤醒通道叫 PM 班（FUSE-S6-PM-WAKE 痕），业主信箱不发
    mk("ALARM: S6-TEST 低档样本", 35)
    rc, out = run_fuse(sb)
    ck("S6 无主≥30min→经PM-WAKE通道叫班（撤必红锁）",
       any("WAKE|S6" in l and "低档样本" in l for l in lines_of(wakep))
       and events(sb, "FUSE-S6-PM-WAKE") and not os.path.isfile(inboxp), tail(out))
    # ①b 键级单发：再跑一圈不重复唤同键
    n1 = sum(1 for l in lines_of(wakep) if "低档样本" in l)
    rc, out = run_fuse(sb)
    ck("S6 同键二圈不重复唤（单发纪律）",
       sum(1 for l in lines_of(wakep) if "低档样本" in l) == n1, tail(out))
    # ② 二级：65min 无主 → 直达业主信箱 [待拍板] + FUSE-S6-OWNER 痕 + 业主:红灯
    mk("ALARM: S6-TEST 高档样本", 65)
    rc, out = run_fuse(sb)
    ck("S6 无主≥60min→业主信箱[待拍板]直达+痕+业主红",
       os.path.isfile(inboxp) and "[待拍板] fuse-S6" in r(inboxp)
       and "高档样本" in r(inboxp) and events(sb, "FUSE-S6-OWNER"), tail(out))
    c0 = r(inboxp).count("[待拍板] fuse-S6")
    rc, out = run_fuse(sb)
    ck("S6 二级再跑不重复直达（键级单发）",
       r(inboxp).count("[待拍板] fuse-S6") == c0, tail(out))
    # ③ ack 清标：认领低档键 → s6 标位退场（同名新报警才能再升级）
    mk("ALARM: S6-TEST 低档样本", 35,
       ack=datetime.now().astimezone().isoformat(timespec="seconds"))
    rc, out = run_fuse(sb)
    st = json.loads(r(fp) or "{}")
    s6st = (st.get("s6") or {})
    ck("S6 ack 落账即清标（woken 退场）",
       "ALARM: S6-TEST 低档样本" not in (s6st.get("woken") or {}), tail(out))
    # ④ 自触发键不参与：② 直达时自挂的「业主:」红灯与人为注入的业主头键都不得再被升级
    assert any("业主:S6 报警无主超时升级直达" in k for k in alerts_keys(sb)), \
        "② 直达自挂业主红未落账（do_owner alarm 通道断）"
    c0b = r(inboxp).count("[待拍板] fuse-S6")
    mk("业主:S6 报警无主超时升级直达：喂自己样本", 99)
    rc, out = run_fuse(sb)
    ck("S6 自触发键（业主头）不参与转发（防自喂连环）",
       r(inboxp).count("[待拍板] fuse-S6") == c0b and "喂自己样本" not in r(inboxp),
       tail(out))
    # ⑤ 撤处置必红：清标清痕基线 → NOACT=s6 跑圈 → 无唤/无直达/无痕增量
    st_set(lambda s: s.pop("s6", None))
    w(wakep, "")
    e0 = ev_count(sb, "FUSE-S6-OWNER")
    i0 = r(inboxp).count("[待拍板] fuse-S6")
    rc, out = run_fuse(sb, noact="s6")
    ck("S6 撤处置必红（NOACT=s6：不唤不直达不落痕）",
       not any("WAKE|S6" in l for l in lines_of(wakep))
       and ev_count(sb, "FUSE-S6-OWNER") == e0
       and r(inboxp).count("[待拍板] fuse-S6") == i0, tail(out))


def home_registry(sb):
    return os.path.join(sb["home"], "registry.json")


def tail(out, n=180):
    return " ".join((out or "").split())[-n:]


# ---------------- 纯判据单元面 ----------------

def units():
    sys.path.insert(0, os.path.join(TEAM, "ops"))
    import fuse
    ok_ts = fuse.parse_ts("2026-11-02T22:43:17+0800") and fuse.parse_ts("2026-11-02 22:43:17") \
        and fuse.parse_ts("2026-11-02T22:43:17+08:00")
    ck("U parse_ts 三形态(events+0800/日志无 tz/卡面+08:00)均可解析", bool(ok_ts))
    ck("U S1 心跳龄阈判定(300s>180 断/60s 续/缺失=断)",
       fuse.s1_decide(300, 180)[0] and not fuse.s1_decide(60, 180)[0] and fuse.s1_decide(None, 180)[0])
    idx = fuse.died_index(["2026-11-02 22:10:00 WORKER-DIED A@m1 x",
                           "2026-11-02 22:20:00 WORKER-DIED A@m1 y",
                           "2026-11-02 22:30:00 WORKER-DIED B@m2 z"])
    ck("U S2 WORKER-DIED 索引按卡分组",
       len(idx.get("A", [])) == 2 and len(idx.get("B", [])) == 1)
    e1 = idx["A"][0][0]; e2 = idx["A"][1][0]
    ck("U S2 窗内2次=触/成迹打断=不触/窗外=不触",
       fuse.s2_decide(idx["A"], None, e2 + 10, 3600)[0]
       and not fuse.s2_decide(idx["A"], e1 + 5, e2 + 10, 3600)[0]
       and not fuse.s2_decide(idx["A"], None, e2 + 7200, 3600)[0])
    ck("U S3 merge-tree 判类 0/1/其余→b/c/?",
       fuse.s3_classify(0) == "b" and fuse.s3_classify(1) == "c" and fuse.s3_classify(128) == "?")
    ck("U S4 起时早于提交=滞后(带宽限)；晚于=绿",
       len(fuse.s4_decide(1000.0, [("ops/dispatch.py", 1000 + 121)], 120)) == 1
       and not fuse.s4_decide(2000.0, [("ops/dispatch.py", 1000.0)], 120))
    now = time.time()
    ck("U S5 无候补=不动/静默未超阈=不动/未唤=WAKE/唤后超窗=WAKE+RED/唤后窗内=等响应",
       fuse.s5_decide(now, now - 3600, 0, 2, None, 1500, 1800)[0] == ""
       and fuse.s5_decide(now, now - 60, 1, 0, None, 1500, 1800)[0] == ""
       and fuse.s5_decide(now, now - 3600, 1, 0, None, 1500, 1800)[0] == "WAKE"
       and fuse.s5_decide(now, now - 3600, 1, 0, now - 1900, 1500, 1800)[0] == "WAKE+OWNER-RED"
       and fuse.s5_decide(now, now - 3600, 0, 3, now - 100, 1500, 1800)[0] == "")
    ck("U S6 无主龄<一级=不动/≥一级=WAKE/≥二级=OWNER/已ack=静默/坏时距=宁漏不误",
       fuse.s6_decide(now, now - 600, False, 1800, 3600)[0] == ""
       and fuse.s6_decide(now, now - 2100, False, 1800, 3600)[0] == "WAKE"
       and fuse.s6_decide(now, now - 3900, False, 1800, 3600)[0] == "OWNER"
       and fuse.s6_decide(now, now - 3900, True, 1800, 3600)[0] == ""
       and fuse.s6_decide(now, None, False, 1800, 3600)[0] == "")
    ck("U S5 带 self=fuse 显标的自触发痕不算 PM 动作（防保险丝自拨静默钟；摘标记必红）",
       fuse.last_pm_action_ts([
           "2026-11-03T00:00:00+0800|REQ-X|OPEN->HOLD|PM|set diff=hold:STOPLOSS:fuse 连2即死 self=fuse",
       ]) is None
       and abs(fuse.last_pm_action_ts([
           "2026-11-03T00:00:00+0800|REQ-X|-|PM|create",
           "2026-11-03T00:10:00+0800|REQ-X|OPEN->HOLD|PM|set diff=hold:STOPLOSS:fuse x self=fuse",
       ]) - fuse.parse_ts("2026-11-03T00:00:00+0800")) < 1)
    ck("U S5 PM 真人痕引用 STOPLOSS 字样而无标记=必须拨钟（21:01 假 OWNER-RED 病根治；嗅探旧规回潮必红）",
       abs(fuse.last_pm_action_ts([
           "2026-11-03T00:00:00+0800|REQ-X|-|PM|create",
           "2026-11-03T00:10:00+0800|REQ-X|HOLD->OPEN|PM|set diff=hold:STOPLOSS:fuse 已归因=recycle误杀非卡>\"\"",
       ]) - fuse.parse_ts("2026-11-03T00:10:00+0800")) < 1)


# ---------------- 主流程 ----------------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    units()
    if "--units" in sys.argv:
        return finish()
    scenes = [("S1", sc_s1), ("S2", sc_s2), ("S3", sc_s3), ("S4", sc_s4), ("S5", sc_s5), ("S6", sc_s6),
              ("SK", sc_sk)]
    for tag, fn in scenes:
        sb = sandbox(tag)
        try:
            fn(sb)
        except Exception as e:
            ck("%s 场景执行器崩溃" % tag, False, "%r" % (e,))
        finally:
            # 幽灵卡锁：fuse 行第二段必须是真实在册卡号——伪卡号「FUSE」会被
            # ops/reconcile_events.py 判据一对成「events 有卡但事实图无此卡」永久红
            # （00:23 真账首圈实锤 count=3）。每场景收箱前全量扫一遍。
            evp = os.path.join(sb["home"], "state", "events.log")
            ids = card_ids(sb)
            ghosts = []
            for l in lines_of(evp):
                f = l.split("|")
                if len(f) >= 4 and f[3] == "fuse" and f[1] not in ids:
                    ghosts.append(l)
            ck("%s fuse 行卡号全在册（幽灵卡锁）" % tag, not ghosts, (ghosts or [""])[0][:120])
            shutil.rmtree(sb["base"], ignore_errors=True)
    return finish()


def finish():
    n_red = sum(1 for _, ok, _ in RESULTS if not ok)
    print("FUSE_CHECK n=%d red=%d %s" % (len(RESULTS), n_red,
                                         "ALL-GREEN" if n_red == 0 else "HAS-RED"))
    return 0 if n_red == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
