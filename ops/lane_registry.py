#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lane_registry.py — 泳道白名单生成 + 垃圾会话目录清理（REQ-BOARD-LANECLEAN-1）。

业主令(2026-11-02 17:3x)：「不是有兜底 down 的么，怎么那么多 Down 还躺在那」。
看板 laneact 曾把 ~/.claude/projects 下 code-wf-* 会话目录**全量**入列，红检/沙箱遗留
（wf-a/b/c/v/t5、m5-tmp-ph-p0~p4）与闲置真道（m4/m8/m9）混排成十几条「躺着的 Down」。

本脚本两件事（只碰白名单与会话目录，不碰在制道数据）：
  build —— 用「名册在册道 ∪ claims 活锚 ∪ lane-*.lock」生成白名单初版，
           落 <team>/state/registry.json 骨架（lanes 数组即够；REQ-REG-HOST-1 落地时
           只加 host/launcher 字段，不动本结构——两卡不打架）。默认 dry 打草稿，--apply 才写。
  clean-sessions —— 清单式删除 ~/.claude/projects 下 code-wf-* 且**无对应白名单在册道**
           的会话目录（含 m5-tmp-* 红检临时树）；默认 dry 只出清单，--apply 才删，逐条打日志。
           安全闸：白名单取 state/registry.json ∪ lane-*.lock ∪ claims owner 三源并集
           （注册表哪怕过期，正在用的道绝不会被删）；三源全空=拒删。

命名禁令（规格③尾）：红检/沙箱今后**不许再产生 code-wf-<道>-tmp-* 会话目录**——
临时树一律放仓外 $TMPDIR，不留 CC 会话目录残骸。常驻静态断言见
cc-team/tests/sandbox_tree_naming_check.py（本卡锁 scripts/e2e/board-laneclean-test.py 第④层）。

用法:
  python -I cc-team/ops/lane_registry.py build [--team-home PATH] [--apply]
  python -I cc-team/ops/lane_registry.py clean-sessions [--registry PATH] [--projects-root DIR] [--apply]
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

TEAM_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ccards import config as _cfg  # REQ-OSS-ENGINE-1：转录前缀/会话根随配置单入口（禁用户目录字面量）
# 会话目录前缀：CC 按工作树路径落项目目录（同 status-web.PROJ 口径）；前缀=encode(车队码根\wf-)
# 现算（config.proj_dir_name 单源），生产值与旧硬前缀逐字相等；DISPATCH_CODE_ROOT 锚在场随锚。
PROJ_PREFIX = _cfg.proj_dir_name(os.environ.get("DISPATCH_CODE_ROOT") or _cfg.CC_CODE, "")
# 白名单初版的在册道与岗位注记（现场定 2026-11-02：m4/m8/m9 登记候补、不删工作树；
# t2~t5 系 DL 违规新开待收口，本卡未收口前不入白名单——wf-t5 会话目录按垃圾清理）。
LANE_NOTES = [
    ("m1", "表管理", ""),
    ("m2", "工作流", ""),
    ("m3", "资产", ""),
    ("m4", "权限审计", "业主现场定(2026-11-02 17:3x)：未入道务登记候补，工作树保留不删"),
    ("m5", "多系统集成", ""),
    ("m6", "平台架构", ""),
    ("m7", "评审A·重页组", ""),
    ("m8", "评审B·长尾页", "业主现场定(2026-11-02 17:3x)：未入道务登记候补，工作树保留不删"),
    ("m9", "评审C·后端对照", "业主现场定(2026-11-02 17:3x)：未入道务登记候补，工作树保留不删"),
    ("m10", "评审D·收口", ""),
    ("ops", "部署运维", ""),
    ("pmmail", "信箱岗", "IM 中转常驻（业主↔PM 经微信主助理）；2026-11-03 名字普查实锤在编，补登"),
]

def _now():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def roster_lanes(team_home):
    """名册在册道：cc-team/registry.json projects[].lanes（主项目 dataworks-ai）。"""
    out = []
    try:
        reg = json.load(open(os.path.join(team_home, "registry.json"), encoding="utf-8"))
        for p in reg.get("projects") or []:
            if p.get("name") == "selftest-demo":
                continue
            # host 册 lanes 项是 dict {lane,host,launcher}（REQ-REG-HOST-1 落地形）——
            # 旧码 str(x) 把整枚 dict 当道名，在册道全被误记 unregistered_seen（2026-11-03
            # build 实锤噪音）。取 lane 字段；str 项兼容保留。
            for x in (p.get("lanes") or []):
                ln = str(x.get("lane") or "").strip() if isinstance(x, dict) else str(x).strip()
                if ln:
                    out.append(ln)
    except Exception:
        pass
    return out


def lock_lanes(team_home):
    """活锚一：lane-*.lock 目录（派发/道锁在用的道，绝不可当成垃圾）。"""
    return sorted(os.path.basename(x)[len("lane-"):-len(".lock")]
                  for x in glob.glob(os.path.join(team_home, "state", "lane-*.lock")))


def claim_lanes(team_home):
    """活锚二：claims/<卡>/claim.json 的 owner（认领在飞的道）。"""
    out = set()
    for f in glob.glob(os.path.join(team_home, "claims", "*", "claim.json")):
        try:
            o = json.load(open(f, encoding="utf-8")).get("owner")
            if o:
                out.add(str(o))
        except Exception:
            pass
    return sorted(out)


def registry_lanes(reg):
    """读白名单文件（str 项或 dict 项都吃）→ set。"""
    out = set()
    for e in (reg or {}).get("lanes") or []:
        if isinstance(e, str) and e.strip():
            out.add(e.strip())
        elif isinstance(e, dict) and str(e.get("lane") or "").strip():
            out.add(str(e["lane"]).strip())
    return out


def effective_whitelist(team_home, registry_path=None):
    """白名单装载（安全并集口径）→ reg,roster,locks,claims,union 五元组。"""
    rp = registry_path or os.path.join(team_home, "state", "registry.json")
    reg = set()
    try:
        reg = registry_lanes(json.load(open(rp, encoding="utf-8")))
    except Exception:
        pass
    roster = set(roster_lanes(team_home))
    locks = set(lock_lanes(team_home))
    claims = set(claim_lanes(team_home))
    return reg, roster, locks, claims, reg | roster | locks | claims


def delete_whitelist(team_home, registry_path=None):
    """删除判据白名单（卡面规格③=「无对应 registry 在册道」才删）：
    - state/registry.json 在册时 → **registry ∪ 道锁 ∪ claims**（名册不再当免死金牌——
      DL 违规新开待收口的 t2~t5 系名册残留，业主本卡已定按垃圾清）；
    - registry 未落 → 退回 名册 ∪ 道锁 ∪ claims 临时口径（宁多保不少删）。
    道锁/claims=在飞活锚，任何情况下都免删（不碰在制道）。"""
    reg, roster, locks, claims, union = effective_whitelist(team_home, registry_path)
    return (reg | locks | claims) if reg else union, union


def cmd_build(a):
    team = a.team_home
    reg, roster, locks, claims, union = effective_whitelist(team)
    lanes = []
    for name, role, note in LANE_NOTES:
        lanes.append({"lane": name, "role": role, "state_hint": "候补", "note": note})
    # 三源里出现的白名单外道：不静默吞——列入 extra 供 PM 定夺（不自动转正）。
    known = set(x["lane"] for x in lanes)
    extra = sorted(union - known)
    doc = {
        "generated_at": _now(),
        "generator": "cc-team/ops/lane_registry.py build (REQ-BOARD-LANECLEAN-1)",
        "note": "泳道白名单骨架：看板泳道区只列 lanes 在册道；REQ-REG-HOST-1 落地时只加 "
                "host/launcher 字段不动本结构。在册道三态（在制/候补/缺编）由看板现算，"
                "本文件只登在册身份与岗位注记。",
        "lanes": lanes,
    }
    if extra:
        doc["unregistered_seen"] = extra
        doc["unregistered_note"] = "名册/道锁/claims 出现但未入白名单——PM 拍板转正或清理，看板按幽灵计数处理"
    print("build %s → lanes=%d extra_unregistered=%s" % (
        "APPLY" if a.apply else "DRY", len(lanes), extra or "无"))
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    outp = os.path.join(team, "state", "registry.json")
    if not a.apply:
        print("DRY：未写盘（--apply 才落 %s）" % outp)
        return 0
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    tmp = outp + ".tmp-lane-registry"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, outp)
    print("APPLY：已落 %s" % outp)
    return 0


def cmd_clean_sessions(a):
    team = a.team_home
    reg, roster, locks, claims, _union = effective_whitelist(team, a.registry)
    keep, union = delete_whitelist(team, a.registry)
    root = a.projects_root or _cfg.CC_PROJ   # 会话转录根缺省=config 派生（双侧形态随仓，生产值同旧串）
    print("白名单: registry=%d 名册=%d 道锁=%s claims=%s → 删除判据免删集=%s" % (
        len(reg), len(roster), locks, claims, ",".join(sorted(keep))))
    if not union:
        print("REJECT：白名单三源全空——无法判断谁是垃圾，拒删（先跑 build）")
        return 2
    if not os.path.isdir(root):
        print("REJECT：会话目录根不存在 %s" % root)
        return 2
    removed, kept_active, ghosts = 0, [], []
    for d in sorted(glob.glob(os.path.join(root, PROJ_PREFIX + "*"))):
        if not os.path.isdir(d):
            continue
        lane = os.path.basename(d).split(PROJ_PREFIX)[-1]
        if lane in keep:
            kept_active.append(lane)
            continue
        ghosts.append(lane)
        if not a.apply:
            print("DRY would-remove: %s (lane=%s 未在册)" % (d, lane))
            continue
        try:
            shutil.rmtree(d)
            removed += 1
            print("APPLY removed: %s (lane=%s 未在册)" % (d, lane))
        except OSError as e:
            print("FAIL remove: %s → %s" % (d, e))
            return 1
    print("summary: 在册保留=%s / 幽灵=%d %s / removed=%d" % (
        ",".join(kept_active) or "-", len(ghosts), ",".join(ghosts) or "-", removed))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="生成白名单初版落 state/registry.json 骨架")
    b.add_argument("--team-home", default=os.path.abspath(TEAM_DEFAULT))
    b.add_argument("--apply", action="store_true", help="写盘（缺省 dry 只打草稿）")
    b.set_defaults(fn=cmd_build)
    c = sub.add_parser("clean-sessions", help="清单式删除未在册 code-wf-* 会话目录")
    c.add_argument("--team-home", default=os.path.abspath(TEAM_DEFAULT))
    c.add_argument("--registry", default=None, help="白名单文件（缺省 <team>/state/registry.json）")
    c.add_argument("--projects-root", default=None, help="会话目录根（缺省 ~/.claude/projects 的 WSL 视图）")
    c.add_argument("--apply", action="store_true", help="真删（缺省 dry 只出清单）")
    c.set_defaults(fn=cmd_clean_sessions)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
