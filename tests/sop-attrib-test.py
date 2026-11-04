#!/usr/bin/env bash
# sop-attrib-test.py — REQ-SOP-ATTRIB-1「归因标签机读化+簇阈值>=2接线+销账归因注记闸」红绿锁。
# 规格（业主 seq=35②③④ 拍板；seq=34 S3 待装；docs/系统保障清单.md §九）：
#   R1 mergetrain 同文件簇阈值：草案 >3（第 4 解才报）作废 → total>=2（第 2 次解即报）；
#      阈值走 env MERGE_TRAIN_CLUSTER_MIN 带默认 2、下限 2；键文案随动 cluster=<簇键> total=N>=T
#      可辨识形；同卡级 conflict_count>2 业主未改口径=零触碰（T2 不回归）；
#      向后兼容：同簇 4 解在新阈下仍报（claim-counter T8 的旧精确文案断言由本锁 T1/T2 承接）。
#   R2 recycle FAIL 处置痕机读归因 attrib=施工|考题|环境 三选一（只贴标签不改刀）：
#      环境=跑不了/桥 rc=127/尾痕空/对侧不可达；考题=判据形态病（单侧 OS 判据实证/疑似跨解释器喂错）；
#      施工=真痕红=产品缺陷（mark_reject 族）。落点=events 行尾 + DONE-PENDING/REJECT 点名行
#      + 卡面（REJECT 走 verdict 文本）+ 本轮 DONE-ATTRIB 归因节。去重(pending_sha)/驳回权零触碰。
#   R3 alerts.ack() 销账注记闸：空/裸名(PM)/与键文案复读 → 拒+die 点名口径；机件通道
#      （autoack 自带 reason、auto:/auto@/fuse-|fuse_ 前缀）放行；acked_by 存注记原文；
#      cmd_ack 调用方零改（经 cli 走一遍=出口在 alerts 内的实证）。
# 判据：
#   T1 同簇第 1 解无键、第 2 解出 cluster 开放键（total=2>=2 精确形）；env 覆写（=3 时第 2 解不报
#      第 3 解报）；坏值/下界回 2；T2 同卡级 3 解才出 card 键（>2 口径不回归）+同簇 4 解仍报
#      （向后兼容承接）；T3 考题/环境/施工三例各贴对标签：events 行尾、点名行、卡面 verdict、
#      归因节四处同现，且 pending_sha 去重语义不变（复扫 events 仍一笔）；三值外标签必炸；
#   T4 裸名/空/复读 ack 拒且不落 ack 痕、带归因注记过且 acked_by=原文、FUSE/auto 前缀放行、
#      autoack 不受闸、cli ack 不带 --who 端到端被拒（调用方零改实证）；
#   T5 撤闸差分对（业主纪律：守卫要验过它红得起来）：摘阈值比较式=T1 红、摘 _attrib_tag=T3 红、
#      摘注记闸调用=T4 红（子进程跑 gut 拷贝件，产码文件只读）。
# 跑法: python -I cc-team/tests/sop-attrib-test.py   （exit 0 + 尾行 SOP_ATTRIB_PASS = 全绿）
# 纪律：全沙箱（独立 CC_TEAM_HOME 临时账家 + temp git 仓 + 假 run_argv 分流桩，零真起子进程判据），
#   零碰生产 :8790、零碰真实台账；撤闸样全在拷贝件上注入，产码文件只读；双语头同 claim-counter-test
#   （''''true 藏 bash 引导段；段内禁再现字面 '''）。
''''true
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY=""
_p312="${CC_PY312_EXE:-}"
[ -z "$_p312" ] && for u in /mnt/[A-Za-z]*/Users/*; do
  [ -x "$u/AppData/Local/Programs/Python/Python312/python.exe" ] && _p312="$u/AppData/Local/Programs/Python/Python312/python.exe" && break
done
for c in python3 python py "${_p312}"; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "FAIL: sop-attrib-test 找不到 python 解释器"; exit 1; fi
exec "$PY" -I -X utf8 "$0"
'''
# -*- coding: utf-8 -*-
import contextlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:                                    # BUG-CCARDS-UTF8-1 同纪律：CJK 尾痕打印不炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ARGV = sys.argv[1:]
# 子进程模式（T5）：argv = <被测树根> --expect-red <t1|t3|t4>；常规模式无 argv。
CT = os.path.abspath(_ARGV[0]) if _ARGV else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECT = _ARGV[2].lower() if len(_ARGV) >= 3 and _ARGV[1] == "--expect-red" else None

os.environ.pop("CC_AUTO_VERIFY", None)          # done 首验通道关闭——本锁验归因不验自动验收
os.environ["CARD_3FORM_EPOCH"] = "off"          # 三段式闸关档——沙箱立卡不带 src/story/plan
os.environ["MERGE_TRAIN_STEP_TIMEOUT"] = "240"

OK_, FAIL = [], []


def ck(name, cond, detail=""):
    (OK_ if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + ("  [%s]" % detail if detail else ""))


def gitc(d, *a):
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", d] + list(a),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    if r.returncode != 0:
        raise RuntimeError("git %s rc=%s %s" % (" ".join(a), r.returncode, out[-200:]))
    return r.returncode, out


def cut(src, marker, repl, want=1):
    """撤闸差分注入（claim-counter 同式）：marker 先验在场（缺席=锁腐硬败），
    命中行整行换成替换行——摘闸=等价删行为。"""
    lines = src.splitlines(keepends=True)
    idxs = [i for i, ln in enumerate(lines) if marker in ln]
    if len(idxs) != want:
        raise AssertionError("撤点 marker 命中 %d 处≠%d: %r" % (len(idxs), want, marker))
    for i in idxs:
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        lines[i] = indent + repl
    return "".join(lines)


def ev_lines(home):
    try:
        return open(os.path.join(home, "state", "events.log"), encoding="utf-8").read().splitlines()
    except OSError:
        return []


def card_of(home, cid):
    g = json.load(open(os.path.join(home, "state", "graph.json"), encoding="utf-8"))
    return (g.get("cards") or g.get("nodes") or {}).get(cid) or {}


def alerts_keys(home):
    try:
        return list(json.load(open(os.path.join(home, "state", "alerts.json"),
                                   encoding="utf-8")).get("alerts", {}))
    except (OSError, ValueError):
        return []


def cluster_keys(home):
    return [k for k in alerts_keys(home) if k.startswith("CONFLICT-THRESHOLD cluster=")]


def fresh(home, mod="mergetrain", **env):
    """场景隔离（claim-counter fresh 同式）：独立 CC_TEAM_HOME + 清 ccards/recycle/mergetrain
    模块缓存重 import——路径常量全在 import 期取值，一进程跑全部场景不串账。
    ⚠ 返回后旧 ccards.* 引用全部作废：各场景 fresh 之后必须重新 from ccards import …。"""
    for m in list(sys.modules):
        if m == "ccards" or m.startswith("ccards.") or m in ("recycle", "mergetrain"):
            del sys.modules[m]
    os.environ.pop("MERGE_TRAIN_CLUSTER_MIN", None)
    for k, v in env.items():
        os.environ[k] = str(v)
    os.environ["CC_TEAM_HOME"] = home
    ops = os.path.join(CT, "ops")
    for p in (ops, CT):
        if p not in sys.path:
            sys.path.insert(0, p)
    return importlib.import_module(mod)


def make_repo(root, tag):
    """fixture 仓（claim-counter 同式精简）：main 带 seed 提交 + lane 分支。"""
    d = os.path.join(root, tag)
    os.makedirs(d, exist_ok=True)
    gitc(d, "init", "-q", "-b", "main")
    gitc(d, "config", "user.email", "sop-test@local")
    gitc(d, "config", "user.name", "sop-test")
    gitc(d, "config", "core.autocrlf", "false")
    open(os.path.join(d, "README.md"), "w", encoding="utf-8", newline="\n").write("line\n")
    gitc(d, "add", "-A")
    gitc(d, "commit", "-qm", "seed main")
    gitc(d, "checkout", "-qb", "lane")
    return d


def lane_head(d):
    return gitc(d, "rev-parse", "HEAD")[1]


def mk_done_card(home, cid, verify_after_done, repo):
    """走真闸造 DONE 卡（create->claim->真提交->done 锚对同=claim-counter proven 通道），
    再把判据字段现改成 CASE 标记（沙箱账本自改合法；run_argv 已被桩接管，改文不真跑）。"""
    from ccards import cli as _cli
    _cli.run(["create", cid, "--title", "归因沙箱卡 %s" % cid, "--lanes", "w1",
              "--verify", "bash -c true", "--repo", repo])
    _cli.run(["claim", cid, "--owner", "w1"])
    sha = lane_head(repo)
    _cli.run(["done", cid, "--owner", "w1", "--commit", sha, "--repo", repo])
    gp = os.path.join(home, "state", "graph.json")
    doc = json.load(open(gp, encoding="utf-8"))
    doc["cards"][cid]["verify"] = verify_after_done
    json.dump(doc, open(gp, "w", encoding="utf-8"), ensure_ascii=False)


def _closure(mt, home, cid, log_acc):
    rec = {"stage": "passed", "integrations": "blocked", "blocked_at": "x",
           "conflict_seen": True, "blocked_ts": time.time(), "conflict_files": "README.md"}
    return mt._conflict_closure(rec, cid, home, lambda s: log_acc.append(str(s)))


def _mkcards(prefix, n):
    # fresh 清模块缓存→cli 用完即取：跨 fresh 持旧引用会绑旧账家（一进程多场景定版教训）
    from ccards import cli as _cli
    for i in range(n):
        _cli.run(["create", "%s-%d" % (prefix, i), "--title", "簇阈沙箱卡", "--lanes", "ANY",
                  "--verify", "bash -c true"])
    return ["%s-%d" % (prefix, i) for i in range(n)]


# ============ T1 簇阈值接线：第 1 解无键、第 2 解出键（>=2 形），env 覆写、下界钉 ============
def s_t1():
    SBX = os.path.join(SB, "t1")
    os.makedirs(SBX, exist_ok=True)
    t = os.path.join(SBX, "home")
    mt = fresh(t)
    ck("T1a 默认阈 C_CLUSTER=2（seq=35② 拍板值接线，草案 >3 作废）", mt.C_CLUSTER == 2,
       "实值=%s" % mt.C_CLUSTER)
    cids = _mkcards("SA-T1", 2)
    _lg = []
    _closure(mt, t, cids[0], _lg)
    ck("T1b 同簇第 1 解=零 cluster 键（第 1 解不报仍成立——报的时点变了、提前不脱靶）",
       not cluster_keys(t), str(cluster_keys(t)))
    _closure(mt, t, cids[1], _lg)
    _k = cluster_keys(t)
    ck("T1c 同簇第 2 解即出 CONFLICT-THRESHOLD cluster 开放键（total=2>=2 精确形）",
       "CONFLICT-THRESHOLD cluster=README.md total=2>=2" in _k, str(_k))
    ck("T1d 键文案随动可辨识（total=N>=T 形态正则）",
       any(re.search(r"total=\d+>=\d+", k) for k in _k), str(_k))

    # env 覆写：MERGE_TRAIN_CLUSTER_MIN=3 → 第 2 解不报、第 3 解报 total=3>=3
    t3h = os.path.join(SBX, "home3")
    mt3 = fresh(t3h, MERGE_TRAIN_CLUSTER_MIN=3)
    ck("T1e env 覆写生效 C_CLUSTER=3（阈值走 env 带默认=卡面边界条款）",
       mt3.C_CLUSTER == 3, "实值=%s" % mt3.C_CLUSTER)
    c3 = _mkcards("SA-T3", 3)
    _closure(mt3, t3h, c3[0], _lg)
    _closure(mt3, t3h, c3[1], _lg)
    ck("T1f 阈=3 时同簇第 2 解不报（证阈值真走 env，非常量写死 2）", not cluster_keys(t3h),
       str(cluster_keys(t3h)))
    _closure(mt3, t3h, c3[2], _lg)
    ck("T1g 阈=3 时第 3 解报且键随动 total=3>=3",
       "CONFLICT-THRESHOLD cluster=README.md total=3>=3" in cluster_keys(t3h),
       str(cluster_keys(t3h)))

    # 下界与坏值：floor=2（口径地板），坏值回默认 2
    mtf = fresh(os.path.join(SBX, "homef"), MERGE_TRAIN_CLUSTER_MIN=1)
    ck("T1h 阈下界=2（env=1 不得退回「第 1 解就报」）", mtf.C_CLUSTER == 2, "实值=%s" % mtf.C_CLUSTER)
    mtb = fresh(os.path.join(SBX, "homeb"), MERGE_TRAIN_CLUSTER_MIN="not-a-number")
    ck("T1i 坏 env 回退默认 2 不炸写口", mtb.C_CLUSTER == 2, "实值=%s" % mtb.C_CLUSTER)
    fresh(os.path.join(SB, "homez"))             # 回默认档，env 残值由 fresh 统一清


# ============ T2 同卡级 >2 不回归 + 同簇 4 解向后兼容（claim-counter T8 场景承接） ============
def s_t2():
    t = os.path.join(SB, "t2")
    mt = fresh(t)
    cid = _mkcards("SA-T2", 1)[0]
    _lg = []
    _ns = [_closure(mt, t, cid, _lg) for _ in range(2)]
    ck("T2a 同卡 2 解计数 1/2（bump 通道照常）", _ns == [1, 2], str(_ns))
    _k = alerts_keys(t)
    ck("T2b 同卡第 2 解不出 card 键（conflict_count>2 口径业主未改=零触碰）",
       not any("card=" + cid in k for k in _k), str(_k))
    _closure(mt, t, cid, _lg)
    _k = alerts_keys(t)
    ck("T2c 同卡第 3 解出 card 键 conflict_count=3>2（旧形原样在册）",
       "CONFLICT-THRESHOLD card=%s conflict_count=3>2" % cid in _k, str(_k))
    _closure(mt, t, cid, _lg)                    # 第 4 解（claim-counter T8 造 4 解场景）
    _k = cluster_keys(t)
    ck("T2d 向后兼容承接：同簇 4 解在新阈下仍报（total=4>=2 键在册；claim-counter 旧精确文案断言作废，由本锁接管）",
       "CONFLICT-THRESHOLD cluster=README.md total=4>=2" in _k, str(_k))
    ck("T2e 簇计数=events.log 实探 4 笔（append-only 唯一账不变）",
       mt._cluster_total(t, "README.md") == 4, "实探=%s" % mt._cluster_total(t, "README.md"))


# ============ T3 归因三例：考题/环境/施工各贴对，落 events 行尾+点名行+卡面+归因节 ============
BEHAV = {
    "CASE-KAO":  {"l": (1, "AssertionError: 1 != 2 真红"), "x": (0, "ok")},                # 对侧绿本机红=单侧OS判据
    "CASE-HUAN": {"l": (1, ""), "x": (1, "AssertionError: boom")},                         # 本机尾痕空=环境未排除
    "CASE-SHI":  {"l": (1, "AssertionError: bugA"), "x": (1, "AssertionError: bugB")},     # 双侧真痕红
}


def s_t3():
    t = os.path.join(SB, "t3")
    rc = fresh(t, mod="recycle")
    from ccards import cli as _cli, commands as _CMD
    repo = make_repo(SB, "t3-repo")
    for cid, v in (("SOP-K", "CASE-KAO"), ("SOP-H", "CASE-HUAN"), ("SOP-S", "CASE-SHI")):
        mk_done_card(t, cid, v, repo)
    # 分流桩：不真起子进程（三例 rc/tail 由表驱动），宿主解析钉死
    _CMD._resolve_verify_bash = lambda *a, **k: "LOCAL"
    rc.run_argv = lambda argv, to: BEHAV.get(argv[-1], {"l": (0, ""), "x": (0, "")})[
        "x" if argv[0] == "CROSS" else "l"]
    rc.cross_argv = lambda verify: ["CROSS", "-c", verify]

    _b = io.StringIO()
    with contextlib.redirect_stdout(_b):
        n_s, n_v, n_r, n_dp = rc.done_sweep_scan(True, 0.0, 3, 1)
    out1 = _b.getvalue()

    ck("T3a 三分流计数=swept3/pending2/reject1（贴标签不改刀：分流判定与返回值语义零变）",
       (n_s, n_v, n_r, n_dp) == (3, 0, 1, 2), "实=(%s,%s,%s,%s)" % (n_s, n_v, n_r, n_dp))
    ck("T3b 点名行=DONE-PENDING 带 attrib=考题（对侧绿本机红=单侧OS判据）",
       any(ln.startswith("DONE-PENDING SOP-K:") and "attrib=考题" in ln
           for ln in out1.splitlines()), out1[:200])
    ck("T3c 点名行=DONE-PENDING 带 attrib=环境（尾痕空/环境性失败未排除）",
       any(ln.startswith("DONE-PENDING SOP-H:") and "attrib=环境" in ln
           for ln in out1.splitlines()), out1[:200])
    ck("T3d 点名行=DONE-REJECT 带 attrib=施工（双侧真痕红=产品缺陷）",
       any(ln.startswith("DONE-REJECT SOP-S:") and "attrib=施工" in ln
           for ln in out1.splitlines()), out1[:200])

    def _tail(cid, marker, attrib):
        hits = [ln for ln in ev_lines(t) if ("|%s|DONE->" % cid) in ln and marker in ln]
        return len(hits) >= 1 and all(ln.rstrip().endswith(" attrib=" + attrib) for ln in hits)

    ck("T3e events 行尾=attrib=考题（KAO 待该侧验收行，行尾即机读字段）",
       _tail("SOP-K", "待该侧验收:", "考题"),
       " ".join(ln for ln in ev_lines(t) if "SOP-K" in ln)[:200])
    ck("T3f events 行尾=attrib=环境（HUAN 待该侧验收行）",
       _tail("SOP-H", "待该侧验收:", "环境"),
       " ".join(ln for ln in ev_lines(t) if "SOP-H" in ln)[:200])
    ck("T3g events 行尾=attrib=施工（SHI 补跑FAIL归因行）",
       _tail("SOP-S", "补跑FAIL归因:", "施工"),
       " ".join(ln for ln in ev_lines(t) if "SOP-S" in ln)[:200])
    ck("T3h 卡面归因节：REJECT 后卡 verdict 文本带 attrib=施工（卡面可见位）",
       "attrib=施工" in (card_of(t, "SOP-S").get("verdict") or ""),
       str(card_of(t, "SOP-S").get("verdict"))[:200])
    ck("T3i 卡态=OPEN（施工打回驳回权照常）；考题/环境两卡留 DONE（不驳先喊照常）",
       card_of(t, "SOP-S").get("status") == "OPEN"
       and card_of(t, "SOP-K").get("status") == "DONE"
       and card_of(t, "SOP-H").get("status") == "DONE")
    ck("T3j 本轮归因节汇总行：DONE-ATTRIB 施工=1 考题=1 环境=1（三值全枚举覆盖）",
       "DONE-ATTRIB 归因节: 施工=1 考题=1 环境=1" in out1, out1[-300:])

    # 去重语义零触碰：复扫同 sha → events 仍一笔（点名行/每轮计数照打）
    _b2 = io.StringIO()
    with contextlib.redirect_stdout(_b2):
        rc.done_sweep_scan(True, 0.0, 3, 2)
    _pk = [ln for ln in ev_lines(t) if "|SOP-K|DONE->DONE|recycle|" in ln]
    ck("T3k pending_sha 去重不变：二轮复扫 KAO events 仍一笔（只贴标签不动刀）",
       len(_pk) == 1, "笔数=%d" % len(_pk))
    ck("T3l 二轮归因节=本轮处置痕数非累计账（施工卡已驳不再扫=施工0，pending 两卡照喊=考题1环境1）",
       "DONE-ATTRIB 归因节: 施工=0 考题=1 环境=1" in _b2.getvalue(), _b2.getvalue()[-200:])

    # 三值枚举锁：越界标签必炸（机读字段口径=恰三值）
    _boom = False
    try:
        rc._attrib_tag("玄学")
    except AssertionError:
        _boom = True
    ck("T3m attrib 只许 施工|考题|环境 三选一（越界值拒=枚举可机读前提）", _boom)
    ck("T3n 标签落点单源：_attrib_tag 形如「 attrib=X」带前导空格（行尾字段解析不歧义）",
       rc._attrib_tag("环境") == " attrib=环境", repr(rc._attrib_tag("环境")))


# ============ T4 销账归因注记闸：裸/空/复读拒，注记过，机件放行 ============
def s_t4():
    t = os.path.join(SB, "t4")
    fresh(t)
    from ccards import alerts as al, cli as _cli
    KEY = "SOP-ACK cluster=README.md total=2>=2 归因闸样例键"
    al.record(KEY)
    al.record("SOP-ACK2 机件前缀放行样例键")
    al.record("SOP-ACK4 autoack 放行样例键")
    al.record("SOP-ACK5 cli 端到端样例键")

    def _dies(substr, who):
        try:
            al.ack(substr, who)
            return False
        except SystemExit as e:
            msg = str(e.args[0] if e.args else e)
            return "REQ-SOP-ATTRIB-1 R3" in msg and "销账必带逐键归因注记" in msg

    ck("T4a 裸名 PM 销账=拒+die 点名口径", _dies("SOP-ACK", "PM"), str(alerts_keys(t)))
    ck("T4b 空注记=拒", _dies("SOP-ACK", ""))
    ck("T4c 裸名变体（pm / PM。 / 纯空白）=拒（判形不因标点大小写逃逸）",
       _dies("SOP-ACK", "pm") and _dies("SOP-ACK", "PM。") and _dies("SOP-ACK", "   "))
    ck("T4d 与键文案复读=拒（扣掉命中键后残量无正文）", _dies("SOP-ACK", KEY))
    _e = al.entries()[KEY]
    ck("T4e 违规拒销不落 ack 痕（键仍开放=拒得响亮不是静默）",
       not _e.get("ack") and al.still_open(_e), str(_e))
    NOTE = "PM 依 seq=35④ 裁定：环境红候业主 GitHub 拍板非判据病，掐铃留痕 x2"
    r = al.ack("SOP-ACK cluster", NOTE)   # 精确到单键（裸 "SOP-ACK" 子串会跨题歧义返列表）
    ck("T4f 带逐键归因注记=过", bool(r) and al.entries()[KEY].get("ack"), str(r)[:120])
    ck("T4g alerts.json ack 痕保留注记原文（acked_by 先例升格为闸门后仍存全文）",
       al.entries()[KEY].get("acked_by") == NOTE, str(al.entries()[KEY].get("acked_by"))[:160])
    ck("T4h 机件前缀 fuse-/auto: 放行不误伤（fuse 处置痕通道=卡面边界条款）",
       bool(al.ack("SOP-ACK2", "FUSE-S5 处置痕@1502 收口")) is True)
    ck("T4i autoack（机器销账自带 reason 旁路）不经注记闸=停铃照成",
       al.autoack("SOP-ACK4 autoack 放行样例键", reason="超2tick未续报") is True
       and al.entries()["SOP-ACK4 autoack 放行样例键"].get("acked_by") == "auto:recycle")
    # 端到端：cli ack 不带 --who（cmd_ack 默认 who="PM"）被拒——调用方零改、闸在 alerts 内出口
    _die_cli = False
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _cli.run(["ack", "SOP-ACK5"])
    except SystemExit:
        _die_cli = True
    ck("T4j cli ack 不带 --who 端到端被拒（cmd_alerts/cmd_ack 零改实证）", _die_cli)
    _b = io.StringIO()
    with contextlib.redirect_stdout(_b):
        _cli.run(["ack", "SOP-ACK5", "--who", "PM 复核：同题已并案，掐"])
    ck("T4k cli ack --who 带理由端到端过且痕存原文",
       "ACKED" in _b.getvalue()
       and al.entries()["SOP-ACK5 cli 端到端样例键"].get("acked_by") == "PM 复核：同题已并案，掐",
       _b.getvalue()[:120])
    ck("T4l PM-lock 型身份标记不被误杀（alarm-deage-check 既有调用形=向后兼容）",
       al.ack("SOP-ACK", "PM-lock") is not None)


# ============ T5 撤闸差分对：摘阈值式/摘标签/摘注记闸 → 对应案必红（子进程 gut 拷贝件） ============
GUTS = [
    ("t1", "ops", "mergetrain.py", "if total >= C_CLUSTER:", "if False:  # 撤阈值(差分注入)\n"),
    ("t3", "ops", "recycle.py", 'return " attrib=%s" % attrib', 'return ""  # 撤标签(差分注入)\n'),
    ("t4", "ccards", "alerts.py", "gate = ack_note_check(who, hits)", "gate = None  # 撤注记闸(差分注入)\n"),
]


def s_t5():
    for case, sub, fname, marker, repl in GUTS:
        g = os.path.join(SB, "gut-" + case)
        shutil.rmtree(g, ignore_errors=True)
        os.makedirs(g, exist_ok=True)
        for tree in ("ccards", "ops"):
            shutil.copytree(os.path.join(CT, tree), os.path.join(g, tree),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        p = os.path.join(g, sub, fname)
        src = open(p, encoding="utf-8").read()
        if marker not in src:
            ck("T5[%s] 撤点 marker 在场(%s)" % (case, fname), False, "锁腐：产码改动把撤点搬走了")
            continue
        open(p, "w", encoding="utf-8").write(cut(src, marker, repl))
        env = dict(os.environ)
        env.pop("MERGE_TRAIN_CLUSTER_MIN", None)
        env.pop("CC_TEAM_HOME", None)
        r = subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.abspath(__file__),
                            g, "--expect-red", case],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600, env=env)
        tail = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["(无输出)"]
        ck("T5[%s] 摘 %s：%s 案变红（守卫验过它红得起来）" % (case, fname, case.upper()),
           r.returncode == 0, tail[0][:160])


def _report_expect():
    bad = len(FAIL)
    if bad == 0:
        print("GUT-NOT-RED(摘闸后对应案仍全绿=差分对失效)")
        sys.exit(1)
    print("GUT-RED-OK 摘闸致红 %d 案: %s" % (bad, "; ".join(FAIL)[:200]))
    sys.exit(0)


if __name__ == "__main__":
    SB = tempfile.mkdtemp(prefix="cc-sop-attrib-lock-")
    os.environ["MERGE_TRAIN_TMPDIR"] = SB
    # 宿主钉死（claim-counter 同式）：防 System32 WSL 启动器毒径混进真闸通道
    _exp = os.environ.get("CARD_VERIFY_BASH")
    _w = _exp or shutil.which("bash") or "bash"
    if _exp is None and os.name == "nt" and _w.lower().startswith(
            os.environ.get("SystemRoot", "C:\\Windows").lower()):
        pass
    else:
        os.environ["CARD_VERIFY_BASH"] = _w

    if EXPECT in ("t1", "t3", "t4"):
        {"t1": s_t1, "t3": s_t3, "t4": s_t4}[EXPECT]()
        _report_expect()

    s_t1()
    s_t2()
    s_t3()
    s_t4()
    s_t5()
    if FAIL:
        print("SOP_ATTRIB_FAIL cases=%d red=%d: %s" % (len(OK_) + len(FAIL), len(FAIL),
                                                       "; ".join(FAIL)))
        sys.exit(1)
    print("SOP_ATTRIB_PASS cases=%d" % len(OK_))
    sys.exit(0)
