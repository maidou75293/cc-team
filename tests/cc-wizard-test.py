#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cc-wizard-test.py — cc 交互向导红绿锁（REQ-CC-WIZARD-1；判据=本文件末行 CC_WIZARD_PASS）。

锁什么（全沙箱、非交互：脚本喂输入 QueueAsk，绝不打真键盘、绝不碰正账）：
 A) 入口分发：裸 `cc`（无子命令）=进向导；带子命令=原一键面逐字不变（init/selftest/export 不连坐）。
 B) 方案书 schema：agent 形态与**无 agent 降级形态**同 schema——降级路径单独断言，
    且断言降级时 agent 执行口一次都没被调（不许把 agent 当测试前提）。
 C) 审阅环可改可删后 schema 仍完整。
 D) 确认=批量立卡：立卡数对账 graph.json；卡面三段式/判据三要素/type/verify=方案「如何测试」
    栏**原文**（闸不豁免：空判据整批拒；red-first 未申报产物=create 真拒，向导如实报失败）。
 E) 全链 run_wizard（首次=沙箱演示→y→真实流程绑沙箱仓→播报有限拍）：六步环节全在场+
    差分对=摘任一环节的证据行后环节审计必红；沙箱演示不污染用户账（用户账只此一轮的卡）。
 F) 播报：偏移状态机行不重不漏；判据红的卡只有[交卷]没有[验收绿]（不放行在账上可证）；
    打回有[打回红]；工位行、立卡行、开工行、进度行各就各位。
 G) EOF 保护：输入流断=未确认不立卡、账本零改动。

跑法：python -I cc-team/tests/cc-wizard-test.py → 末行 CC_WIZARD_PASS（all.sh 第六套）。
"""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # cc-team
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ccards import oss_cli, wizard  # noqa: E402

FAILS = []


def ok(name):
    print("PASS: %s" % name)


def bad(name, detail=""):
    FAILS.append(name)
    print("FAIL: %s%s" % (name, (" —— " + detail) if detail else ""))


def check(cond, name, detail=""):
    ok(name) if cond else bad(name, detail)


def has(lines, pat, name):
    """transcript 里存在含 pat 的行。"""
    hit = any(pat in ln for ln in lines)
    check(hit, name, "未找到含 %r 的行" % pat)
    return hit


class Cap:
    def __init__(self):
        self.lines = []

    def __call__(self, s=""):
        self.lines.append(str(s))


def fresh_team():
    t = tempfile.mkdtemp(prefix="cc-wiz-test-")
    led = wizard.Ledger(t)
    led.ensure()
    return t, led


NO_AGENT = lambda: (None, None)  # noqa: E731  降级形态=未装外接 agent（探测口替身）

# ══ A) 入口分发 ══════════════════════════════════════════════════════════════
calls = []
_orig_rw = wizard.run_wizard
wizard.run_wizard = lambda **kw: (calls.append(kw), 42)[1]
try:
    rc = oss_cli.main([])
finally:
    wizard.run_wizard = _orig_rw
check(rc == 42 and len(calls) == 1, "A1 裸 cc（无子命令）=进交互向导")

calls2 = []
wizard.run_wizard = lambda **kw: (calls2.append(kw), 99)[1]
try:
    rc2 = oss_cli.main(["selftest", "--engine-home", os.path.join(tempfile.gettempdir(), "no-such-engine")])
finally:
    wizard.run_wizard = _orig_rw
check(rc2 == 2 and not calls2, "A2 带子命令=原一键面（selftest 通道不连坐、不进向导）")

# ══ B) 方案书 schema：agent 形态 / 无 agent 降级（单独断言）/ 坏输出降级 ═══════
GOOD_JSON = json.dumps({"modules": [
    {"name": "模块甲", "functions": ["功能一", "功能二"], "test": "grep -q OK a.py",
     "usage": "页面甲使用", "files": ["a.py"]},
    {"name": "模块乙", "functions": "功能丙；功能丁", "test": "true", "usage": "页面乙使用"},
]}, ensure_ascii=False)

capB = Cap()
planA = wizard.build_plan("我要一个智能体", "fake-claude", wizard.QueueAsk([]), capB,
                          agent_run=lambda exe, prompt, **kw: GOOD_JSON)
check(planA["source"] == "agent", "B1 agent 形态：schema source=agent")
check(wizard.validate_plan(planA) is True, "B1 agent 形态 schema 合格（四栏全键）")
check(planA["modules"][1]["functions"] == ["功能丙", "功能丁"],
      "B1 agent 形态 functions 字符串形归一为列表")

spy_calls = []


def _spy_agent_run(*a, **kw):
    spy_calls.append(1)
    raise AssertionError("降级路径不应触碰 agent 执行口")


tmpl_answers = ["2",
                "接待模块", "对外收需求", "", "grep -q R-JD jie.py", "jie.py", "在入口页用",
                "台账模块", "记录进展", "", "true", "", "在看板用",
                "确认"]
capB2 = Cap()
planT = wizard.build_plan("我要一个智能体", None, wizard.QueueAsk(tmpl_answers), capB2,
                          agent_run=_spy_agent_run)
check(planT["source"] == "template", "B2 无 agent 降级：走内置结构化模板（单独断言）")
check(wizard.validate_plan(planT) is True, "B2 降级 schema 合格")
check(not spy_calls, "B2 降级全程 agent 执行口零调用（agent 不是测试前提）")
check(set(planT["modules"][0]) & set(wizard.MODULE_KEYS) == set(wizard.MODULE_KEYS)
      and set(planA["modules"][0]) & set(wizard.MODULE_KEYS) == set(wizard.MODULE_KEYS),
      "B3 两形态同 schema（四栏必达键一致）")
check(planT["modules"][0]["files"] == ["jie.py"] and "files" not in planT["modules"][1],
      "B4 交付产物=可选申报件（填了有、没填不落键）")

capB3 = Cap()
planB = wizard.build_plan("需求X", "fake-claude", wizard.QueueAsk(tmpl_answers), capB3,
                          agent_run=lambda exe, prompt, **kw: "这不是 JSON")
check(planB["source"] == "template", "B5.1 agent 坏输出=如实喊降级改走模板")
check(wizard.validate_plan(planB) is True, "B5.2 降级后 schema 仍合格")
has(capB3.lines, "[降级]", "B5.3 降级有明示行（不悄悄丢内容）")

# ══ C) 审阅环：可改可删 ═══════════════════════════════════════════════════════
planC = {"requirement": "r", "source": "template", "modules": [
    {"name": "甲", "functions": ["f"], "test": "旧判据", "usage": "u"},
    {"name": "乙", "functions": ["g"], "test": "t2", "usage": "u2"}]}
finalC = wizard.review_plan(planC, wizard.QueueAsk(["改1 测试 新判据", "删2", "确认"]), Cap())
check(finalC["modules"][0]["test"] == "新判据" and len(finalC["modules"]) == 1,
      "C1 审阅环改/删后=用户最终口径（确认前不落卡）")
check(wizard.validate_plan(finalC) is True, "C2 改删后 schema 仍完整")

# ══ D) 确认=批量立卡（闸不豁免，对账 graph.json）═══════════════════════════════
teamD, ledD = fresh_team()
planD = {"requirement": "我要建设一个企业内部智能体", "source": "template", "modules": [
    {"name": "数据接入", "functions": ["接库", "接消息"],
     "test": "grep -q D-OK d1_ingest.py", "usage": "任务页选源", "files": ["d1_ingest.py"]},
    {"name": "对话编排", "functions": ["编排"], "test": "true", "usage": "聊天窗"}]}
capD = Cap()
idsD = wizard.create_cards(planD, ledD, "", capD)
check(idsD == ["REQ-WIZ-01", "REQ-WIZ-02"], "D1 立卡数=模块数（2 张入账）")
cardsD = ledD.cards()
check(len(cardsD) == 2, "D2 账本对账：graph.json 恰 2 张卡")
c1 = cardsD.get("REQ-WIZ-01", {})
check(c1.get("verify") == "grep -q D-OK d1_ingest.py", "D3 卡面 verify=方案『如何测试』栏原文（不改写）")
check(all((c1.get(k) or "").strip() for k in ("src", "story", "plan")),
      "D4 三段式三栏全落账（走现有闸，不豁免）")
check(c1.get("type") == "REQ" and all((c1.get("criteria") or {}).get(k) for k in ("op", "input", "expect")),
      "D5 type=REQ + 判据三要素齐（G1/G2 闸口径）")
check(c1.get("files") == ["d1_ingest.py"], "D6 red-first 判据=产物申报进卡面 files（R2 在册通道，非绕闸）")
has(capD.lines, "red-first 豁免(R2)", "D7 R2 豁免痕由引擎自己喊出（向导只申报）")

planD_bad = {"requirement": "r", "source": "template",
             "modules": [{"name": "缺判据", "functions": ["f"], "test": "", "usage": "u"}]}
capD2 = Cap()
idsD2 = wizard.create_cards(planD_bad, ledD, "", capD2)
check(idsD2 == [] and has(capD2.lines, "判据闸不豁免", "D8 空判据整批拒确认（回显含闸语）")
      and len(ledD.cards()) == 2, "D9 拒确认不落账（账仍 2 张）")

planD_nofile = {"requirement": "r", "source": "template",
                "modules": [{"name": "未申报产物", "functions": ["f"],
                             "test": "grep -q NOPE never_declared.py", "usage": "u"}]}
capD3 = Cap()
idsD3 = wizard.create_cards(planD_nofile, ledD, "", capD3)
check(idsD3 == [] and has(capD3.lines, "立卡失败", "D10 red-first 未申报产物=create 真拒（引擎闸活着的证据）")
      and len(ledD.cards()) == 2, "D11 被拒卡不落账（不绕闸不自欺）")

# ══ F) 播报：偏移状态机行不重不漏 ════════════════════════════════════════════
stF = {"ev_pos": 0, "pg_pos": 0}
capF = Cap()
l1 = wizard.broadcast_once(teamD, stF, None, capF)
l2 = wizard.broadcast_once(teamD, stF, None, capF)
check(len(l1) == 2 and l2 == [], "F1 偏移轮询：首拍回放 2 条立卡事件、二拍零重复（行不重不漏）")
check(l1[0].startswith("[立卡]") and l1[1].startswith("[立卡]"), "F2 events→中文播报行映射")

# ══ E) 全链 run_wizard：首次=沙箱演示→y→沙箱「真实仓」开工→播报+差分对 ═════════
teamE, ledE = fresh_team()
capE = Cap()
askE = wizard.QueueAsk([
    "y",                                  # 演示后：在「真实仓」开工（此处绑沙箱 teamE）
    "我要建设一个每日报告机器人",           # ②需求
    "1",                                  # 模板：模块数
    "报告生成", "按日汇总出报告", "",      # 名/功能行/空行
    "grep -q E-OK e1_report.py", "e1_report.py", "机器人页订阅",   # 测试/产物/用法
    "确认",                               # ④确认立卡
])
rcE = wizard.run_wizard(team_home=teamE, repo="", ask=askE, out=capE,
                        detect=NO_AGENT, broadcast_ticks=1, broadcast_interval=0.05)
check(rcE == 0, "E1 全链 run_wizard 非交互跑通（rc=0）")
check(os.path.isfile(os.path.join(teamE, "state", "wizard-demo.seen")),
      "E2 首次进入=先跑沙箱演示并落『已演示』痕（二次进入不再重演）")
cardsE = ledE.cards()
check(list(cardsE) == ["REQ-WIZ-01"], "E3 演示沙箱与用户账隔离：用户账只本轮 1 张卡")
check(cardsE.get("REQ-WIZ-01", {}).get("verify") == "grep -q E-OK e1_report.py",
      "E4 用户账卡面=本轮方案口径原文")
trE = capE.lines
for i in range(1, 7):
    has(trE, "[步骤%d/6]" % i, "E5.%d 环节%d 在场" % (i, i))
has(trE, "未检测到外接终端 agent", "E6 探测结果明示（①『并明示』=没装也讲明白）")
has(trE, "[工位]", "E7 工位播报（⑤『创建了哪些工位』）")
has(trE, "[立卡] REQ-WIZ-01", "E8 立卡播报")
has(trE, "沙箱演示开始", "E9 演示沙箱开场")
has(trE, "[验收绿·合入主干]", "E10 演示链：交卷判据绿→验收绿+合入主干")
has(trE, "[判据红]", "E11 演示链：判据红=不放行（卡留待验）")
has(trE, "[打回红]", "E12 演示链：验收后打回红")
has(trE, "[开工] 工位 slot-1 领了", "E13 演示链：哪道领了哪张卡")
has(trE, "[交卷]", "E14 演示链：交卷播报")
m_red = re.search(r"\[演示收\].*?REQ-WIZ-01=(\w+).*?REQ-WIZ-02=(\w+).*?REQ-WIZ-03=(\w+)", "\n".join(trE))
check(bool(m_red) and m_red.groups() == ("VERIFIED", "DONE", "OPEN"),
      "E15 演示三态终局对账：绿=VERIFIED / 红不放行=DONE / 打回=OPEN",
      "收到=%s" % (m_red.groups() if m_red else None,))


def stages_present(lines):
    return all(any(("[步骤%d/6]" % i) in ln for ln in lines) for i in range(1, 7))


check(stages_present(trE), "E16 差分对·正跑：六环节审计全绿")
for drop in range(1, 7):
    trimmed = [ln for ln in trE if ("[步骤%d/6]" % drop) not in ln]
    check(not stages_present(trimmed),
          "E17.%d 差分对·摘环节%d=审计必红（全链证据缺一即不成立）" % (drop, drop))
# 判据红的那张只许有[交卷]不许有[验收绿]（不放行在播报面可证）
red_lines = [ln for ln in trE if "REQ-WIZ-02" in ln]
check(any("[交卷]" in ln for ln in red_lines) and not any("[验收绿" in ln for ln in red_lines),
      "E18 红卡播报面=有交卷无验收绿（不放行）")

# ══ G) EOF 保护 ═══════════════════════════════════════════════════════════════
teamG, ledG = fresh_team()
os.makedirs(os.path.join(teamG, "state"), exist_ok=True)
with open(os.path.join(teamG, "state", "wizard-demo.seen"), "w", encoding="utf-8") as f:
    f.write("pre-seeded\n")   # 跳过演示，直击正式流程
capG = Cap()
rcG = wizard.run_wizard(team_home=teamG, repo="", ask=wizard.QueueAsk([]), out=capG,
                        detect=NO_AGENT)
check(rcG == 1 and has(capG.lines, "输入流结束", "G1 EOF=未确认不立卡（如实收）")
      and not ledG.cards(), "G2 EOF 场景账本零改动")

for p in (teamD, teamE, teamG):
    shutil.rmtree(p, ignore_errors=True)

print("cc-wizard-test: fails=%d" % len(FAILS))
if FAILS:
    for f_ in FAILS:
        print("FAILED-ITEM: %s" % f_)
    sys.exit(1)
print("CC_WIZARD_PASS")
