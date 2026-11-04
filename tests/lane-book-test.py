#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lane-book-test — 校验器道名源头闸专测（业主 2026-11-03 五点整改第 1 条）。
病根实锤：旧 create 卡校验卡号/依赖/状态/判据四样、独漏道名——mech1-3/pm/ver1/wops
幽灵道因此进账，卡躺进没人消费的队列（11-03 夜班停摆案背景之一）。新规：立卡/改道/
入队三道口统一 `_check_lanes`——道名必须在岗册（state/registry.json，唯一权威，
**不并 host 册**——t2~t5 名册明文未收口不入白名单）或 ANY=池通配；册缺=跳过
（沙箱通道）。幽灵答复归口同理被 set 口拒（dispatch T10 与之互补，不复刻规则）。

跑法：python -I cc-team/tests/lane-book-test.py → LANE-BOOK-GREEN n=8
红自证：把 commands._check_lanes 的 `book = lane_book()` 变异成 `book = set()`，
②④⑤⑥ 必红（变异过程实录见 TODO/提交说明，不常驻本文件防自匹配假绿）。
REQ-VERIFY-DRYRUN-1 适配：夹具判据 "v"（跑不通的占位串）改 "true"——发卡三口现挂
试跑闸，占位假判据会被试跑拒掉，本锁只测道名闸，判据必须本身能跑（归因不互糊）。
"""
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="lanebook-")
os.environ["CC_TEAM_HOME"] = TMP          # 必须在 import ccards 之前钉沙箱
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs(os.path.join(TMP, "state"))
json.dump({"lanes": [{"lane": "m1", "role": "表管理"}]},
          open(os.path.join(TMP, "state", "registry.json"), "w", encoding="utf-8"))
from ccards import cli                       # noqa: E402
from ccards import config as _C              # noqa: E402


def run(*args):
    try:
        return cli.run(list(args))
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def show_exists(cid):
    # show 对缺卡 die(rc=1)：rc=0 即账上有这张卡
    return run("show", cid) == 0


oks = []


def ck(name, got, want):
    oks.append((name, got == want, "got=%r want=%r" % (got, want)))


ck("①在册道名 create 放行", run("create", "LB-OK", "--title", "t", "--verify", "true",
                                "--lanes", "m1"), 0)
ck("②册外道名 create 必拒（幽灵牌源头闸）", run("create", "LB-BAD", "--title", "t",
                                                "--verify", "true", "--lanes", "ghostz"), 1)
ck("③ANY 池通配放行", run("create", "LB-ANY", "--title", "t", "--verify", "true",
                           "--lanes", "ANY"), 0)
ck("④set 改道册外必拒", run("set", "LB-OK", "--lanes", "ghostz"), 1)
ck("⑤push 册外必拒", run("push", "LB-OK", "--lanes", "ghostz"), 1)
ck("⑥被拒的 LB-BAD 账上零残留", show_exists("LB-BAD"), False)
ck("⑦被拒的道未落队列镜像",
   os.path.exists(os.path.join(TMP, "state", "lane-ghostz.queue")), False)
# 册缺=跳过校验（沙箱通道正语义；正账恒有册，缺册归 reconcile 环看护）
_C.TEAM = tempfile.mkdtemp(prefix="lanebook-nobook-")
ck("⑧无册=不拦（各测试房通道）", run("create", "LB-NB", "--title", "t", "--verify", "true",
                                     "--lanes", "ghostz"), 0)

bad = [t for t, ok, _ in oks if not ok]
for t, ok, hint in oks:
    print("%s %s%s" % ("PASS" if ok else "FAIL", t, ("｜" + hint) if not ok else ""))
if bad:
    print("LANE-BOOK-RED fails=%d" % len(bad))
    sys.exit(1)
print("LANE-BOOK-GREEN n=%d" % len(oks))
