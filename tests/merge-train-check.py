#!/usr/bin/env bash
# merge-train-check.py — REQ-MERGE-TRAIN-1 合并火车红绿锁（卡面④+⑤第四场景；
#   MECH-BLOCKED-EXTMERGE-1 第五场景=blocked 卡外合自动转正）。
# 双语头（MECH-BLOCKED-EXTMERGE-1 改版）：旧形「bash 壳 + PYEOF heredoc」只活 `bash <本文件>`
#   一案（REQ-MERGE-TRAIN-1 卡面判据形）；判据改 `python3 -I <本文件>` 形后，python 直跑旧文件
#   在 bash 行上必 SyntaxError（实锤 2026-11-03）。新形=把 bash 引导段整体藏进 python 三引号
#   串里：`''''true` 在 bash 眼里是 ''+''+true 拼成一个词（真 no-op），在 python 眼里是开串
#   （串首多出的那个 ' 无害）；bash 段查好解释器后 exec python 跑**同一文件**——无循环，python
#   侧那段只是被丢弃的串表达式；闭合 `'''` 行 bash 永远走不到（已 exec 换代）。两形通用。
#   ⚠ 维护约束：引导段与闭合 `'''` 之间不得再出现字面 `'''`（串合不上）；主体不读同目录
#   任何解释器会优先加载的旁路模块（-I 隔离）。
# 场景（每场景独立 temp 账本+独立 git 仓；场景间清 sys.modules 重 import，路径常量随 env 重算）：
#   TC0 静态闸：recycle 挂接/看板壳 14 列（试验/待合）/live.js 取数点/mergetrain 本体在位。
#   S1 干净卡全链自动转正：交卷即验绿(events stage=passed@) → 队列试合沙箱绿 → CAS 并线（双亲
#      merge 形）→ cmd_verdict PASS 真落 VERIFIED（REQ-MERGE-GATE-1 合入后自动满足）。
#   S1b 改动面锁红必拦 / S1c py_compile 红必拦（轻量三件任一件红=不并线，归因红步名）。
#   S2 时间差红绿锁：绿后主干推进 → 队列复验必拦（「队列复验FAIL@新主干」），主干纹丝不动，
#      打回①再入队再拦=稳态；S2b 撤针必红：把沙箱判据钉成假绿 → 同一残卡**真会**被并进主干
#      ——证明 S2 的绿确实全凭那道复验。
#   S3 冲突必 blocked：merge-tree 干跑撞车 → 不合不拆，辅账 blocked；看板纯函数 merge_train_scan
#      亮「待合·撞车·龄Xm」；滞留超阈报警进聚合账。
#   S4 生效锁（第四场景）：主干提交后常驻进程不重启必检出（NOT-ENERGIZED <件> <滞后分钟> 落
#      dispatch.log）；白名单件自动 restart；「重启后」报警消；非白名单只报不动手；摘时间钉
#      （宽限=一年）=必不响——证明响与不响都钉在版本比对上。
#   S5 blocked 卡外合自动转正（第五场景，MECH-BLOCKED-EXTMERGE-1）：撞车卡 blocked → PM 手
#      外合进主干 → 圈扫 is_ancestor 见外线 → 外线 tip 沙箱复跑判据 → cmd_verdict PASS 落
#      VERIFIED + stage=merged(外线)；撤针（is_ancestor 钉 False=外合扫描摘掉）必悬置不转正；
#      已外合卡不再冲 BLOCKED-STALE 错铃；主干分毫未动（零force零改哈希）。
#   S6 改动面锁**真实宿主契约**（MECH-TRAIN-ADR-1 ③；卡链原编号 S5/S6/S7，PM 合流轮 2026-11-03
#      因与本主干 S5 外合场景编号撞车，整体重编 S6/S7/S8，沙箱工件目录同步隔离）：桩 all.sh
#      自报 uname 与关键 env，断言宿主形态=火车进程同形（win 上 MINGW/MSYS 非 Linux=WSL 桥毒
#      宿主）、被测代码钉在沙箱 wt（非主仓工作树）、套件沙箱逐圈隔离。旧锁全用「只回显
#      ALL_SUITES_PASS」的死桩——桩永远不会告诉你宿主错了，这就是「锁绿实坏」的构造性成因
#      （火车 6 笔 fails=5 实录）。
#   S7 撤针必红：CARD_VERIFY_BASH 显式配毒（不存在路径）→ 改动面锁必红且套件零起（宁红不猜、
#      不回落裸 bash）。S6 绿 + S7 红 = 这根针两头都钉住。
#   S8 真 adversary.sh 全绿（不外求桩）：以 scope_env 同式契约环境跑**本树真实套件**，并预埋
#      看板 canary（BOARD_SELFAUDIT_PATH=rc3 报警探针）——套件若不密封（hermetic stub 行被删）
#      canary 必跑→套件必红；今天这病（判据四探活体看板）复发即被抓现行。
''''true
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY=""
# REQ-OSS-ENGINE-1：解释器候选零宿主字面量——CC_PY312_EXE 锚在场优先，缺省顺扫挂载视图
# /mnt/<盘>/Users/<u>/AppData/.../Python312 首个可执行者（本机取到的串与旧默认逐字相同）。
_p312="${CC_PY312_EXE:-}"
[ -z "$_p312" ] && for u in /mnt/[A-Za-z]*/Users/*; do
  [ -x "$u/AppData/Local/Programs/Python/Python312/python.exe" ] && _p312="$u/AppData/Local/Programs/Python/Python312/python.exe" && break
done
for c in python3 python py "${_p312}"; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "FAIL: merge-train-check 找不到 python 解释器"; exit 1; fi
exec "$PY" -I -X utf8 "$0"
'''
# -*- coding: utf-8 -*-
import importlib, json, os, re, shutil, subprocess, sys, tempfile, time

CT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CT not in sys.path:
    sys.path.insert(0, CT)
os.environ.pop("CC_AUTO_VERIFY", None)      # auto@done 首验通道关闭——本锁验的是火车①，别串账
os.environ["MERGE_TRAIN_STEP_TIMEOUT"] = "240"

OK_, FAIL = [], []
def ck(name, cond, detail=""):
    (OK_ if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + ("  [%s]" % detail if detail else ""))

def gitc(d, *a, chk=True):
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", d] + list(a),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    if chk and r.returncode != 0:
        raise RuntimeError("git %s rc=%s %s" % (" ".join(a), r.returncode, out[-200:]))
    return r.returncode, out

def fresh(home, **mt_env):
    """场景隔离：独立 CC_TEAM_HOME + 重 import（ccards/recycle/mergetrain 路径常量全在 import 期
    取值，清模块后随 env 重算——一进程跑全部场景不串账）。"""
    for m in list(sys.modules):
        if m == "ccards" or m.startswith("ccards.") or m in ("recycle", "mergetrain"):
            del sys.modules[m]
    for k, v in mt_env.items():
        os.environ[k] = str(v)
    os.environ["CC_TEAM_HOME"] = home
    ops = os.path.join(CT, "ops")
    for p in (ops,):
        if p not in sys.path:
            sys.path.insert(0, p)
    return importlib.import_module("mergetrain")

def ev_lines(team):
    try:
        return open(os.path.join(team, "state", "events.log"), encoding="utf-8").read().splitlines()
    except OSError:
        return []

def card(team, cid):
    g = json.load(open(os.path.join(team, "state", "graph.json"), encoding="utf-8"))
    return (g.get("cards") or g.get("nodes") or {}).get(cid) or {}

def make_repo(tag, scope_fail=False, sel_ops=False):
    """fixture 仓：main 带 cc-team/tests/{select.sh,all.sh} 桩（改动面锁在沙箱里**真跑**）；
    lane 分支带卡改动。返回 (repo, lane_sha)。scope_fail=True 时 all.sh 桩红。"""
    d = os.path.join(SB, tag)
    os.makedirs(os.path.join(d, "cc-team", "tests"), exist_ok=True)
    os.makedirs(os.path.join(d, "cc-team", "ops"), exist_ok=True)
    gitc(d, "init", "-q", "-b", "main")
    gitc(d, "config", "user.email", "train-test@local")
    gitc(d, "config", "user.name", "train-test")
    gitc(d, "config", "core.autocrlf", "false")
    allsh = "#!/usr/bin/env bash\necho SCOPE-RED\nexit 1\n" if scope_fail else \
            "#!/usr/bin/env bash\necho ALL_SUITES_PASS\n"
    sel = ('#!/usr/bin/env bash\n'
           'for f in "$@"; do case "$f" in cc-team/ops/*) echo "PARTIAL: bash tests/all.sh" ;; '
           '*) echo "FULL: bash tests/all.sh" ;; esac; done\n')
    open(os.path.join(d, "cc-team", "tests", "all.sh"), "w", newline="\n").write(allsh)
    open(os.path.join(d, "cc-team", "tests", "select.sh"), "w", newline="\n").write(sel)
    open(os.path.join(d, "cc-team", "ops", "seed.py"), "w", newline="\n").write("X = 1\n")
    open(os.path.join(d, "README.md"), "w", encoding="utf-8", newline="\n").write("line\n")
    gitc(d, "add", "-A"); gitc(d, "commit", "-qm", "seed main")
    gitc(d, "checkout", "-qb", "lane")
    return d, None

def lane_commit(d, files):
    for name, body in files.items():
        p = os.path.join(d, *(name.split("/")))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8", newline="\n").write(body)
    gitc(d, "add", "-A"); gitc(d, "commit", "-qm", "lane card work")
    return gitc(d, "rev-parse", "HEAD")[1]

def mk_card(team, cli, cid, repo, verify, files=""):
    # REQ-VERIFY-DRYRUN-1 适配：create 挂「发卡即考试」试跑闸，判据在一次性临时目录里试跑——
    # 旧形 test -f X 缺失时 rc=1 且尾痕空（规格明拒的形态），改 cat X（缺失打印 No such file
    # 报文）+ --files 声明同名缺失路径走 R2 red-first 豁免；train 侧在仓根跑，cat 与 test -f
    # 对「绿料文件存在」判定等价（沙箱 marker 均非空）。
    # ⚠ --files 连带激活 done 口越域闸（commands.py:902 起：旧 files=[] 走「卡无域跳过」）——
    # 声明必须覆盖该卡 done 提交的全部文件，故各场景按「提交文件全集」传，不只判据绿料。
    argv = ["create", cid, "--title", "火车沙箱卡 %s" % cid, "--lanes", "w1",
            "--verify", verify, "--repo", repo]
    if files:
        argv += ["--files", files]
    cli.run(argv)
    cli.run(["claim", cid, "--owner", "w1"])
    return cid

def state_of(team):
    return json.load(open(os.path.join(team, "state", "merge-train.json"), encoding="utf-8"))

def alerts_keys(team):
    try:
        return list(json.load(open(os.path.join(team, "state", "alerts.json"),
                                   encoding="utf-8")).get("alerts", {}))
    except (OSError, ValueError):
        return []

SB = tempfile.mkdtemp(prefix="cc-merge-train-lock-")
os.environ["MERGE_TRAIN_TMPDIR"] = SB
# MECH-TRAIN-ADR-1：锁的宿主自己先钉对。旧行 which("bash") 优先——win 任务 PATH 下 which
# 恰可能命中 System32 WSL 桥（本次事故本体），把毒值写进钉等于让 S5/S6 假红、且会**覆盖**
# ctrld 配好的真 Git Bash 钉。改：显式钉优先沿用（配了毒就让 _resolve_verify_bash 响亮 die，
# 不代配置洗白）；无显式钉且 which 落在 SystemRoot 下=不钉，交由其 Git Bash 候选自解析。
_exp = os.environ.get("CARD_VERIFY_BASH")
_w = _exp or shutil.which("bash") or "bash"
if _exp is None and os.name == "nt" and _w.lower().startswith(
        os.environ.get("SystemRoot", "C:\\Windows").lower()):
    pass
else:
    os.environ["CARD_VERIFY_BASH"] = _w

# ---------------- TC0 静态闸（撤掉任一挂接=本锁先红，防「代码在、链路断」） ----------------
rec_src = open(os.path.join(CT, "ops", "recycle.py"), encoding="utf-8").read()
sw_src = open(os.path.join(CT, "board", "status-web.py"), encoding="utf-8").read()
js_src = open(os.path.join(CT, "board", "live.js"), encoding="utf-8").read()
ck("TC0a recycle 挂火车(merge-tree/MERGE-TRAIN/train_scan/持管让路)",
   all(x in rec_src for x in ("MERGE-TRAIN", "merge-tree", "train_scan(", "train_held_ids")),
   "巡逻环挂接四要素齐")
ck("TC0b 看板壳 14 列含 试验|待合 连排",
   "<th>试验</th><th>待合</th>" in sw_src and sw_src.count("<col style=") >= 14
   and "merge_train_scan" in sw_src and "待合·撞车·龄" in sw_src, "")
ck("TC0c live.js 两格取数点唯一（TR 单点，两格同源于它）",
   "var TR = (_O && _O.train)" in js_src and js_src.count("_O.train") == 2
   and "(TR ? " in js_src and "(TR && TR.q" in js_src,
   "摘掉取数点=两列同失（红自证靶面）")

mt = fresh(os.path.join(SB, "t0"), MERGE_TRAIN_STALE_MIN=1)
ck("TC0d 火车本体闸：零force（真并只 merge --no-ff / update-ref 带旧值CAS）",
   'merge", "--no-ff"' in open(os.path.join(CT, "ops", "mergetrain.py"), encoding="utf-8").read()
   and "--force-with-lease" not in open(os.path.join(CT, "ops", "mergetrain.py"), encoding="utf-8").read(),
   "update-ref 通道 oldValues 必带（见 mergetrain CAS 段）")
# 名册口径改判（2026-11-04 夜账，锁维护非改产码）：业主 11-03 令删 WSL 后
# RESIDENT_UNITS 刻意清空=PARKED（mergetrain.py 源码注释在册），Windows 周期职责
# 全收进 cc-ctrld 进程内环。旧断言「cc-dispatch 白名单在」随发行版注销永红=删账后
# 未同步的锁。新断言=名册必须空（防有人偷偷塞回 systemd 依赖）+ PARKED 凭据在源码
# + 看板/recycle 仍不得进任何白名单。
with open(mt.__file__, encoding="utf-8") as _f:
    _mt_src = _f.read()
ck("TC0e 生效锁名册：删WSL后名册=PARKED空册（防偷塞回systemd），看板/recycle不在册",
   mt.RESIDENT_UNITS == [] and "PARKED" in _mt_src
   and not any("board" in str(u).lower() or "recycle" in str(u).lower() for u in mt.RESIDENT_UNITS),
   "units=%s parked_note_in_src=%s" % (mt.RESIDENT_UNITS, "PARKED" in _mt_src))

# ---------------- S1 干净卡全链自动转正（CAS 并线通道：main 未被任何树签出） ----------------
t = os.path.join(SB, "s1"); repo, _ = make_repo("s1-repo")
sha = lane_commit(repo, {"marker.txt": "done\n", "tools/ok.py": "Y = 2\n"})
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
from ccards import cli as _cli
mt.RESIDENT_UNITS = []
mk_card(t, _cli, "TC-S1", repo, "cat marker.txt", "marker.txt,tools/ok.py")
_cli.run(["done", "TC-S1", "--owner", "w1", "--commit", sha, "--repo", repo])
main_tip = gitc(repo, "rev-parse", "main")[1]          # 并线前主干（真并后应=merge 的第一亲）
g1 = mt.run_ring(team=t, apply=True, log=lambda s: print("   s1| " + str(s)[:150]))
rec = state_of(t)["cards"].get("TC-S1", {})
par = gitc(repo, "rev-list", "--parents", "-n", "1", "main")[1].split()
ck("S1① 交卷即验绿 + events 痕 stage=passed@",
   g1["passed"] == 1 and any("stage=passed@%s" % sha[:12] in l and "|mergetrain|" in l for l in ev_lines(t)),
   "passed=%s" % g1.get("passed"))
ck("S1② 队列试合沙箱三件跑过并 CAS 真并线（merge 双亲形：亲一=旧主干 亲二=交付）",
   rec.get("stage") == "merged" and len(par) >= 3 and par[1] == main_tip and sha in par[2:],
   "rec=%s parents=%s old=%s" % (rec.get("stage"), par[1:3], main_tip))
ck("S1② 交付 commit 已在主干（REQ-MERGE-GATE-1 终态断言满足）",
   mt.is_ancestor(repo, sha, "main") is True, "")
ck("S1② cmd_verdict 真落 VERIFIED（走正式通道非裸改）",
   card(t, "TC-S1").get("status") == "VERIFIED", "卡面=%s" % card(t, "TC-S1").get("status"))
ck("S1② 沙箱用完即删（无泄漏 worktree）",
   len([w for w in gitc(repo, "worktree", "list", "--porcelain")[1].splitlines()
        if w.startswith("worktree ")]) == 1, "")

# ---------------- S1b/S1c 试合三件任红=不并线（改动面锁 / py_compile） ----------------
for tag, sc_fail, files, ver, want in (
        ("s1b", True, {"marker.txt": "ok\n"}, "cat marker.txt", "改动面锁"),
        ("s1c", False, {"marker.txt": "ok\n", "broken.py": "def f( :\\\n"}, "cat marker.txt", "py_compile")):
    t2 = os.path.join(SB, tag); r2, _ = make_repo("%s-repo" % tag, scope_fail=sc_fail)
    s2 = lane_commit(r2, files)
    mt = fresh(t2, MERGE_TRAIN_STALE_MIN=60)
    import ccards.cli as _c2
    mt.RESIDENT_UNITS = []
    mk_card(t2, _c2, "TC-%s" % tag.upper(), r2, ver, ",".join(files))
    _c2.run(["done", "TC-%s" % tag.upper(), "--owner", "w1", "--commit", s2, "--repo", r2])
    tip0 = gitc(r2, "rev-parse", "main")[1]
    mt.run_ring(team=t2, apply=True, log=lambda s: print("   %s| " % tag + str(s)[:150]))
    rec2 = state_of(t2)["cards"].get("TC-%s" % tag.upper(), {})
    ck("%s 沙箱步「%s」红→打回①且主干纹丝不动" % (tag, want),
       rec2.get("stage") == "refailed" and rec2.get("fail_step") == want
       and gitc(r2, "rev-parse", "main")[1] == tip0 and card(t2, "TC-%s" % tag.upper()).get("status") == "DONE",
       "rec=%s/%s" % (rec2.get("stage"), rec2.get("fail_step")))

# ---------------- S2 时间差红绿锁（双卡队列：头卡先并，主干推进后次卡必拦） ----------------
# 为什么必须双卡：run_ring 同一圈先①后②，单卡一旦验绿当圈就并走了——「候合队列中」
# 的状态抓不住，破坏提交永远追不上它。故 TC-S2A 排队头先并掉（mw 先挂=顺带覆盖
# 主干已签出的 merge --no-ff 在树并线路径），TC-S2B 留队（MERGE_BATCH=1 一圈只并一张），
# 再让主干推进——时间差的红绿就发生在 B 的队列复验上。
t = os.path.join(SB, "s2"); repo, _ = make_repo("s2-repo")
shaA = lane_commit(repo, {"featureA.txt": "A work\n"})
shaB = lane_commit(repo, {"featureB.txt": "B work\n"})
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
import ccards.cli as _c3
mt.RESIDENT_UNITS = []
mw = os.path.join(SB, "s2-mainwt")                      # 先签出主干：A 走「在树 merge --no-ff」真通道
gitc(repo, "worktree", "add", "-q", mw, "main")
# 道锁「一道一活」：done 才解锁，故双卡必须 claim→done 交替，不能连 claim 两张
mk_card(t, _c3, "TC-S2A", repo, "cat featureA.txt", "featureA.txt")
_c3.run(["done", "TC-S2A", "--owner", "w1", "--commit", shaA, "--repo", repo])
mk_card(t, _c3, "TC-S2B", repo,
        "cat featureA.txt >/dev/null && cat featureB.txt >/dev/null "
        "&& ! cat sabotage.txt >/dev/null 2>&1",
        "featureA.txt,featureB.txt")
_c3.run(["done", "TC-S2B", "--owner", "w1", "--commit", shaB, "--repo", repo])
mt.run_ring(team=t, apply=True, log=lambda s: print("   s2r1| " + str(s)[:150]))
ck("S2 前置：头卡 A 绿并转正（在树并线），次卡 B 只候合未并（队列存在的前提）",
   state_of(t)["cards"].get("TC-S2A", {}).get("stage") == "merged"
   and card(t, "TC-S2A").get("status") == "VERIFIED"
   and state_of(t)["cards"].get("TC-S2B", {}).get("stage") == "passed",
   "A=%s B=%s" % (state_of(t)["cards"].get("TC-S2A", {}).get("stage"),
                  state_of(t)["cards"].get("TC-S2B", {}).get("stage")))
# 主干推进：B 候合期间，另一线在 mw 提交破坏文件（B 交卷时不存在=①当时确绿过）
open(os.path.join(mw, "sabotage.txt"), "w", newline="\n").write("trunk moved\n")
gitc(mw, "add", "-A"); gitc(mw, "commit", "-qm", "trunk moves on")
tip1 = gitc(repo, "rev-parse", "main")[1]
mt.run_ring(team=t, apply=True, log=lambda s: print("   s2r2| " + str(s)[:150]))
rec = state_of(t)["cards"].get("TC-S2B", {})
ck("S2 队列复验拦住：红归因「队列复验FAIL@新主干」+ 主干未被火车再动",
   rec.get("stage") == "refailed" and rec.get("fail_step") == "本卡判据"
   and gitc(repo, "rev-parse", "main")[1] == tip1
   and any("队列复验FAIL@新主干@%s" % tip1[:8] in l and "|mergetrain|" in l for l in ev_lines(t))
   and card(t, "TC-S2B").get("status") == "DONE",
   "rec=%s 步=%s" % (rec.get("stage"), rec.get("fail_step")))
_st = state_of(t)
_st["cards"]["TC-S2B"]["fail_ts"] = time.time() - 12 * 60   # 退避窗（≥RETRY_MIN=10min）出账：①到期重验
json.dump(_st, open(os.path.join(t, "state", "merge-train.json"), "w", encoding="utf-8"))
mt.run_ring(team=t, apply=True, log=lambda s: print("   s2r3| " + str(s)[:150]))
ck("S2 打回①再入队再拦=稳态（主线不追平，卡永不混进主干）",
   sum(1 for l in ev_lines(t) if "队列复验FAIL@新主干" in l) >= 2
   and state_of(t)["cards"].get("TC-S2B", {}).get("stage") == "refailed"
   and card(t, "TC-S2B").get("status") == "DONE", "")
# S2b 撤针必红：把沙箱「本卡判据」钉成假绿 → 同一残卡真会被并进主干——S2 的绿全凭那道复验。
# 与 S2 同形双卡：A 先干净并掉，主干破坏后 B 候合；钉绿 → B 越过真复验混进主干。
# 注：转正那一层（cmd_verdict 自带重跑）若也红，卡会停在 DONE——但**污染已进主干**，
# 所以判据钉的是「并线发生 + sabotage 入主干」这两件事，证明这层防线全靠本卡判据。
t = os.path.join(SB, "s2b"); repo, _ = make_repo("s2b-repo")
shaA = lane_commit(repo, {"featureA.txt": "A work\n"})
shaB = lane_commit(repo, {"featureB.txt": "B work\n"})
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60)
import ccards.cli as _c4
mt.RESIDENT_UNITS = []
mw = os.path.join(SB, "s2b-mainwt")
gitc(repo, "worktree", "add", "-q", mw, "main")
mk_card(t, _c4, "TC-S2BA", repo, "cat featureA.txt", "featureA.txt")
_c4.run(["done", "TC-S2BA", "--owner", "w1", "--commit", shaA, "--repo", repo])
mk_card(t, _c4, "TC-S2BB", repo,
        "cat featureA.txt >/dev/null && cat featureB.txt >/dev/null "
        "&& ! cat sabotage.txt >/dev/null 2>&1",
        "featureA.txt,featureB.txt")
_c4.run(["done", "TC-S2BB", "--owner", "w1", "--commit", shaB, "--repo", repo])
mt.run_ring(team=t, apply=True, log=lambda s: print("   s2br1| " + str(s)[:150]))
open(os.path.join(mw, "sabotage.txt"), "w", newline="\n").write("trunk moved\n")
gitc(mw, "add", "-A"); gitc(mw, "commit", "-qm", "trunk moves on")
assert state_of(t)["cards"].get("TC-S2BB", {}).get("stage") == "passed", "S2b 前置：B 应在候合"
_orig = mt.card_verify_step
mt.card_verify_step = lambda root, verify, rm: (0, "PINNED-GREEN(撤针)")
try:
    mt.run_ring(team=t, apply=True, log=lambda s: print("   s2br2| " + str(s)[:150]))
finally:
    mt.card_verify_step = _orig
merged_b = state_of(t)["cards"].get("TC-S2BB", {}).get("stage") == "merged"
sab_in_main = gitc(repo, "show", "main:sabotage.txt")[0] == 0
ck("S2b 撤针必红：假绿钉住复验→残卡真混进主干（证明 S2 的绿全凭那道复验；此钉若常驻=事故）",
   merged_b and sab_in_main,
   "并线=%s sabotage入主干=%s（红=火车失去本卡判据会放行坏卡）" % (merged_b, sab_in_main))

# ---------------- S3 冲突必 blocked + 看板「待合·撞车·龄Xm」+ 滞留报警 ----------------
t = os.path.join(SB, "s3"); repo, _ = make_repo("s3-repo")
sha = lane_commit(repo, {"README.md": "lane side\n", "marker.txt": "ok\n"})   # ①判据绿料
mw = os.path.join(SB, "s3-mainwt")                                            # 主干同文件异侧改=撞车源
gitc(repo, "worktree", "add", "-q", mw, "main")
open(os.path.join(mw, "README.md"), "w", encoding="utf-8", newline="\n").write("trunk side\n")
gitc(mw, "add", "-A"); gitc(mw, "commit", "-qm", "trunk edits README")
tip0 = gitc(repo, "rev-parse", "main")[1]
mt = fresh(t, MERGE_TRAIN_STALE_MIN=1)
import ccards.cli as _c5
mt.RESIDENT_UNITS = []
mk_card(t, _c5, "TC-S3", repo, "cat marker.txt", "marker.txt,README.md")
_c5.run(["done", "TC-S3", "--owner", "w1", "--commit", sha, "--repo", repo])
mt.run_ring(team=t, apply=True, log=lambda s: print("   s3r1| " + str(s)[:150]))
rec = state_of(t)["cards"].get("TC-S3", {})
ck("S3 merge-tree 干跑撞车→integrations=blocked（不合不拆，主干分毫未动）",
   rec.get("stage") == "blocked" and rec.get("integrations") == "blocked"
   and "README.md" in rec.get("conflict_files", "")
   and gitc(repo, "rev-parse", "main")[1] == tip0
   and any("integrations=blocked@" in l and "|mergetrain|" in l for l in ev_lines(t)),
   "rec=%s files=%s" % (rec.get("stage"), rec.get("conflict_files")))
_st = state_of(t)
_st["cards"]["TC-S3"]["blocked_ts"] = time.time() - 31 * 60     # 推龄 31min（>30min 规格阈）
json.dump(_st, open(os.path.join(t, "state", "merge-train.json"), "w", encoding="utf-8"))
mt.run_ring(team=t, apply=True, log=lambda s: print("   s3r2| " + str(s)[:150]))
ck("S3 滞留>30min → 报警行进聚合账（稳定键无分钟 OPT1）",
   any(k.startswith("MERGE-TRAIN-BLOCKED-STALE TC-S3") for k in alerts_keys(t)),
   "keys=%s" % [k[:60] for k in alerts_keys(t)][:4])
import importlib.util as _ilu
spec = _ilu.spec_from_file_location("sw_probe", os.path.join(CT, "board", "status-web.py"))
sw = _ilu.module_from_spec(spec)
# 模块顶层 PORT=int(sys.argv[1])：本脚本 argv[1]=cc-team 路径，导入前须顶掉，防误当端口炸
_av0, _av1 = sys.argv[0], sys.argv[1:]
sys.argv = [_av0, "18999"]
try:
    spec.loader.exec_module(sw)
finally:
    sys.argv = [_av0] + _av1
cells = sw.merge_train_scan(ev_lines(t), [{"id": "TC-S3", "status": "DONE"}],
                            {"TC-S3": rec}, now=time.time() + 40 * 60)
c3 = cells.get("TC-S3", {})
ck("S3 看板纯函数亮「待合·撞车·龄Xm」（试验列=撞车；数字服务端算好，页面零自算）",
   c3.get("t") == "撞车" and re.match(r"^待合·撞车·龄\d+m$", str(c3.get("q"))),
   "cell=%s/%s" % (c3.get("t"), c3.get("q")))
sums = sw.merge_train_sum([{"id": "TC-S3", "status": "DONE"}], cells)
ck("S3 汇总数与交卷池同源（DONE 现读=1，撞车=1）",
   sums.get("交卷池") == 1 and sums.get("撞车") == 1, str(sums))

# ---------------- S4 生效锁（第四场景）：主干提交不重启必检出；重启后报警消；白名单外不动手 ----------------
t = os.path.join(SB, "s4")
os.makedirs(os.path.join(t, "cc-team", "ops"), exist_ok=True)
repo4 = t
gitc(repo4, "init", "-q", "-b", "main")
gitc(repo4, "config", "user.email", "x@y"); gitc(repo4, "config", "user.name", "x")
open(os.path.join(repo4, "cc-team", "ops", "dispatch.py"), "w", newline="\n").write("print(1)\n")
gitc(repo4, "add", "-A"); gitc(repo4, "commit", "-qm", "lifeline commit")
life = int(gitc(repo4, "log", "-1", "--format=%ct", "--", "cc-team/ops/dispatch.py")[1])
calls = []
# 每单元独立「进程起时」：restart 只翻自己件的账——cc-other 不在白名单，别人重启与它无关
_st4 = {"cc-dispatch.service": life - 600, "cc-other.service": life - 600}
def fake_sh(cmdline):
    if cmdline.startswith("systemctl --user show"):
        ts = time.strftime("%a %Y-%m-%d %H:%M:%S", time.localtime(_st4[cmdline.split()[3]]))
        return 0, ("MainPID=4242\nExecMainStartTimestamp=%s\nActiveState=active\nUnitFileState=enabled\n" % ts)
    if cmdline.startswith("date -d"):
        m = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", cmdline)
        if not m:
            return 1, ""
        return 0, str(int(time.mktime(time.strptime(m.group(0), "%Y-%m-%d %H:%M:%S"))))
    if cmdline.startswith("systemctl --user restart"):
        calls.append(cmdline)
        _st4[cmdline.split()[3]] = time.time() + 5      # 重启=该件进程起时翻篇到当下
        return 0, "restarted"
    return 127, "?"
def _reset4():
    for k in _st4:
        _st4[k] = life - 600
mt.RESIDENT_UNITS = [
    {"part": "cc-dispatch", "unit": "cc-dispatch.service",
     "lifelines": ["cc-team/ops/dispatch.py"], "auto_restart": True},
    {"part": "cc-other", "unit": "cc-other.service",
     "lifelines": ["cc-team/ops/dispatch.py"], "auto_restart": False},
]
lines = []
en1 = mt.energizen_scan(repo4, t, True, lines.append, state={"cards": {}, "restarts": {}}, run_sh=fake_sh)
dlog = open(os.path.join(t, "logs", "dispatch.log"), encoding="utf-8").read()
ck("S4 主干提交后不重启→NOT-ENERGIZED <件> <滞后分钟> 落 dispatch.log（滞后≈10min）",
   en1["alarms"] == 2 and re.search(r"NOT-ENERGIZED cc-dispatch \d+", dlog)
   and re.search(r"NOT-ENERGIZED cc-other \d+", dlog), "alarms=%s" % en1["alarms"])
ck("S4 白名单件自动 restart 恰一次；白名单外件只报不动手",
   len([c for c in calls if "cc-dispatch" in c]) == 1 and not any("cc-other" in c for c in calls),
   "calls=%s" % calls)
en2 = mt.energizen_scan(repo4, t, True, lines.append, state={"cards": {}, "restarts": {"cc-dispatch": time.time()}}, run_sh=fake_sh)
ck("S4 「重启后」报警消：白名单件起时翻篇→ENERGIZED 可见行；非白名单件无人翻篇→报警持续（只报不动手）",
   any(l.startswith("ENERGIZED cc-dispatch") for l in lines)
   and en2["alarms"] == 1 and any("NOT-ENERGIZED cc-other" in l for l in lines)
   and len([c for c in calls if "cc-dispatch" in c]) == 1,   # 冷却窗外也不二次拉起（起时已新）
   "alarms=%s（cc-other 应持续响=件没重启；dispatch 消=重启翻篇）" % en2["alarms"])
old_grace = mt.ENERGIZE_GRACE_MIN
mt.ENERGIZE_GRACE_MIN = 525600.0                        # 撤针：时间钉放宽到一年
_reset4()
en3 = mt.energizen_scan(repo4, t, True, lines.append, state={"cards": {}, "restarts": {}}, run_sh=fake_sh)
mt.ENERGIZE_GRACE_MIN = old_grace
ck("S4 撤针必红面：把版本钉拆掉（宽限=一年）→ 同案不响——响必响在「进程起时<命脉提交」比对上",
   en3["alarms"] == 0 and not any("cc-other" in c for c in calls), "撤钉 alarms=%s" % en3["alarms"])
_reset4()
en4 = mt.energizen_scan(repo4, t, False, lines.append, state={"cards": {}, "restarts": {}}, run_sh=fake_sh)
ck("S4 dry=报警可见但零 restart 子进程零 dispatch.log 新笔（只点名纪律）",
   en4["alarms"] == 2 and not any("cc-other" in c for c in calls), "")

# ---------------- S5 blocked 卡外合自动转正（第五场景，MECH-BLOCKED-EXTMERGE-1） ----------------
# 病因（2026-11-03 实锤）：queue_of 只纳 passed → blocked 卡不进队列，线480 is_ancestor 让路
#   分支永不可达；done-sweep 跳 HELD_STAGES（blocked/merged 都跳）→ PM 手把 blocked 卡外合后
#   卡悬 DONE 只能手工 verdict 补章；推龄 >30min 的 BLOCKED-STALE 报警行还照冲已外合卡亮。
#   本场景：双撞车卡 blocked → PM 手外合 → 圈扫自动转正（判据在**外线 tip 沙箱**复跑，
#   cmd_verdict 正式通道落 VERIFIED）；撤针=is_ancestor 钉 False（外合扫描瞎）必永悬 DONE。
t = os.path.join(SB, "s5"); repo, _ = make_repo("s5-repo")
mw = os.path.join(SB, "s5-mainwt")
gitc(repo, "worktree", "add", "-q", mw, "main")                    # 主干有树可签出=PM 外合的手在这里
shaA = lane_commit(repo, {"README.md": "lane A side\n", "marker5a.txt": "a\n"})
shaB = lane_commit(repo, {"README.md": "lane B side\n", "marker5b.txt": "b\n"})
open(os.path.join(mw, "README.md"), "w", encoding="utf-8", newline="\n").write("trunk side\n")
gitc(mw, "add", "-A"); gitc(mw, "commit", "-qm", "trunk edits README")   # README 两侧改=撞车源
mt = fresh(t, MERGE_TRAIN_STALE_MIN=60, MERGE_TRAIN_BATCH=2)
import ccards.cli as _c6
mt.RESIDENT_UNITS = []
mk_card(t, _c6, "TC-S5A", repo, "cat marker5a.txt", "marker5a.txt,README.md")
_c6.run(["done", "TC-S5A", "--owner", "w1", "--commit", shaA, "--repo", repo])
mk_card(t, _c6, "TC-S5B", repo, "cat marker5b.txt", "marker5b.txt,README.md")
_c6.run(["done", "TC-S5B", "--owner", "w1", "--commit", shaB, "--repo", repo])
mt.run_ring(team=t, apply=True, log=lambda s: print("   s5r1| " + str(s)[:150]))
_st = state_of(t)
ck("S5 前置：双撞车卡如预期 blocked（外合扫描的靶面）",
   _st["cards"].get("TC-S5A", {}).get("stage") == "blocked"
   and _st["cards"].get("TC-S5B", {}).get("stage") == "blocked",
   "A=%s B=%s" % (_st["cards"].get("TC-S5A", {}).get("stage"),
                  _st["cards"].get("TC-S5B", {}).get("stage")))
# PM 手工外合整个 lane（解冲突=README 取 PM 合成版，marker 随并入主干）——火车此时不在拍上
rc, _ = gitc(mw, "merge", "--no-ff", "--no-edit", "-m", "PM ext merge lane", shaB, chk=False)
if rc != 0:                                                        # 预期撞 README：人工裁决
    open(os.path.join(mw, "README.md"), "w", encoding="utf-8", newline="\n").write("PM resolved\n")
    gitc(mw, "add", "-A")
    rc, out = gitc(mw, "commit", "--no-edit", "-m", "PM ext merge lane (README resolved)", chk=False)
    assert rc == 0, "S5 前置失败：PM 外合提交未落地 %s" % out[-200:]
tip_ext = gitc(repo, "rev-parse", "main")[1]
ck("S5 前置：两交付已被外合在主干（自 blocked 后火车手未碰主干分毫）",
   gitc(repo, "merge-base", "--is-ancestor", shaA, "main", chk=False)[0] == 0
   and gitc(repo, "merge-base", "--is-ancestor", shaB, "main", chk=False)[0] == 0,
   "tip=%s" % tip_ext[:12])
_orig_anc = mt.is_ancestor
mt.is_ancestor = lambda *a, **k: False        # 撤针：外合扫描摘掉（等于修前世界）
try:
    g_pin = mt.run_ring(team=t, apply=True, log=lambda s: print("   s5pin| " + str(s)[:150]))
finally:
    mt.is_ancestor = _orig_anc
ck("S5 撤针必红：外合扫描瞎→blocked 卡永悬 DONE 零转正（本锁的绿全凭那道扫描）",
   g_pin.get("extmerged", 0) == 0
   and card(t, "TC-S5A").get("status") == "DONE" and card(t, "TC-S5B").get("status") == "DONE"
   and all(state_of(t)["cards"].get(x, {}).get("stage") == "blocked" for x in ("TC-S5A", "TC-S5B")),
   "extmerged=%s" % g_pin.get("extmerged"))
_st = state_of(t)                                                   # 推龄 31min+降阈：修前此案必冲错铃
for x in ("TC-S5A", "TC-S5B"):
    _st["cards"][x]["blocked_ts"] = time.time() - 31 * 60
json.dump(_st, open(os.path.join(t, "state", "merge-train.json"), "w", encoding="utf-8"))
mt.STALE_MIN = 1.0
g5 = mt.run_ring(team=t, apply=True, log=lambda s: print("   s5r2| " + str(s)[:150]))
st5 = state_of(t)
ck("S5 外合自动转正：rec=merged(外线) 且卡面 VERIFIED（外线 tip 沙箱判据复跑走 cmd_verdict 正式通道）",
   g5.get("extmerged") == 2
   and all(st5["cards"].get(x, {}).get("stage") == "merged"
           and st5["cards"].get(x, {}).get("merge_sha") == "(外线)" for x in ("TC-S5A", "TC-S5B"))
   and card(t, "TC-S5A").get("status") == "VERIFIED" and card(t, "TC-S5B").get("status") == "VERIFIED",
   "extmerged=%s A=%s B=%s" % (g5.get("extmerged"), st5["cards"].get("TC-S5A", {}).get("stage"),
                              st5["cards"].get("TC-S5B", {}).get("stage")))
ck("S5 零force零改哈希：转正后主干 tip 分毫未动（火车只读主干+写辅账）",
   gitc(repo, "rev-parse", "main")[1] == tip_ext, "")
ck("S5 已外合不冲撞车错铃：龄31min>阈1min，BLOCKED-STALE/EXT-FAILED 均未进聚合账",
   not any("TC-S5" in k for k in alerts_keys(t)),
   "keys=%s" % [k[:70] for k in alerts_keys(t)])
ck("S5 events 痕：stage=merged(外线合入)→verdict PASS 转正 每卡一笔",
   sum(1 for l in ev_lines(t) if "(外线合入) → verdict PASS 转正" in l and "|mergetrain|" in l) == 2, "")
ck("S5 外合沙箱用完即删：worktree 回到 2（lane 树+主干树）无泄漏",
   len([w for w in gitc(repo, "worktree", "list", "--porcelain")[1].splitlines()
        if w.startswith("worktree ")]) == 2, "")

# ---------------- S6 改动面锁真实宿主契约（MECH-TRAIN-ADR-1 ③；卡链原 S5，合流轮重编 S6/S7/S8） ----------
# 病根实锤（6 笔火车实录同签名 fails=5）：旧 scope_lock_step 裸 `bash` 在 win 任务 PATH=
# C:\Windows\System32\bash.exe（WSL 桥），看板等活依赖已迁 win → 套件在毒宿主恒红；且套件按
# /mnt/c 缺省读**主仓工作树**而非沙箱合并内容。死桩 all.sh 对这两件事全瞎——本场景让桩自报。
_fwd = lambda p: p.replace("\\", "/")
t5 = os.path.join(SB, "s6"); os.makedirs(t5, exist_ok=True)
r5, _ = make_repo("s6-repo")
open(os.path.join(r5, "cc-team", "tests", "all.sh"), "w", newline="\n").write(
    '#!/usr/bin/env bash\n'
    'B="$(cd "$(dirname "$0")/../../.." && pwd)"\n'
    '{ echo "HOST=$(uname -s)"; echo "CODE=$CC_CODE_HOME"; echo "ADR=$CC_ADR_HOME";'
    ' echo "SUITE=$CCSUITE_DIR"; echo "SEAL=$BOARD_SELFAUDIT_PATH"; } > "$B/s6cap.txt"\n'
    'echo ALL_SUITES_PASS\n')
gitc(r5, "add", "-A"); gitc(r5, "commit", "-qm", "s5 self-reporting stub")
gitc(r5, "worktree", "add", "--detach", os.path.join(t5, "wt"), "HEAD")
rc5, txt5 = mt.scope_lock_step(t5, ["anything.py"])
cap5 = {}
try:
    for ln in open(os.path.join(t5, "s6cap.txt"), encoding="utf-8"):
        if "=" in ln:
            k, v = ln.rstrip("\n").split("=", 1)
            cap5[k] = v
except OSError:
    pass
_hu = cap5.get("HOST", "").upper()
_hok = _hu.startswith(("MINGW", "MSYS", "CYGWIN")) if os.name == "nt" else _hu.startswith("LINUX")
ck("S6a 改动面锁宿主形态=火车进程同形（win 上必 MINGW/MSYS，Linux=WSL 桥毒宿主即红）",
   rc5 == 0 and "改动面锁绿" in txt5 and _hok,
   "rc=%s HOST=%s os=%s txt=%s" % (rc5, cap5.get("HOST"), os.name, txt5[:60]))
ck("S6b 被测代码钉沙箱合并树（CC_CODE_HOME=root/wt，不是主仓工作树——测的必须是待合物）",
   cap5.get("CODE") == _fwd(os.path.join(t5, "wt")), "CODE=%s" % cap5.get("CODE"))
ck("S6c 套件沙箱逐圈隔离（CC_ADR_HOME/CCSUITE_DIR 落本圈 root 下，非全局 /tmp 硬编码）",
   cap5.get("ADR", "").startswith(_fwd(t5)) and cap5.get("SUITE", "").startswith(_fwd(t5))
   and cap5.get("ADR") != "/tmp/cc-adr",
   "ADR=%s SUITE=%s" % (cap5.get("ADR"), cap5.get("SUITE")))

# ---------------- S7 撤针必红：显式钉配毒 → 改动面锁红且套件零起 ----------------
cap5f = os.path.join(t5, "s6cap.txt")
if os.path.exists(cap5f):
    os.remove(cap5f)
_bkv = os.environ.get("CARD_VERIFY_BASH")
os.environ["CARD_VERIFY_BASH"] = (_fwd(os.path.join(os.environ.get("SystemRoot", "C:\\Windows"),
                                       "System32", "no-such-bash-mtar.exe"))
                                  if os.name == "nt" else "/nonexistent/no-bash-mtar")
try:
    rc6, txt6 = mt.scope_lock_step(t5, ["anything.py"])
finally:
    if _bkv is None:
        os.environ.pop("CARD_VERIFY_BASH", None)
    else:
        os.environ["CARD_VERIFY_BASH"] = _bkv
ck("S7 显式钉配毒→改动面锁必红且套件零起（宁红不猜不回落裸 bash；针两头钉死：S6 绿 S7 红）",
   rc6 != 0 and not os.path.exists(cap5f)
   and ("宿主钉死" in txt6 if os.name == "nt" else True),
   "rc=%s txt=%s" % (rc6, txt6[:90]))

# ---------------- S8 真 adversary.sh：火车宿主形+契约环境下全绿，且自带看板密封 ----------------
# 外求 canary（BOARD_SELFAUDIT_PATH=rc3）：套件**无条件**覆写该变量为自带密封桩——若哪天有人
# 删了那行（判据四重新探活体看板=今天 fails=5 的病根），canary 生效→对账断言连坐→本场景必红。
t7 = os.path.join(SB, "s8"); os.makedirs(t7, exist_ok=True)
canary = os.path.join(t7, "canary.py")
open(canary, "w", newline="\n", encoding="utf-8").write(
    "import sys\nprint('CANARY-RAN')\nsys.exit(3)\n")
env7 = mt.scope_env(t7)
env7.update({"CC_CODE_HOME": _fwd(os.path.dirname(CT)),          # 被测=本锁所在树（火车沙箱同位）
             "CC_TEAM_SRC": _fwd(CT),
             "BOARD_SELFAUDIT_PATH": _fwd(canary)})
bash7, berr7 = mt._scope_bash()
rc7, out7 = (mt._run([bash7, "-c", "bash tests/adversary.sh"], cwd=CT, env=env7)
             if berr7 is None else (1, "宿主钉死: %s" % berr7))
ck("S8 真 adversary.sh（非桩）火车契约环境全绿 + 看板 canary 未跑=套件自带密封",
   rc7 == 0 and "ADR_ALL_PASS" in (out7 or "") and "CANARY-RAN" not in (out7 or "")
   and "fails=0" in (out7 or ""),
   "rc=%s tail=%s" % (rc7, _fwd((out7 or "")[-150:])))
ck("S8b 真套件落盘在逐圈隔离目录（CC_ADR_HOME 生效：adr/home+adr/repo 建在本圈 root 下）",
   os.path.isdir(os.path.join(t7, "adr", "home")) and os.path.isdir(os.path.join(t7, "adr", "repo")),
   "home=%s" % os.path.isdir(os.path.join(t7, "adr", "home")))

# ---------------- 汇总 ----------------
n = len(OK_) + len(FAIL)
print("merge-train-check: asserts=%d PASS=%d FAIL=%d" % (n, len(OK_), len(FAIL)))
if FAIL:
    for x in FAIL:
        print("  RED:", x)
    sys.exit(1)
shutil.rmtree(SB, ignore_errors=True)
print("TRAIN-LOCK-GREEN")
