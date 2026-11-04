#!/usr/bin/env python3
# cc-team/tests/mergetrain-tipscan-test.py — MECH-MERGETRAIN-TIPSCAN-1「火车侧多锚扫描闸」红绿锁。
# 病根（src=MECH-FUSE-S3-TIPSCAN-1 卡面射程边界『火车侧同闸若要收，另卡另议』；docs/机制-fuse处置
#   -S3撞车合流-20261103.md 二轮§五-1 遗留面）：fuse S3 多锚闸首拦 BUG-BIND-PMCLAIM-EXEMPT-1
#   （卡面锚 800e052d 之外同 owner 还有证据文 f7fe6a22，PM 定锚树尖外合 77c5135a 脱身），而火车
#   通道无同闸——passed 队列真合/外合闭环只处理卡面锚一笔=缺证据文残半进主干，S3 红灯形同虚设。
# 机制（ops/mergetrain.py _tipscan_gate，挂在 stage2 真合前 + _ext_merge_closure 转正前两支；
#   判定 import fuse s3_tipscan_filter/s3_tipscan_decide 同源单点——卡面禁拷贝；唤人=run_pm_wake
#   同函数，冷却 state=火车辅账 last_pm_wake；events 兜底面=_done_events_count 同式）：
#   命中=不并/不转正 + 「待合·多锚(TRAIN) 卡面锚外另有N笔候并」键（ccards.alerts 键级可 ack）
#   + 叫 PM 定锚——宁叫人不误并。火车侧「候并」口径=锚..树尖后续 commit 且未进主干：PM 把树尖
#   全部外合（77c5135a 实形）候并=0 照常转正；只外合卡面锚=残半，证据文仍候并在场必挡。
#   候并再经台账归属剔除（_tipscan_ledger_attributed：锚后 commit 恰=他卡 DONE/VERIFIED 在册
#   交卷锚 → 是下一张卡的交付，非本卡候并——单泳道顺次叠卡为在册常态，fuse 侧时序看不到此层）。
# 锁面（全沙箱=merge-train-check 姿势：CC_TEAM_HOME 临时账家 + 临时 git 仓 + 唤醒桩，零碰正账）：
#   TC0 静态同源：mergetrain 引用 fuse 判据三件+run_pm_wake；本地零 def s3_tipscan 拷贝；
#         两支挂闸字样在位；fuse 侧零 TRAIN 文案（S3 零改动面）。
#   M   多锚队列挡并全链：锚外 1 笔同卡面候并 → 主干纹丝不动+stage=passed+键+叫PM+events 痕；
#       二圈仍挡且唤不重复（run_pm_wake 冷却=键级单发语义在火车侧的落点）。
#   F   锚外只带别卡号=泳道已挪单 → 照常并（归属过滤不过度挡，与 fuse F 场同义）。
#   S   单锚卡（锚=树尖）照常并+VERIFIED（恒挡世界此处必红）。
#   G   叠卡不误挡：锚后无 token 但=他卡在册交卷锚 → 台账归属剔除照常并（恒挡剔世界必红；
#       merge-train-check S2 队列叠卡实形的本锁钉，防本闸把在册常态挡成死锁）。
#   B   撤闸差分对（守卫要验过它红得起来）：_tipscan_gate 打桩恒放行 → 同一多锚交付**真被**
#       并进主干=残半入线——M 场的「不并」证明真挂在那道闸上，非虚挂。
#   X   外合闭环：只外合卡面锚（证据文留泳道）→ 不转正+「待合·多锚(TRAIN)」+叫PM；PM 把树尖
#       补齐外合 → 候并清零 → 外线沙箱复验+verdict PASS 照常 VERIFIED（77c5135a 实形钉死）。
#   E   兜底面接线：树尖读不出+同卡交卷流水 2 笔 → 挡（decide 同源判义，fuse U 面已锁纯函数，
#       本场只验火车侧装配）。
# 判据：python -I cc-team/tests/mergetrain-tipscan-test.py   # 尾行 TRAIN_TIPSCAN n=… red=0 ALL-GREEN=rc0
import importlib, json, os, re, shutil, subprocess, sys, tempfile, time

try:                                    # 裸 `python -I` 判据形（GBK 控制台）中文可见不炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CT = os.path.dirname(HERE)                       # cc-team 根
PY = sys.executable
RESULTS = []


def ck(name, cond, note=""):
    RESULTS.append((name, bool(cond)))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, ("｜" + note) if note else ""))


os.environ.pop("CC_AUTO_VERIFY", None)           # 沙箱 done 不拉自动验收（同 merge-train-check 纪律）
os.environ.pop("CARD_MAINLINE", None)            # 主干 ref 走 main→master→trunk 自解析
os.environ["MERGE_TRAIN_STEP_TIMEOUT"] = "240"

SB = tempfile.mkdtemp(prefix="cc-train-tipscan-")
os.environ["MERGE_TRAIN_TMPDIR"] = SB
# 宿主钉（同 merge-train-check：显式钉沿用；which 落 System32=WSL 桥毒宿主不钉，交自解析）
_exp = os.environ.get("CARD_VERIFY_BASH")
_w = _exp or shutil.which("bash") or "bash"
if _exp is None and os.name == "nt" and _w.lower().startswith(
        os.environ.get("SystemRoot", "C:\\Windows").lower()):
    pass
else:
    os.environ["CARD_VERIFY_BASH"] = _w
# 唤 PM 桩（fuse run_pm_wake 的 env FUSE_PM_WAKE_CMD 通道；同 fuse-tipscan-test wake.py 形）
STUBS = os.path.join(SB, "stubs")
os.makedirs(STUBS, exist_ok=True)
WAKE_PY = os.path.join(STUBS, "wake.py")
with open(WAKE_PY, "w", encoding="utf-8") as _f:
    _f.write("import os\n"
             "open(os.environ['FUSE_STUB_WAKE'],'a',encoding='utf-8')"
             "\\\n    .write('WAKE|%s\\n'%os.environ.get('PM_WAKE_REASON','').replace('\\n',' '))\n"
             "raise SystemExit(int(os.environ.get('FUSE_STUB_WAKE_RC') or 0))\n")
os.environ["FUSE_PM_WAKE_CMD"] = '"%s" -I "%s"' % (PY, WAKE_PY)


def gitc(d, *a, chk=True):
    r = subprocess.run(["git", "-C", d] + list(a), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=120)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    if chk and r.returncode != 0:
        raise RuntimeError("git %s rc=%s %s" % (" ".join(a), r.returncode, out[-200:]))
    return r.returncode, out


def fresh(home, **envs):
    """场景隔离（同 merge-train-check）：CC_TEAM_HOME+重 import；fuse 一并清（判据引用随新账家重绑）。"""
    for m in list(sys.modules):
        if m == "ccards" or m.startswith("ccards.") or m in ("recycle", "mergetrain", "fuse"):
            del sys.modules[m]
    for k, v in envs.items():
        os.environ[k] = str(v)
    os.environ["CC_TEAM_HOME"] = home
    ops = os.path.join(CT, "ops")
    if ops not in sys.path:
        sys.path.insert(0, ops)
    if CT not in sys.path:
        sys.path.insert(0, CT)
    os.makedirs(os.path.join(home, "state"), exist_ok=True)
    wakep = os.path.join(home, "wake.calls")
    open(wakep, "w", encoding="utf-8").close()
    os.environ["FUSE_STUB_WAKE"] = wakep
    return importlib.import_module("mergetrain")


def make_repo(tag):
    """fixture 仓（同 merge-train-check）：main 带 select.sh/all.sh 桩；工作树停在 lane。"""
    d = os.path.join(SB, tag)
    os.makedirs(os.path.join(d, "cc-team", "tests"), exist_ok=True)
    os.makedirs(os.path.join(d, "cc-team", "ops"), exist_ok=True)
    gitc(d, "init", "-q", "-b", "main")
    gitc(d, "config", "user.email", "ts-test@local")
    gitc(d, "config", "user.name", "ts-test")
    gitc(d, "config", "core.autocrlf", "false")
    open(os.path.join(d, "cc-team", "tests", "all.sh"), "w", newline="\n").write(
        "#!/usr/bin/env bash\necho ALL_SUITES_PASS\n")
    open(os.path.join(d, "cc-team", "tests", "select.sh"), "w", newline="\n").write(
        '#!/usr/bin/env bash\nfor f in "$@"; do case "$f" in cc-team/ops/*) echo "PARTIAL: bash tests/all.sh" ;; '
        '*) echo "FULL: bash tests/all.sh" ;; esac; done\n')
    open(os.path.join(d, "cc-team", "ops", "seed.py"), "w", newline="\n").write("X = 1\n")
    open(os.path.join(d, "README.md"), "w", encoding="utf-8", newline="\n").write("seed\n")
    gitc(d, "add", "-A"); gitc(d, "commit", "-qm", "seed main")
    seed = gitc(d, "rev-parse", "main")[1]
    gitc(d, "checkout", "-qb", "lane")
    return d, seed


def lane_commit(d, files, msg):
    for name, body in files.items():
        p = os.path.join(d, *(name.split("/")))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8", newline="\n").write(body)
    gitc(d, "add", "-A"); gitc(d, "commit", "-qm", msg)
    return gitc(d, "rev-parse", "HEAD")[1]


def mk_card(cli, cid, repo, verify, files):
    argv = ["create", cid, "--title", "多锚闸沙箱卡 %s" % cid, "--lanes", "w1",
            "--verify", verify, "--repo", repo, "--files", files]
    cli.run(argv)
    cli.run(["claim", cid, "--owner", "w1"])


def state_of(team):
    return json.load(open(os.path.join(team, "state", "merge-train.json"), encoding="utf-8"))


def alerts_of(team):
    try:
        return json.load(open(os.path.join(team, "state", "alerts.json"), encoding="utf-8")).get("alerts", {})
    except (OSError, ValueError):
        return {}


def wake_lines(home, needle):
    try:
        return [l for l in open(os.path.join(home, "wake.calls"), encoding="utf-8") if needle in l]
    except OSError:
        return []


def ev_text(team):
    try:
        return open(os.path.join(team, "state", "events.log"), encoding="utf-8").read()
    except OSError:
        return ""


def card_of(team, cid):
    g = json.load(open(os.path.join(team, "state", "graph.json"), encoding="utf-8"))
    return (g.get("cards") or g.get("nodes") or {}).get(cid) or {}


# ═════════════════ TC0 静态同源闸（判据漂移先红=卡面「禁拷贝造成双处漂移」的机读形态） ═════════════════
mt_src = open(os.path.join(CT, "ops", "mergetrain.py"), encoding="utf-8").read()
ck("TC0a mergetrain 引用 fuse 判据三件+唤人通道（同源单点）",
   "s3_tipscan_filter" in mt_src and "s3_tipscan_decide" in mt_src
   and "_done_events_count" in mt_src and "run_pm_wake" in mt_src, "四引用齐=判定/兜底/唤人全 import")
ck("TC0b 判据零拷贝：mergetrain 本地不得 def s3_tipscan（拷贝=两处漂移温床，卡面纪律）",
   "def s3_tipscan" not in mt_src and "def _done_events_count" not in mt_src, "")
ck("TC0c 两支挂闸在位：队列真合前 + _ext_merge_closure 转正前",
   '_tipscan_gate(cid, c, repo, ref, team, apply, log, state, cards, git, "队列并线")' in mt_src
   and '_tipscan_gate(cid, c, repo, ref, team, apply, log, state, cards, git, "外合转正")' in mt_src, "")
fuse_src = open(os.path.join(CT, "ops", "fuse.py"), encoding="utf-8").read()
ck("TC0d fuse 侧零改动面：S3 件不得出现 TRAIN 文案（闸在火车自家，S3 语义不受染）",
   "TRAIN" not in fuse_src, "")

# ═════════════════ M 多锚队列挡并 + 键 + 叫PM + 冷却单发 ═════════════════
t = os.path.join(SB, "m"); repo, seed = make_repo("m-repo")
shaA = lane_commit(repo, {"marker.txt": "done\n"}, "feat(TC-M1): 交卷半边")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
import ccards.cli as _cli
mk_card(_cli, "TC-M1", repo, "cat marker.txt", "marker.txt")
_cli.run(["done", "TC-M1", "--owner", "w1", "--commit", shaA, "--repo", repo])
lane_commit(repo, {"evidence.md": "后半边\n"}, "docs(TC-M1): 证据文后半边")   # 锚外同卡面候并
g1 = mt.run_ring(team=t, apply=True, log=lambda s: print("   m1| " + str(s)[:160]))
tip_m1 = gitc(repo, "rev-parse", "main")[1]
rec_m1 = state_of(t)["cards"].get("TC-M1", {})
key_m1 = [k for k in alerts_of(t) if "待合·多锚(TRAIN)" in k and "TC-M1" in k]
ck("M1 多锚挡并：主干纹丝不动(=seed)+stage 留 passed+计数 tipscan=1",
   g1.get("tipscan") == 1 and tip_m1 == seed and rec_m1.get("stage") == "passed",
   "tipscan=%s main=%s stage=%s" % (g1.get("tipscan"), tip_m1[:8], rec_m1.get("stage")))
ck("M1b 键级 ack：「待合·多锚(TRAIN) TC-M1 …1 笔候并」进聚合账且带 ack 字段",
   key_m1 and "1 笔候并" in key_m1[0] and "ack" in alerts_of(t)[key_m1[0]], key_m1 and key_m1[0][:120])
ck("M1c 叫 PM：run_pm_wake 通道唤 1 笔，reason 带卡号+多锚存疑+禁单锚",
   len(wake_lines(t, "TC-M1")) == 1 and "多锚存疑" in wake_lines(t, "TC-M1")[0]
   and "禁单锚" in wake_lines(t, "TC-M1")[0], "".join(wake_lines(t, "TC-M1"))[:200])
ck("M1d events 痕：MERGE-TRAIN-TIPSCAN 多锚存疑(git面·队列并线) 锚外1笔",
   "MERGE-TRAIN-TIPSCAN 多锚存疑(git面·队列并线) 锚外1笔" in ev_text(t), "")
g2 = mt.run_ring(team=t, apply=True, log=lambda s: print("   m2| " + str(s)[:160]))
ck("M2 二圈仍挡且唤不重复（run_pm_wake 冷却=键级单发落点）；键 count 累计",
   g2.get("tipscan") == 1 and gitc(repo, "rev-parse", "main")[1] == seed
   and len(wake_lines(t, "TC-M1")) == 1 and alerts_of(t)[key_m1[0]].get("count", 0) >= 2,
   "count=%s wake=%d" % (alerts_of(t)[key_m1[0]].get("count"), len(wake_lines(t, "TC-M1"))))

# ═════════════════ B 撤闸差分对：摘 _tipscan_gate=恒放行 → 同一多锚交付真被并（M 的绿全凭那道闸） ═════════════════
t = os.path.join(SB, "b"); repo, seed = make_repo("b-repo")
shaA = lane_commit(repo, {"marker.txt": "done\n"}, "feat(TC-B1): 交卷半边")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
import ccards.cli as _cli
mk_card(_cli, "TC-B1", repo, "cat marker.txt", "marker.txt")
_cli.run(["done", "TC-B1", "--owner", "w1", "--commit", shaA, "--repo", repo])
lane_commit(repo, {"evidence.md": "后半边\n"}, "docs(TC-B1): 证据文后半边")
mt._tipscan_gate = lambda *a, **k: False        # 撤闸（等价修前世界：火车无多锚扫描）
g = mt.run_ring(team=t, apply=True, log=lambda s: print("   b| " + str(s)[:160]))
ck("B1 撤闸必红差分对：无闸=残半真入主干（merged=1 且锚外半边没进=M1b 断言的真伪面）",
   g.get("merged") == 1 and state_of(t)["cards"].get("TC-B1", {}).get("stage") == "merged"
   and gitc(repo, "merge-base", "--is-ancestor", shaA, "main", chk=False)[0] == 0
   and gitc(repo, "merge-base", "--is-ancestor",
             gitc(repo, "rev-parse", "lane")[1], "main", chk=False)[0] != 0,
   "merged=%s（有闸世界 M 场此断言必反）" % g.get("merged"))
ck("B2 撤闸世界零键零唤（M 场键/唤/痕三件全挂闸上）",
   not [k for k in alerts_of(t) if "多锚(TRAIN)" in k] and not wake_lines(t, "TC-B1"), "")

# ═════════════════ F 锚外只带别卡号=泳道已挪单，本卡照常并 ═════════════════
t = os.path.join(SB, "f"); repo, seed = make_repo("f-repo")
shaA = lane_commit(repo, {"marker.txt": "done\n"}, "feat(TC-F1): 交卷")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
import ccards.cli as _cli
mk_card(_cli, "TC-F1", repo, "cat marker.txt", "marker.txt")
_cli.run(["done", "TC-F1", "--owner", "w1", "--commit", shaA, "--repo", repo])
lane_commit(repo, {"other.md": "别单\n"}, "fix(TC-OTHER-9): 泳道挪去下一单")
g = mt.run_ring(team=t, apply=True, log=lambda s: print("   f| " + str(s)[:160]))
ck("F 归属过滤不过度挡：锚外只带别卡号 → 本卡照常并+VERIFIED",
   g.get("merged") == 1 and g.get("tipscan", 0) == 0
   and card_of(t, "TC-F1").get("status") == "VERIFIED"
   and not [k for k in alerts_of(t) if "多锚(TRAIN)" in k and "TC-F1" in k],
   "merged=%s tipscan=%s" % (g.get("merged"), g.get("tipscan")))

# ═════════════════ S 单锚卡（锚=树尖）照常并（恒挡世界此处必红） ═════════════════
t = os.path.join(SB, "s"); repo, seed = make_repo("s-repo")
shaA = lane_commit(repo, {"marker.txt": "done\n"}, "feat(TC-S1): 完整交付一笔净")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
import ccards.cli as _cli
mk_card(_cli, "TC-S1T", repo, "cat marker.txt", "marker.txt")
_cli.run(["done", "TC-S1T", "--owner", "w1", "--commit", shaA, "--repo", repo])
g = mt.run_ring(team=t, apply=True, log=lambda s: print("   s| " + str(s)[:160]))
ck("S 单锚照常并：tip==锚 → (True,[]) 直过，merged=1 VERIFIED，键/唤零",
   g.get("merged") == 1 and g.get("tipscan", 0) == 0
   and card_of(t, "TC-S1T").get("status") == "VERIFIED"
   and not wake_lines(t, "TC-S1T") and not [k for k in alerts_of(t) if "多锚(TRAIN)" in k],
   "merged=%s" % g.get("merged"))

# ═════════════════ G 泳道叠卡=他卡在册交卷锚（台账归属剔除）不误挡（merge-train-check S2 实形的专锁钉） ═════════════════
t = os.path.join(SB, "g"); repo, seed = make_repo("g-repo")
shaA = lane_commit(repo, {"marker.txt": "g done\n"}, "feat(TC-G1): 头卡交卷")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
import ccards.cli as _cli
mk_card(_cli, "TC-G1", repo, "cat marker.txt", "marker.txt")
_cli.run(["done", "TC-G1", "--owner", "w1", "--commit", shaA, "--repo", repo])
shaB = lane_commit(repo, {"g2.txt": "next\n"}, "wip 叠卡：下一张卡先行交付（无 token 形态）")
mk_card(_cli, "TC-G2", repo, "cat marker.txt >/dev/null && cat g2.txt", "marker.txt,g2.txt")
_cli.run(["done", "TC-G2", "--owner", "w1", "--commit", shaB, "--repo", repo])
g1 = mt.run_ring(team=t, apply=True, log=lambda s: print("   g1| " + str(s)[:160]))
rec_g1 = state_of(t)["cards"].get("TC-G1", {})
ck("G 叠卡不误挡：锚后 commit 无 token 但=他卡(TC-G2)在册交卷锚 → 台账归属剔除，头卡并线 tipscan=0 零键",
   g1.get("tipscan", 0) == 0 and rec_g1.get("stage") == "merged"
   and not [k for k in alerts_of(t) if "多锚(TRAIN)" in k],
   "tipscan=%s stage=%s keys=%s" % (g1.get("tipscan"), rec_g1.get("stage"),
                                    [k[:40] for k in alerts_of(t) if "多锚" in k]))
for _ in range(2):                                       # 批容量不论，两圈必清队
    mt.run_ring(team=t, apply=True, log=lambda s: print("   g2| " + str(s)[:160]))
ck("G2 双卡双双 VERIFIED：被认领笔自提自并（锚=树尖），叠卡族零误伤",
   card_of(t, "TC-G1").get("status") == "VERIFIED" and card_of(t, "TC-G2").get("status") == "VERIFIED",
   "G1=%s G2=%s" % (card_of(t, "TC-G1").get("status"), card_of(t, "TC-G2").get("status")))

# ═════════════════ X 外合闭环：只外合锚=残半挡转正；树尖全外合=候并清零照常转正（77c5135a 实形） ═════════════════
t = os.path.join(SB, "x"); repo, seed = make_repo("x-repo")
mw = os.path.join(SB, "x-mainwt")
gitc(repo, "worktree", "add", "-q", mw, "main")                    # 主干有树可签出=PM 外合的手在这里
shaA = lane_commit(repo, {"marker.txt": "done\n"}, "feat(TC-X1): 交卷锚")
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
mt.RESIDENT_UNITS = []
import ccards.cli as _cli
mk_card(_cli, "TC-X1", repo, "cat marker.txt", "marker.txt")
_cli.run(["done", "TC-X1", "--owner", "w1", "--commit", shaA, "--repo", repo])
shaB = lane_commit(repo, {"evidence.md": "后半边\n"}, "docs(TC-X1): 证据文后半边")
gitc(mw, "merge", "--no-ff", "-m", "merge(TC-X1): PM 只外合卡面锚（残半外合）", shaA)   # BIND 险形
tip_x1 = gitc(repo, "rev-parse", "main")[1]
g = mt.run_ring(team=t, apply=True, log=lambda s: print("   x1| " + str(s)[:160]))
ck("X1 残半外合挡转正：卡留 DONE 不 VERIFIED，extmerged=0（_ext_route→closure→闸）",
   card_of(t, "TC-X1").get("status") == "DONE" and g.get("extmerged", 0) == 0
   and g.get("pending", 0) >= 1, "extmerged=%s pending=%s" % (g.get("extmerged"), g.get("pending")))
key_x1 = [k for k in alerts_of(t) if "待合·多锚(TRAIN)" in k and "TC-X1" in k]
ck("X1b 键+唤齐：「待合·多锚(TRAIN) TC-X1 …1 笔候并」+叫PM(reason 带外合转正位)",
   key_x1 and "1 笔候并" in key_x1[0] and wake_lines(t, "TC-X1")
   and "外合转正" in wake_lines(t, "TC-X1")[0], key_x1 and key_x1[0][:120])
ck("X1c 主干零扰动：火车没碰主干分毫（PM 外合后 tip 原样）",
   gitc(repo, "rev-parse", "main")[1] == tip_x1, "")
gitc(mw, "merge", "--no-ff", "-m", "merge(TC-X1): PM 把树尖也外合（定锚=树尖，77c5135a 同形）", shaB)
tip_x2 = gitc(repo, "rev-parse", "main")[1]
g = mt.run_ring(team=t, apply=True, log=lambda s: print("   x2| " + str(s)[:160]))
ck("X2 树尖全外合放行转正：候并清零 → 外线沙箱判据复跑+verdict PASS → VERIFIED+merged(外线)",
   g.get("extmerged") == 1 and card_of(t, "TC-X1").get("status") == "VERIFIED"
   and state_of(t)["cards"].get("TC-X1", {}).get("stage") == "merged"
   and "外线合入" in ev_text(t),
   "extmerged=%s stage=%s" % (g.get("extmerged"), state_of(t)["cards"].get("TC-X1", {}).get("stage")))
ck("X2b 零force：转正后主干 tip 分毫未动", gitc(repo, "rev-parse", "main")[1] == tip_x2, "")

# ═════════════════ E 兜底面接线：树尖读不出+同卡交卷流水 2 笔=挡（decide 同源，验装配非纯函数） ═════════════════
t = os.path.join(SB, "e")
os.makedirs(os.path.join(t, "state"), exist_ok=True)
mt = fresh(t)
mt.RESIDENT_UNITS = []
from ccards import events as _ev, graph as _gr
_gr.save({"id": "TC-E1", "title": "兜底面 fixture", "status": "DONE", "deps": [],
          "owner": "w1", "lanes": ["w1"], "verify": "cat marker.txt",
          "commit": "a" * 40, "repo": os.path.join(SB, "e-dead-repo")})
_ev.event("TC-E1", "CLAIMED", "DONE", "w1", "first submit")
_ev.event("TC-E1", "REJECTED", "DONE", "w1", "rework resubmit")
buf = []
blocked = mt._tipscan_gate("TC-E1", card_of(t, "TC-E1"), os.path.join(SB, "e-dead-repo"),
                           "main", t, True, buf.append, {"cards": {}},
                           {"TC-E1": card_of(t, "TC-E1")}, lambda rp, *a: (128, ""), "队列并线")
key_e1 = [k for k in alerts_of(t) if "待合·多锚(TRAIN)" in k and "TC-E1" in k]
ck("E 兜底面=挡：两仓探不出树尖+交卷流水2笔 → events 面键+唤PM（宁叫人不误并）",
   blocked is True and key_e1 and "2 笔候并 commit(events面)" in key_e1[0]
   and len(wake_lines(t, "TC-E1")) == 1, key_e1 and key_e1[0][:120])
open(os.path.join(t, "state", "events.log"), "w", encoding="utf-8").close()
_ev.event("TC-E1", "CLAIMED", "DONE", "w1", "single submit")
blocked2 = mt._tipscan_gate("TC-E1", card_of(t, "TC-E1"), os.path.join(SB, "e-dead-repo"),
                            "main", t, True, buf.append, {"cards": {}},
                            {"TC-E1": card_of(t, "TC-E1")}, lambda rp, *a: (128, ""), "队列并线")
ck("E2 交卷流水仅 1 笔=不疑放行（返工过的卡不永锁，decide U3 同义在火车侧装配成立）",
   blocked2 is False, "blocked2=%s" % blocked2)

# ═════════════════ 收口 ═════════════════
shutil.rmtree(SB, ignore_errors=True)
red = sum(1 for _, ok in RESULTS if not ok)
print("TRAIN_TIPSCAN n=%d red=%d %s" % (len(RESULTS), red, "ALL-GREEN" if red == 0 else "HAS-RED"))
sys.exit(0 if red == 0 else 1)
