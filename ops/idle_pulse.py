#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""idle_pulse.py — 产线停摆哨（业主 2026-11-03 03:2x 定版：「5 分钟没有消耗，就要系统卡，
实际定时任务去查看，并汇报给你」）。由 cc-ctrld idlepulse 环每 300s 拉起（零 OS 定时器，
全 Windows）；影子=只报不立卡，正式 --apply 才立卡。

判据（单条，宁滥勿缺；只喊不动作）：
  系统「活性」= 三源里最新一次落笔时刻——①台账流水 events.log ②各会话转录 *.jsonl
  ③在岗心跳 lane-*.beat / claims 目录 mtime。三源 max 龄 > 300s = 零消耗 = 停摆。
  归因只写卡面 note 供 PM 判（不自动处置）：ready>0 而无人干活=派发/启动断；
  ready==0 且 claimed==0=供料枯竭（该发新卡了）。

动作（幂等）：立/续 SYS-FLOWSTOP-<YYYYMMDD> 卡（每日一张，重复停摆走 note+count 不双卡），
alerts 记 ALARM SYS-FLOWSTOP（同键聚合）；**恢复即自绿**——卡判据=「此刻活性<300s」
（本脚本 --check-live），消耗恢复后 done-sweep/verdict 通道自动转正，不留僵尸卡。
自测：--selftest 四场景（沙箱）：停摆立卡 / 新鲜不动 / 恢复判据自绿 / 沙箱自证（账根
不回流正账）→ IDLEPULSE-GREEN。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # cc-team
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

STALL_S = int(os.environ.get("IDLEPULSE_STALL_S", "300"))


def activity_age(projects_root, team_home):
    """三源最新落笔距今秒数；全空/读不到=inf（更该立卡，但如实带 cause 说明）。"""
    cands = []
    ev = os.path.join(team_home, "state", "events.log")
    try:
        cands.append(os.path.getmtime(ev))
    except OSError:
        pass
    try:
        for p in glob.glob(os.path.join(projects_root, "*", "*.jsonl")):
            try:
                cands.append(os.path.getmtime(p))
            except OSError:
                continue
    except OSError:
        pass
    try:
        for p in glob.glob(os.path.join(team_home, "state", "lane-*.beat")) + \
                 glob.glob(os.path.join(team_home, "claims", "*")):
            try:
                cands.append(os.path.getmtime(p))
            except OSError:
                continue
    except OSError:
        pass
    if not cands:
        return float("inf")
    return time.time() - max(cands)


def ledger_counts():
    """(ready, claimed)——engine 只读快照，供归因；炸了=(-1,-1) 只喊不拦。"""
    try:
        from ccards import engine
        t = engine.tick()
        return len(t.get("ready") or []), len(t.get("claimed") or {})
    except Exception:
        return -1, -1


def card_id():
    return "SYS-FLOWSTOP-" + time.strftime("%Y%m%d")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="正式=立卡；缺省影子只报")
    ap.add_argument("--check-live", action="store_true",
                    help="判据模式：活性<阈=0(绿)；停摆中=1(红)。卡面 verify 用它")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--projects-root", default=None)
    ap.add_argument("--team-home", default=None)
    a = ap.parse_args(argv)

    # BUG-BIND-DEADPATH-1 收口三班（2026-11-04）：selftest 分派必须在 ccards 首见 import 之前。
    # 旧序=先 import ccards.config 再进 _selftest：config.py:10 导入期把 CC_TEAM_HOME 缓存进
    # sys.modules（彼时仍是真账根），_selftest 改 env 对其无效 → 沙箱是纸面的，「停摆」场景把
    # 900s 伪活性龄直写正账 notes/alerts（实锤：06:41~06:45 本道验收复跑泄污 5 笔+键
    # SYS-FLOWSTOP-202611040641；05:39 主控所断「带外手动 idle_pulse 误报/测量伪值」同型根因）。
    # 分派前置后 ccards 首见发生在沙箱 env 之下，真账零回流；④沙箱自证锁见 _selftest。
    # REQ-VERIFY-DRYRUN-1 适配·同洞双班独立取证（2026-11-04 slot-94；本班 pmmail 同案异记并入）：
    # 旧顺序下三场景直接落现网账（当日 SYS-FLOWSTOP 在册时=只写 alerts 报警；卡不在的日子=把
    # 测试卡连试跑痕写进真 events，R3「零写账」破洞）。分派先行+沙箱 env 后首次 import=全隔离，
    # 两班同断同修。
    if a.selftest:
        return _selftest()

    # BUG-BIND-DEADPATH-1 收敛（2026-11-04 夜班二班）：转录根缺省统一走 ccards.config 单入口
    # （env 锚 DISPATCH_PROJ > config.toml host.sessions_dir > CC_PROJ 随仓位置运行时派生=本机
    # Windows 原生视图）。旧 expanduser("~") 直落随起进程 HOME 漂移（WSL 时代 HOME=/home/...
    # 整类 BIND-WAIT 案底），不再作为生产缺省。
    from ccards import config as _cfg
    projects = a.projects_root or os.environ.get("DISPATCH_PROJ") or _cfg.CC_PROJ
    team = a.team_home or os.environ.get("CC_TEAM_HOME") or ROOT

    age = activity_age(projects, team)
    if a.check_live:
        print("IDLEPULSE-LIVE age=%.0fs 阈=%d" % (age, STALL_S))
        return 0 if age < STALL_S else 1

    ready, claimed = ledger_counts()
    ts = time.strftime("%F %T")
    if age < STALL_S:
        print("%s idlepulse: 活性 %.0fs<%d 正常 ready=%s claimed=%s"
              % (ts, age, STALL_S, ready, claimed))
        return 0
    why = ("供料枯竭（ready=0 且 claimed=0，该发新卡）" if ready == 0 and claimed == 0
           else "派发/启动断（账上有料却无活性）" if ready > 0
           else "在岗断流（有在制无活性）" if claimed > 0
           else "活性三源皆空（账本/转录/心跳都读不到）")
    cid = card_id()
    title = "产线停摆：≥%d 分钟零消耗（系统哨自动立案）" % (STALL_S // 60)
    desc = ("idle_pulse 哨发现台账流水/会话转录/在岗心跳三源 %ds 无任何落笔。"
            "归因初判：%s。处置归 PM：查 ctrld 环与启动通道，恢复消耗后本卡判据自绿。"
            % (STALL_S, why))
    # REQ-VERIFY-DRYRUN-1 适配：三口发卡现挂「发卡即考试」试跑闸——旧形把 python 的 stderr
    # 全吞（>/dev/null 2>&1），缺失时报文不可见、rc=1 尾痕空，恰是规格明拒的形态，试跑必被
    # 拒。改裸形直取 --check-live 自身 rc（本体 108-111 行即 return 0/1，语义等价），并 --files
    # 声明被测脚本路径：临时目录里命「can't open file」恰走 R2 red-first 豁免（本卡判据本质
    # 就是「在场即绿」，发卡时文件未落地属合法 red-first）。
    verify = "python3 -I cc-team/ops/idle_pulse.py --check-live"
    print("%s idlepulse: 停摆 %.0fs>%d 因=%s 卡=%s %s"
          % (ts, age, STALL_S, why, cid, "APPLY" if a.apply else "(dry)"))
    if not a.apply:
        return 0
    from ccards import cli
    from ccards import alerts
    try:
        from ccards import graph
        all_cards = graph.all_cards()
    except OSError:
        all_cards = {}
    if cid not in all_cards:
        # lanes=ops 系已退役 RESIDENT ops 常驻道时代残留——名册现仅 m1-m10+wf-slot-*，
        # 钉不存在道=卡永不可派=ready 积压恒在+IDLE-WARN 连拍（2026-11-04 实锤 12 拍，
        # 同型坑另见 REQ-HEALTH-WEEKLY-1 人工钉错）。ANY 进正常管道后，"恢复自绿→
        # claim→done-sweep→verdict 转正不留僵尸"设计闭环才真正可达（本文件 14-15 行口径）。
        rc = cli.run(["create", cid, "--title", title, "--desc", desc,
                      "--verify", verify, "--lanes", "ANY",
                      "--files", "cc-team/ops/idle_pulse.py"])
        if rc != 0:
            print("WARN idlepulse 立卡失败 rc=%s（写闸拒/同名在途，下拍重试）" % rc)
        else:
            rc2 = cli.run(["note", cid, "--owner", "idlepulse", "--text",
                           "首报 %s：%s（活性龄 %.0fs）" % (ts, why, age)])
            if rc2 != 0:
                print("WARN idlepulse note 失败 rc=%s" % rc2)
    else:
        c = all_cards.get(cid) or {}
        if c.get("status") in ("OPEN", "CLAIMED", "DONE"):
            cli.run(["note", cid, "--owner", "idlepulse", "--text",
                     "续报 %s：%s（活性龄 %.0fs）" % (ts, why, age)])
    try:
        alerts.record("ALARM SYS-FLOWSTOP %s: 产线零消耗超阈（%s）" % (
            time.strftime("%Y%m%d%H%M") , why))
    except OSError:
        pass
    return 0


def _selftest():
    """四场景沙箱：①三源全旧→立卡+报警；②转录新鲜→不动；③--check-live 恢复即自绿；
    ④沙箱自证=ccards 账根落沙箱（正账零回流）。全部走临时 CC_TEAM_HOME，不碰正账——
    前提是先于 ccards 首见 import 分派+清缓存（旧形导入期缓存真账根=纸面沙箱，06:4x 实锤）。"""
    tmp = tempfile.mkdtemp(prefix="idlepulse-st-")
    save = os.environ.get("CC_TEAM_HOME")
    team = os.path.join(tmp, "cc-team")
    proj = os.path.join(tmp, "projects", "C--Users-x-code-wf-st1")
    os.makedirs(os.path.join(team, "state"))
    os.makedirs(os.path.join(team, "claims"))
    os.makedirs(proj)
    ev = os.path.join(team, "state", "events.log")
    open(ev, "w", encoding="utf-8").close()
    tr = os.path.join(proj, "s.jsonl")
    open(tr, "w", encoding="utf-8").write("{}\n")
    old = time.time() - 900
    os.utime(ev, (old, old)); os.utime(tr, (old, old))
    os.environ["CC_TEAM_HOME"] = team
    os.environ["IDLEPULSE_STALL_S"] = "300"
    # 纵深防御：宿主若已先行 import 过 ccards（导入期缓存=真账根，env 后改无效——06:4x
    # 实锤泄漏形），清除包与子模块令其在沙箱 env 下重派生；配 ④ 沙箱自证成红绿对。
    for _m in [k for k in list(sys.modules)
               if k == "ccards" or k.startswith("ccards.")]:
        del sys.modules[_m]
    oks = []
    # ① 停摆→立卡（global STALL_S 在 import 时定值，这里用环境变量+重读：确保 main 现读）
    global STALL_S
    STALL_S = 300
    rc = main(["--apply", "--projects-root", os.path.join(tmp, "projects"),
               "--team-home", team])
    from ccards import graph
    cards = graph.all_cards()
    cid = card_id()
    ok1 = rc == 0 and cid in cards and (cards[cid].get("status") or "") == "OPEN"
    oks.append(("①三源全旧=停摆必立 SYS-FLOWSTOP 卡（OPEN 走写闸）", ok1, str(cid in cards)))
    # ② 转录刚写过→活性新鲜→不动（卡数不增）
    os.utime(tr, None)
    n_before = len(cards)
    rc2 = main(["--apply", "--projects-root", os.path.join(tmp, "projects"),
                "--team-home", team])
    ok2 = rc2 == 0 and len(graph.all_cards()) == n_before
    oks.append(("②刚有消耗=不立卡（新鲜即豁免，防滥）", ok2, ""))
    # ③ 判据自绿：活性在场→--check-live rc=0；再拨回 900s 陈旧→rc=1（红绿同源）
    rc3 = main(["--check-live", "--projects-root", os.path.join(tmp, "projects"),
                "--team-home", team])
    os.utime(tr, (old, old)); os.utime(ev, (old, old))
    rc4 = main(["--check-live", "--projects-root", os.path.join(tmp, "projects"),
                "--team-home", team])
    ok3 = rc3 == 0 and rc4 == 1
    oks.append(("③卡判据=恢复消耗即绿、仍停摆即红（红绿同源一套码）", ok3, "rc3=%s rc4=%s" % (rc3, rc4)))
    # ④沙箱自证：ccards 重导入后的账根必须落本沙箱——见真账路径=回流泄漏复发形，此判必红
    #   （撤「分派前置+缓存清除」任一防护且宿主先 import ccards 时，①会随真账卡态假绿假红
    #    漂，唯此条恒红兜底；06:4x 真账实泄 5 笔换回的锁）。
    from ccards import config as _sc
    ok4 = os.path.abspath(_sc.TEAM) == os.path.abspath(team)
    oks.append(("④沙箱自证=ccards 账根落沙箱（正账零回流）", ok4, _sc.TEAM))
    if save is None:
        os.environ.pop("CC_TEAM_HOME", None)
    else:
        os.environ["CC_TEAM_HOME"] = save
    bad = [t for t, ok, _ in oks if not ok]
    for t, ok, hint in oks:
        print("%s %s%s" % ("PASS" if ok else "FAIL", t, ("｜" + hint) if (not ok and hint) else ""))
    if bad:
        print("IDLEPULSE-RED fails=%d" % len(bad))
        return 1
    print("IDLEPULSE-GREEN n=%d" % len(oks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
