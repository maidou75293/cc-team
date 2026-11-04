# ccards.statemachine — 台账状态标准化定版（MECH-STATE-STD-1，业主信箱 2026-11-02 seq=4）。
# 本模块是状态语义的**唯一数据源**：七态术语表 / 状态=属主+出口矩阵 / 每态必填字段 / 迁移矩阵（全部合法边）。
# 代码闸（ccards.commands 各命令 require_edge）与 selftest 断言（tests/state_std_test.py）读**同一张表**=同源校验；
# README.md 的「状态=属主+出口矩阵」「术语表」「迁移矩阵」小节与本表逐行一致，由 state_std_test 钉死——
# 改一边不改另一边必红。以后新增任何机制，先回答「它看护哪个状态」（OWNER_MATRIX 的 duty 列），防止再造无主状态。
# 边界（业主已定）：**不动** STATUS_ENUM 七态枚举本身；**不加**「待校验」新状态——DONE+done_at 账龄即唯一表达。
import os
from .config import STATUS_ENUM, die

# ── 一、术语表：每态一个中文术语（看板/卡面/文档文案统一，禁自造词；括号内为既有别名，出现即红）
TERMS = {
    "DRAFT": "草稿",
    "OPEN": "待领",
    "CLAIMED": "在制",
    "HOLD": "挂起",
    "DONE": "待验",
    "VERIFIED": "已验收",
    "CANCELED": "已作废",
}

# ── 二、状态=属主+出口（owner=这张卡现在该谁动；exit=出口动作；duty=谁看护它，停摆找谁）
OWNER_MATRIX = {
    "DRAFT": ("PM", "补全 verify 后 set --status OPEN 发布",
              "无自动看护；停摆归 PM——新增机制必须先自答「看护哪个状态」才许上岗"),
    "OPEN": ("dispatcher（发单侧）", "claim/start → CLAIMED",
             "队列/next/ccops tick 报可领；无泳道映射的就绪卡由 dag ALARM 点名"),
    "CLAIMED": ("本卡 owner（泳道 worker）", "done → DONE",
                "ops/recycle.py STALLED 心跳看护（pid 双侧核验+静默阈；session 档只 WARN）"),
    "HOLD": ("挂 hold/deps 的人", "deps 全 DONE 且无人工 hold 时 thaw → OPEN",
             "ccops tick 每圈自动 thaw；缺 verify 的解冻被闸拒并 ALARM"),
    "DONE": ("Verifier（校验环节）", "verdict PASS → VERIFIED / REJECT → OPEN",
             "MECH-DONE-SWEEP-1 滞留扫描（复用勿重复造）；账龄锚=卡面 done_at"),
    "VERIFIED": ("—", "无（例外通道 verdict REJECT 回 OPEN，审计留痕）",
                 "终态，不再照看"),
    "CANCELED": ("—", "无（例外通道 PM set → OPEN 复活，需 verify）",
                 "终态，不再照看"),
}

TERMINAL = tuple(s for s in STATUS_ENUM if OWNER_MATRIX[s][2].startswith("终态"))

# ── 三、每态必填字段（卡面侧；缺=异常，判定见 field_gaps）。
#     session 锚在 claims/<id>/claim.json 侧（身份三键定版：check-exit 用会话键），不在卡面重复；
#     mandate 为预留字段（委托/授权信息将挂卡面），本卡不建闸不进 REQUIRED_FIELDS。
REQUIRED_FIELDS = {
    "DRAFT": [],
    "OPEN": ["verify"],
    "CLAIMED": ["owner", "claimed_at"],
    "HOLD": [],              # HOLD 允许暂缺 verify：thaw 闸单独拒「缺 verify 解冻」并 ALARM，不必双写
    "DONE": ["owner", "commit", "done_at"],
    "VERIFIED": ["verdict"],
    "CANCELED": [],
}
RESERVED_FIELDS = ["mandate"]


def field_gaps(cards):
    """[(卡号, 状态, [缺字段,...])] 按卡号排序——每态必填字段的唯一判定，闸门与测试同读此函数。"""
    out = []
    for cid in sorted(cards):
        c = cards[cid]
        st = c.get("status")
        need = REQUIRED_FIELDS.get(st)
        if need is None:
            continue
        miss = [f for f in need if not c.get(f)]
        if miss:
            out.append((cid, st, miss))
    return out


def gate_mode():
    """缺字段闸档位（业主定：存量补齐后才启用告警，防上线即满盘红误报）：
    warn（缺省，只喊 WARN 不进告警账）/ enforce（ALARM+进 alerts 账，sweep 环补完存量后由 PM 切档）/ off。"""
    m = (os.environ.get("CARD_FIELD_GATE") or "warn").strip().lower()
    return m if m in ("warn", "enforce", "off") else "warn"


# ── 四、迁移矩阵：全部合法边的单一数据表 (from, to, via, 说明)。from "-"=立卡。
#     与 commands.py 现存闸逐条对读生成，**未**新增/放宽任何边（STATUS_ENUM 不动，业主边界）。
#     规则要点（业主已定，行内自含）：set 只在 DRAFT/OPEN/HOLD/CANCELED 四态互移（禁直写
#     CLAIMED/DONE/VERIFIED=绕过锁/验货/验收）；verdict REJECT 回 OPEN；HOLD 走 thaw/start；
#     VERIFIED→VERIFIED 是 --reverify 转正自环（不动状态机链）。
_SETTABLE = ("DRAFT", "OPEN", "HOLD", "CANCELED")

TRANSITIONS = [
    ("-", "DRAFT", "create", "PM 起草（允许无判据；cli choices 同步只放 DRAFT/OPEN/HOLD）"),
    ("-", "OPEN", "create", "立卡即待领（OPEN 必带 verify，闸门已锁）"),
    ("-", "HOLD", "create", "挂起注册卡（PROC-LEDGER-1 收编在途交付用）"),
    ("OPEN", "CLAIMED", "claim", "dispatcher 取活：verify+deps+lanes+双锁全闸；写 claimed_at"),
    ("DRAFT", "CLAIMED", "start", "PM 直做卡（需 --verify 补判据）"),
    ("OPEN", "CLAIMED", "start", "PM 直做卡/临时泳道"),
    ("HOLD", "CLAIMED", "start", "PM 直做卡（需先清 hold 字段）"),
    ("CLAIMED", "DONE", "done", "worker 勾卡：现场验 commit+files 域；写 done_at；清双锁"),
    ("DONE", "VERIFIED", "verdict PASS", "Verifier 机器复跑卡面 verify 绿=落章（CC_AUTO_VERIFY 时 done 锁外拉起同路）"),
    ("DONE", "OPEN", "verdict REJECT", "打回重做：清 owner/commit/output/claimed_at/done_at，回队头"),
    ("VERIFIED", "OPEN", "verdict REJECT", "验收后打回，同上（红检撤章的唯一下行通道）"),
    ("VERIFIED", "VERIFIED", "verdict --reverify", "转正自环：仅 Verifier 身份+机器复跑绿，只换 verdict 痕"),
    ("CLAIMED", "OPEN", "release", "回收：清 owner/claimed_at，回队头（recycle/lane-steal）"),
    ("HOLD", "OPEN", "thaw", "deps 闸自动解冻（需卡面 verify；解冻后补位入队）"),
    ("DRAFT", "CANCELED", "cancel", "PM 作废"),
    ("OPEN", "CANCELED", "cancel", "PM 作废"),
    ("CLAIMED", "CANCELED", "cancel", "PM 作废（活跃 claims 需 --force 清锁）"),
    ("HOLD", "CANCELED", "cancel", "PM 作废"),
    ("VERIFIED", "CANCELED", "cancel", "PM 作废——系统保障转性专用（业主 seq=10：卡账只收业务，"
     "保障登记移 docs/系统保障清单.md，RESIDENT 岗卡一律作废）；业务 VERIFIED 卡不享此边，命令闸另拒"),
] + [
    (f, t, "set", "PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子）")
    for f in _SETTABLE for t in _SETTABLE
]

LEGAL = {(f, t, v): note for (f, t, v, note) in TRANSITIONS}

# via 通道清单（测试/文档对读用；出现表外通道名=闸 programming error）
CHANNELS = ("create", "set", "claim", "start", "done", "verdict PASS", "verdict REJECT",
            "verdict --reverify", "release", "cancel", "thaw")


def legal_edge(frm, to, via):
    """纯判定：这条边在不在矩阵表里（不 die，供测试/预检用）。"""
    return (frm, to, via) in LEGAL


def require_edge(frm, to, via):
    """命令侧写状态前的同源闸：不在迁移矩阵=拒（防任意态互窜）。
    各命令先跑自己的业务闸（verify/deps/lanes/锁），本闸兜「边合法性」最后一道——
    要加新边：先改本表 + README 迁移矩阵（两处在同一 PR），并让 state_std_test 的同源断言过。"""
    if (frm, to, via) not in LEGAL:
        die("迁移矩阵(%s→%s via %s)不在表内=非法转移——新增通道须先改 ccards/statemachine.py "
            "TRANSITIONS 与 README.md 迁移矩阵（同源断言会钉两边一致），并过验收；禁止只改命令绕闸"
            % (frm, to, via))
