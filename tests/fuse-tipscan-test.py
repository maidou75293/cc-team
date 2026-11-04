#!/usr/bin/env python3
# cc-team/tests/fuse-tipscan-test.py — MECH-FUSE-S3-TIPSCAN-1「S3 判类前扫同 owner 后续 commit 锚」红绿锁。
# 病根（docs/机制-fuse处置-S3撞车合流-20261103.md 二轮§五-1 遗留面，三轮§五-2 再实锤）：
#   卡面 commit 只记一笔——UX-BOARD-COLLAPSE-1 的锚 20493822 是三笔半成品之首（后续 f84b0d8d 才是
#   live.js 分桶半边），b 类自动并线按单锚只会并进「有折叠壳、无填行」的残缺半边。MECH-DONEANCHOR-TIP-1
#   只治了 done 交卷瞬间的记锚，S3 侧「扫同 owner 后续 commit 提示 PM」未落地=本卡。
# 机制（ops/fuse.py seg_s3 b 类支 + 纯判据 s3_tipscan_filter/s3_tipscan_decide）：
#   merge-tree 判净后、真合前，以 git log 扫卡面锚→泳道树尖（同 owner 域=卡交付仓 c["repo"]+交卷支
#   done_anchor.branch，仓失踪退主干仓复核）的后续 commit：带本卡号=本卡多锚；无归属=宁红不猜按
#   本卡算；只带别卡号=泳道挪下一单不算（不误挡）。命中≥1 改判「待合·多锚(FUSE-S3)」不自动并、
#   红灯+首报叫 PM 人工定锚（键级单发 state=s3_tipscan）。兜底：树尖探不出且同卡交卷流水>1 笔
#   （events CLAIMED->DONE 多笔，fuse 自身 DONE->DONE 痕不计）也挡。宁叫人不误并。
# 锁面（全沙箱=同 fuse-check 姿势：CC_TEAM_HOME 临时账家 + 临时 git 仓 + 唤醒/重启桩，零碰正账）：
#   U   纯判据三态/归属过滤/无归属宁红不猜/token 形态（S3、D02 无连字符不算归属）。
#   M   多锚卡（锚外 1 笔无归属）禁自动并+灯+叫PM；键级单发；龄未到位不动（候合窗口先于扫描）；
#       dry 只报不落账；NOACT=s3 撤处置（灯=报面保留、事件/唤醒/台账全停）后复针必再唤=差分对。
#   S   单锚卡照常自动并（闸门不过度挡——恒挡世界此处必红）。
#   F   锚外后续笔只带别卡号=泳道已挪单，本卡照常自动并（归属过滤不过度挡）。
#   E   树尖读不出（泳道回收=交卷支删+交付仓路径失踪）且同卡交卷流水 2 笔 → events 兜底面挡。
#   B   摘针必红差分对（记忆纪律「守卫要验过它红得起来」）：把纯判据 git 面改盲的 fuse 拷贝
#       （tmp 树，产品件只读）跑同一多锚沙箱 → 半边**真被并入主干**——M 场「不并」断言证明
#       真挂在扫描闸上，非虚挂（有闸不并/无闸并=两半钉死）。
# 用法：python -I cc-team/tests/fuse-tipscan-test.py   # 尾行 FUSE_TIPSCAN n=… red=0 ALL-GREEN=rc0
import json, os, re, shutil, subprocess, sys, tempfile, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TEAM = os.path.dirname(HERE)                       # cc-team 根
FUSE = os.path.join(TEAM, "ops", "fuse.py")
CARDS = os.path.join(TEAM, "cards_dag.py")
PY = sys.executable
RESULTS = []
os.environ.pop("CC_AUTO_VERIFY", None)             # 沙箱 done 不拉自动验收（同 done-anchor-tip 纪律）


def ck(name, cond, note=""):
    RESULTS.append((name, bool(cond), note))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, ("｜" + note) if note else ""))


# ---------------- 沙箱基座（同 fuse-check 形） ----------------

def sandbox(tag):
    base = tempfile.mkdtemp(prefix="fuse-ts-%s-" % tag)
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
    rc, out = gate(sb, "create", cid, "--title", "多锚沙箱卡%s" % cid, "--verify", "true",
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


def lane_commit(sb, fname, msg, content=None):
    """在当前泳道支落一笔（文件各异互不冲突=保持 b 类净形——本锁只测多锚闸不测撞车）。"""
    repo = sb["repo"]
    w(os.path.join(repo, fname), content if content is not None else fname + " " + msg + "\n")
    g(["-C", repo, "add", fname])
    g(["-C", repo, "commit", "-m", msg])
    return g(["-C", repo, "rev-parse", "HEAD"])[1]


def fuse_env(sb, extra=None):
    env = dict(os.environ)
    env.update({
        "CC_TEAM_HOME": sb["home"],
        "PYTHONIOENCODING": "utf-8",
        "CC_AUTO_VERIFY": "",
        "FUSE_MAIN_REPO": sb["repo"],
        "FUSE_MAIN_REF": "main",
        "FUSE_HB": os.path.join(sb["base"], "hb.txt"),
        "FUSE_LOGS": os.path.join(sb["home"], "logs", "dispatch.log"),
        "FUSE_RESTART_CMD": '"%s" -I "%s"' % (PY, os.path.join(sb["stubs"], "restart.py")),
        "FUSE_PM_WAKE_CMD": '"%s" -I "%s"' % (PY, os.path.join(sb["stubs"], "wake.py")),
        "FUSE_PROBE_START_CMD": '"%s" -I "%s"' % (PY, os.path.join(sb["stubs"], "probe.py")),
        "FUSE_STUB_RESTART": os.path.join(sb["base"], "restart.calls"),
        "FUSE_STUB_WAKE": os.path.join(sb["base"], "wake.calls"),
        "FUSE_RESTART_THROTTLE_MIN": "0",
        "FUSE_WAKE_MIN_INTERVAL_MIN": "0",
    })
    if not os.path.isfile(env["FUSE_HB"]):
        w(env["FUSE_HB"], "%s hb\n" % time.strftime("%F %T"))
    if extra:
        env.update(extra)
    # 自卡（段级痕挂真卡号，幽灵卡锁同源纪律）
    if not os.path.exists(os.path.join(sb["base"], ".selfcard")):
        rc, out = gate(sb, "create", "REQ-FLEET-FUSE-1", "--title", "断流保险丝(沙箱自账)",
                       "--verify", "true", "--lanes", "fuse", "--status", "OPEN")
        assert rc == 0, out
        w(os.path.join(sb["base"], ".selfcard"), "1")
    return env


def run_fuse(sb, extra=None, noact=None, fuse_path=None):
    env = fuse_env(sb, extra)
    if noact:
        env["FUSE_NOACT"] = noact
    p = subprocess.run([PY, "-I", fuse_path or FUSE, "--apply"], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=240)
    return p.returncode, ((p.stdout or "") + "\n" + (p.stderr or ""))


def run_fuse_dry(sb, extra=None):
    env = fuse_env(sb, extra)
    p = subprocess.run([PY, "-I", FUSE], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=240)
    return p.returncode, ((p.stdout or "") + "\n" + (p.stderr or ""))


def events(sb, needle):
    return [l for l in r(os.path.join(sb["home"], "state", "events.log")).splitlines()
            if needle in l and "|fuse|" in l]


def ev_count(sb, needle):
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


def graph_edit(sb, cid, **kv):
    p = os.path.join(sb["home"], "state", "graph.json")
    d = json.loads(r(p) or "{}")
    c = d.setdefault("cards", {}).setdefault(cid, {})
    c.update(kv)
    w(p, json.dumps(d, ensure_ascii=False))


def st_edit(sb, drop=None):
    p = os.path.join(sb["home"], "state", "fuse.json")
    st = json.loads(r(p) or "{}")
    if drop:
        st.pop(drop, None)
    w(p, json.dumps(st))


def events_append(sb, line):
    p = os.path.join(sb["home"], "state", "events.log")
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def lines_of(p):
    try:
        return [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]
    except OSError:
        return []


def wake_lines(sb, needle):
    return [l for l in lines_of(os.path.join(sb["base"], "wake.calls")) if needle in l]


def card_ids(sb):
    try:
        d = json.load(open(os.path.join(sb["home"], "state", "graph.json"), encoding="utf-8"))
        return set((d.get("cards") or d.get("nodes") or {}).keys())
    except (OSError, ValueError):
        return set()


def merged(sb, h):
    return g(["-C", sb["repo"], "merge-base", "--is-ancestor", h, "main"], check=False)[0] == 0


def tail(out, n=180):
    return " ".join((out or "").split())[-n:]


# ---------------- 纯判据单元面 ----------------

def units():
    sys.path.insert(0, os.path.join(TEAM, "ops"))
    import fuse
    ck("U1 decide: 锚外 1 笔=(1,'git') 多锚存疑",
       fuse.s3_tipscan_decide([("a1", "fix: 补半边")], True, 0) == (1, "git"))
    ck("U2 decide: git 面已证单锚 → 交卷流水 3 笔不再三猜（返工过的卡不永锁）",
       fuse.s3_tipscan_decide([], True, 3) == (0, ""))
    ck("U3 decide: 树尖读不出+流水2笔=events 兜底挡；1笔=不疑",
       fuse.s3_tipscan_decide([], False, 2) == (2, "events")
       and fuse.s3_tipscan_decide([], False, 1) == (0, ""))
    ck("U4 filter: 本卡号留/别卡号去/无归属留（宁红不猜）",
       fuse.s3_tipscan_filter("REQ-A-1", [("h1", "fix(REQ-A-1): 补半边"),
                                          ("h2", "feat(REQ-B-1): 挪下一单"),
                                          ("h3", "chore: 无号补账")])
       == [("h1", "fix(REQ-A-1): 补半边"), ("h3", "chore: 无号补账")])
    ck("U5 filter: 混标(本卡+别卡)按本卡留；无连字符 token（S3/D02）不造伪归属",
       fuse.s3_tipscan_filter("REQ-A-1", [("h1", "merge(REQ-A-1): 自动并线带 REQ-FLEET-FUSE-1 S3")])
       == [("h1", "merge(REQ-A-1): 自动并线带 REQ-FLEET-FUSE-1 S3")]
       and fuse.s3_tipscan_filter("REQ-A-1", [("h2", "修 S3 与 D02 的旧账")])
       == [("h2", "修 S3 与 D02 的旧账")])
    # _done_events_count 兜底素材判义：只数真交卷迁移；fuse 自身 DONE->DONE 处置痕/他卡痕不计
    eh = tempfile.mkdtemp(prefix="fuse-ts-ev-")
    os.makedirs(os.path.join(eh, "state"), exist_ok=True)
    w(os.path.join(eh, "state", "events.log"),
      "2026-11-05T00:00:00+0800|REQ-A|CLAIMED->DONE|m1|commit=a\n"
      "2026-11-05T00:01:00+0800|REQ-A|DONE->DONE|fuse|FUSE-S3-TIPSCAN 多锚存疑 git面\n"
      "2026-11-05T00:02:00+0800|REQ-A|DONE->VERIFIED|PM|verdict PASS\n"
      "2026-11-05T00:03:00+0800|REQ-B|OPEN->DONE|m2|commit=b\n")
    ck("U6 _done_events_count 只数真交卷迁移=1（fuse 自身 DONE->DONE 痕不喂兜底面；撤 old!=new 判据必红）",
       fuse._done_events_count({"home": eh}, "REQ-A") == 1
       and fuse._done_events_count({"home": eh}, "REQ-B") == 1)
    shutil.rmtree(eh, ignore_errors=True)


# ---------------- 场景 ----------------

def sc_multi(sb):
    """M：三笔半成品之首形（锚+1 笔无归属后续）——禁单锚自动并全链断言。"""
    repo, wakep = sb["repo"], os.path.join(sb["base"], "wake.calls")
    g(["-C", repo, "checkout", "-b", "tsA"])
    h1 = lane_commit(sb, "a.txt", "feat(REQ-T-TSM): 交卷半边")
    make_done_card(sb, "REQ-T-TSM", h1)
    lane_commit(sb, "b.txt", "fix: 补后半")            # 锚外无归属后续笔=多锚素材
    g(["-C", repo, "checkout", "main"])
    w(wakep, "")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ck("M1 多锚卡禁单锚自动并 + 待合·多锚红灯 + FUSE-S3-TIPSCAN 痕 + 叫PM（撤扫描必红=B 场差分对）",
       not merged(sb, h1) and events(sb, "FUSE-S3-TIPSCAN")
       and any("待合·多锚(FUSE-S3) REQ-T-TSM" in k for k in alerts_keys(sb))
       and wake_lines(sb, "多锚存疑"), tail(out))
    ck("M1b 卡面状态纹丝不动（DONE 候 PM 定锚，本器不越权）",
       card(sb, "REQ-T-TSM").get("status") == "DONE", "")
    # 键级单发：第二圈不再唤
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ck("M2 二圈不重复唤（state=s3_tipscan 键级单发）仍不并",
       len(wake_lines(sb, "多锚存疑")) == 1 and not merged(sb, h1), tail(out))
    # 龄未到位不动（候合窗口先于扫描）
    w(wakep, "")
    c0 = ev_count(sb, "FUSE-S3-TIPSCAN")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "9999"})
    ck("M3 龄<阈=正常候合窗口不扫不唤（多锚卡同受窗口约束）",
       "正常候合窗口" in out and not wake_lines(sb, "多锚存疑")
       and ev_count(sb, "FUSE-S3-TIPSCAN") == c0, tail(out))
    # dry 只报不落账
    w(wakep, "")
    a0 = len(alerts_keys(sb))
    rc, out = run_fuse_dry(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ck("M4 dry 只报不落账（DRY-ALARM 多锚在场，零灯增量/零痕/零唤）",
       "DRY-ALARM 待合·多锚" in " ".join(out.split()) and len(alerts_keys(sb)) == a0
       and ev_count(sb, "FUSE-S3-TIPSCAN") == c0 and not wake_lines(sb, "多锚存疑"), tail(out))
    # NOACT=s3：摘处置（灯=报面按 S3 既有契约保留；事件/唤醒/台账=处置整段空转）
    st_edit(sb, drop="s3_tipscan")
    w(wakep, "")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"}, noact="s3")
    ck("M5 NOACT=s3 撤处置不唤不落痕不记台账（灯报面保留，半边仍不并）",
       not wake_lines(sb, "多锚存疑") and ev_count(sb, "FUSE-S3-TIPSCAN") == c0
       and "s3_tipscan" not in (json.loads(r(os.path.join(sb["home"], "state", "fuse.json")) or "{}"))
       and any("待合·多锚(FUSE-S3) REQ-T-TSM" in k for k in alerts_keys(sb))
       and not merged(sb, h1), tail(out))
    # 复针必再唤：M5 的静默不是「恒不唤」虚挂
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ck("M6 复针再跑必唤必落痕（M5↔M6 差分对=处置真挂 act(s3) 上）",
       len(wake_lines(sb, "多锚存疑")) == 1 and ev_count(sb, "FUSE-S3-TIPSCAN") == c0 + 1
       and not merged(sb, h1), tail(out))


def sc_single(sb):
    """S：锚=树尖单锚卡 → 照常自动并（恒挡世界此处必红=闸门不过度挡）。"""
    repo = sb["repo"]
    g(["-C", repo, "checkout", "-b", "ts1"])
    h1 = lane_commit(sb, "s.txt", "feat(REQ-T-TSS): 完整一笔交清")
    make_done_card(sb, "REQ-T-TSS", h1)
    g(["-C", repo, "checkout", "main"])
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ck("S1 单锚卡照常自动并（FUSE-S3-MERGED）且无多锚痕无唤（不过度挡·撤闸世界与此同绿故差分在 B 场）",
       merged(sb, h1) and events(sb, "FUSE-S3-MERGED")
       and not events(sb, "FUSE-S3-TIPSCAN") and not wake_lines(sb, "多锚存疑"), tail(out))


def sc_foreign(sb):
    """F：锚外后续笔只带别卡号（泳道已挪下一单）→ 不算本卡账，照常自动并。"""
    repo = sb["repo"]
    g(["-C", repo, "checkout", "-b", "tsF"])
    h1 = lane_commit(sb, "f.txt", "feat(REQ-T-TSF): 本卡交卷(锚)")
    make_done_card(sb, "REQ-T-TSF", h1)
    lane_commit(sb, "n.txt", "feat(REQ-T-NEXT9): 下一单在途")
    g(["-C", repo, "checkout", "main"])
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ck("F1 锚外别卡号笔排除归属 → 本卡照常自动并不叫PM",
       merged(sb, h1) and not events(sb, "FUSE-S3-TIPSCAN")
       and not wake_lines(sb, "多锚存疑"), tail(out))


def sc_events(sb):
    """E：泳道回收签名（交卷支已删+交付仓路径失踪）+ 同卡交卷流水 2 笔 → events 兜底面挡。"""
    repo = sb["repo"]
    g(["-C", repo, "checkout", "-b", "tsE"])
    h1 = lane_commit(sb, "e.txt", "feat(REQ-T-TSE): 交卷")
    make_done_card(sb, "REQ-T-TSE", h1)
    g(["-C", repo, "checkout", "main"])
    g(["-C", repo, "branch", "-D", "tsE"])          # 交卷支不可解（done_anchor.branch 悬空）
    graph_edit(sb, "REQ-T-TSE", repo=os.path.join(sb["base"], "gone-lane-repo"))  # 交付仓路径失踪
    events_append(sb, "%s|REQ-T-TSE|CLAIMED->DONE|m1|commit=redone2（返工重交流水=多锚账证）"
                  % datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"))
    w(os.path.join(sb["base"], "wake.calls"), "")
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ck("E1 树尖探不出+同卡交卷流水2笔 → events 兜底改判多锚（不并+灯+唤含 events面）",
       not merged(sb, h1) and events(sb, "FUSE-S3-TIPSCAN")
       and any("待合·多锚(FUSE-S3) REQ-T-TSE" in k for k in alerts_keys(sb))
       and wake_lines(sb, "events面"), tail(out))


def sc_blind(sb):
    """B：摘针必红差分对——纯判据改盲的 fuse 拷贝跑同一多锚沙箱，半边真被并入主干。"""
    repo = sb["repo"]
    g(["-C", repo, "checkout", "-b", "tsB"])
    h1 = lane_commit(sb, "k1.txt", "feat(REQ-T-TSB): 交卷半边(锚)")
    make_done_card(sb, "REQ-T-TSB", h1)
    lane_commit(sb, "k2.txt", "fix(REQ-T-TSB): 补代码后半")   # 同卡号锚外笔=git 面实锤素材
    g(["-C", repo, "checkout", "main"])
    # 有闸世界：不并（同 M1 语义，本场再钉一次）
    rc, out = run_fuse(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    ck("B1 有闸世界：同卡号锚外笔禁并+痕+唤",
       not merged(sb, h1) and events(sb, "FUSE-S3-TIPSCAN") and wake_lines(sb, "多锚存疑"), tail(out))
    # 无闸世界：摘针拷贝（tmp 树=ccards/cards_dag 副本+改盲 fuse，产品件只读）
    src = open(FUSE, encoding="utf-8").read()
    blind_ret = '        return len(followups), "git"'
    assert src.count(blind_ret) == 1, "摘针锚点行不唯一/缺失=锁失效（fuse.py 改动先查此处）"
    t = os.path.join(sb["base"], "blind", "cc-team")
    os.makedirs(os.path.join(t, "ops"), exist_ok=True)
    shutil.copytree(os.path.join(TEAM, "ccards"), os.path.join(t, "ccards"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(CARDS, os.path.join(t, "cards_dag.py"))
    w(os.path.join(t, "ops", "fuse.py"), src.replace(blind_ret, '        return 0, "blind"'))
    env = fuse_env(sb, {"FUSE_UNMERGED_AGE_MIN": "0"})
    p = subprocess.run([PY, "-I", os.path.join(t, "ops", "fuse.py"), "--apply"], env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    outb = (p.stdout or "") + (p.stderr or "")
    ck("B2 摘针世界（decide→盲）：同一多锚卡半边真被单锚并入主干 → B1「不并」证明真挂扫描闸上非虚挂",
       merged(sb, h1) and events(sb, "FUSE-S3-MERGED"), tail(outb))
    ck("B3 摘针后锚外笔确实只并了半边（主干有 k1 无 k2=「残缺半边」名词有据）",
       os.path.isfile(os.path.join(repo, "k1.txt")) and not os.path.isfile(os.path.join(repo, "k2.txt")),
       "k1=%s k2=%s" % (os.path.isfile(os.path.join(repo, "k1.txt")),
                        os.path.isfile(os.path.join(repo, "k2.txt"))))


# ---------------- 主流程 ----------------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    units()
    scenes = [("multi", sc_multi), ("single", sc_single), ("foreign", sc_foreign),
              ("events", sc_events), ("blind", sc_blind)]
    for tag, fn in scenes:
        sb = sandbox(tag)
        try:
            fn(sb)
        except Exception as e:
            ck("%s 场景执行器崩溃" % tag, False, "%r" % (e,))
        finally:
            # 幽灵卡锁（同 fuse-check）：|fuse| 行第二段必须真实在册卡号
            evp = os.path.join(sb["home"], "state", "events.log")
            ids = card_ids(sb)
            ghosts = []
            for l in lines_of(evp):
                f = l.split("|")
                if len(f) >= 4 and f[3] == "fuse" and f[1] not in ids:
                    ghosts.append(l)
            ck("%s fuse 行卡号全在册（幽灵卡锁）" % tag, not ghosts, (ghosts or [""])[0][:120])
            shutil.rmtree(sb["base"], ignore_errors=True)
    n_red = sum(1 for _, ok_, _ in RESULTS if not ok_)
    print("FUSE_TIPSCAN n=%d red=%d %s" % (len(RESULTS), n_red,
                                           "ALL-GREEN" if n_red == 0 else "HAS-RED"))
    return 0 if n_red == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
