# ccards.cli — CLI 装配 + 写锁包裹 + 写命令收尾自动刷新有状态 DAG。
# ops 侧（recycle 等）用 run(argv) 做**进程内调用**（MECH-STD1 通信定版：
# 进程内走库，跨机走 CLI+文件；不开第三种通道）。
import argparse, sys
from . import __doc__ as PROTOCOL_DOC
from .commands import (cmd_ack, cmd_bind, cmd_cancel, cmd_claim, cmd_create, cmd_done,
                       cmd_duty, cmd_epoch, cmd_list, cmd_next, cmd_note, cmd_panel, cmd_plan,
                       cmd_progress, cmd_purge, cmd_push, cmd_reindex, cmd_rejectlog, cmd_release, cmd_set,
                       cmd_show, cmd_start, cmd_thaw, cmd_upstream, cmd_verdict, cmd_watch,
                       verify_trial_precheck)
from .config import RESIDENT_DUTY, WRITERS, split
from .graph import cmd_dag
from .locks import team_lock_acquire, team_lock_release


def _parser():
    p = argparse.ArgumentParser(prog="cards_dag.py", description=PROTOCOL_DOC)
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("create"); x.add_argument("id"); x.add_argument("--title", required=True)
    x.add_argument("--desc"); x.add_argument("--deps", type=split, default=[])
    x.add_argument("--files", type=split, default=[]); x.add_argument("--lanes", type=split, default=[])
    x.add_argument("--verify"); x.add_argument("--ref-impl", dest="ref_impl")
    x.add_argument("--hold"); x.add_argument("--repo"); x.add_argument("--parent")
    x.add_argument("--status", default="OPEN", choices=["DRAFT", "OPEN", "HOLD"])
    # REQ-CARD-3FORM-1 三段式三必填（GitHub issue form；业主 seq=22 B 项。缺段拒立与否
    # 看生效档：闸只挂 create 新卡，存量/set/claim 等通道不返工。①栏字段=CA3 同源反查 src，合并形）
    x.add_argument("--src", help="三段①原始来源：业主原话/信箱 seq=/前序卡号（CA3 同源反查同一字段）")
    x.add_argument("--story", help="三段②整理后需求：用户故事+验收标准（命中术语表黑话只回显提醒）")
    x.add_argument("--plan", help="三段③执行计划：方案要点")
    # REQ-CARD-TYPEGATE-1（业主 seq=32 定版）：type 字段正式化 + 判据三要素必填。
    # 生效档 env CARD_TYPE_EPOCH > config.toml gates.cardtype_epoch > warn（照 3FORM 阶梯，存量不炸）
    x.add_argument("--type", dest="card_type",
                   help="卡类型（G2）：PM 定版枚举 REQ/BUG/TEST/MECH/OPS，须与卡号前缀一致；"
                        "enforce 档缺位即拒")
    x.add_argument("--c-op", dest="c_op", help="判据要素①（G1）：操作什么")
    x.add_argument("--c-in", dest="c_in", help="判据要素②（G1）：输入什么")
    x.add_argument("--c-expect", dest="c_expect",
                   help="判据要素③（G1）：预期输出什么（REQ=本卡首次定义；BUG=本就存在当前坏掉的行为）")
    # MECH-RESIDENT-1 岗卡四件（--resident 立岗卡；缺件在 cmd_create 拒）
    x.add_argument("--resident", action="store_true",
                   help="常驻岗卡（seq=8）：在岗态走独立三态 ON-DUTY/DEGRADED/OFF，四件必填")
    x.add_argument("--duty", help="岗责：扫什么/多久（岗卡必填）")
    x.add_argument("--anchor", help="实现锚：crontab 行|automation jobId|脚本绝对路径（岗卡必填）")
    x.add_argument("--heartbeat", help="心跳判据：可执行命令，退出码0=活（岗卡必填）")
    x.add_argument("--fail", help="失败语义：DEGRADED 怎么报警，不自愈（岗卡必填）")
    x.set_defaults(fn=cmd_create)

    x = s.add_parser("set"); x.add_argument("id"); x.add_argument("--status")
    x.add_argument("--force", action="store_true", help="仅限 set --status CANCELED 遇活跃 claims")
    x.add_argument("--deps", type=split); x.add_argument("--files", type=split)
    x.add_argument("--lanes", type=split); x.add_argument("--verify")
    x.add_argument("--ref-impl", dest="ref_impl"); x.add_argument("--hold")
    x.add_argument("--title"); x.add_argument("--desc")
    x.add_argument("--repo", help="REQ-MERGE-GATE-1：交付仓补登（存量 DONE 账缺卡面 repo 时的唯一合法通道；裸改 graph.json=对账 ALARM）")
    x.add_argument("--out-json", dest="out_json", help="产物指针（JSON 对象；MECH-LIT1 起唯一通道）")
    # MECH-RESIDENT-1：岗卡四件只许岗卡改（族籍闸在 cmd_set）；在岗态不经 set，走 duty
    x.add_argument("--duty"); x.add_argument("--anchor")
    x.add_argument("--heartbeat"); x.add_argument("--fail")
    x.set_defaults(fn=cmd_set)

    x = s.add_parser("show"); x.add_argument("id"); x.set_defaults(fn=cmd_show)
    x = s.add_parser("list"); x.add_argument("--status"); x.add_argument("--owner")
    x.set_defaults(fn=cmd_list)
    x = s.add_parser("claim"); x.add_argument("id"); x.add_argument("--owner", required=True)
    x.add_argument("--pid", type=int, default=0,
                   help="worker 真实进程 pid——REQ-BEAT-ANCHOR-1 起仅留档，不再参与判活/归属")
    x.add_argument("--pid-ns", dest="pid_ns", choices=["wsl", "win", "auto"], default="auto",
                   help="pid 所属 OS 空间（留档）；判活已改名牌制（任期+事件流）")
    x.add_argument("--epoch", type=int, help="名牌任期（派发型必带=与道台账对班；缺省=自动发新班 +1）")
    x.set_defaults(fn=cmd_claim)
    x = s.add_parser("epoch", help="REQ-BEAT-ANCHOR-1 发牌：该道任期 +1 入账并打印（dispatcher 派发前调）")
    x.add_argument("owner"); x.add_argument("--cid", default="", help="发牌时点的卡号（留档）")
    x.set_defaults(fn=cmd_epoch)
    x = s.add_parser("start"); x.add_argument("id"); x.add_argument("--owner", required=True)
    x.add_argument("--pid", type=int, default=0)
    x.add_argument("--pid-ns", dest="pid_ns", choices=["wsl", "win", "auto"], default="auto")
    x.add_argument("--verify", help="HOLD 卡补上可执行判据再开工(PM 直做卡一步到位)")
    x.set_defaults(fn=cmd_start)
    x = s.add_parser("bind"); x.add_argument("id"); x.add_argument("--session", required=True,
                                                                help="CC 会话 id（dispatcher 起会话后回填；覆写幂等）")
    x.set_defaults(fn=cmd_bind)
    x = s.add_parser("done"); x.add_argument("id"); x.add_argument("--owner", required=True)
    x.add_argument("--commit", required=True); x.add_argument("--evidence")
    x.add_argument("--epoch", type=int, help="名牌任期（claim 输出/brief 所发）——错班拒收；缺省=过渡放行")
    x.add_argument("--out-json", dest="out_json", help="下游产物（JSON 对象；MECH-LIT1 起唯一通道）")
    x.add_argument("--repo"); x.add_argument("--force", action="store_true",
                                            help="owner 不在 lanes 白名单时(PM 兼做/临时泳道)明示")
    x.set_defaults(fn=cmd_done)
    x = s.add_parser("verdict"); x.add_argument("id"); x.add_argument("result", choices=["PASS", "REJECT"])
    x.add_argument("--reason")
    x.add_argument("--as", dest="as_role", choices=["PM", "Verifier"], default="Verifier",
                   help="验收身份(协议债③)：PM 兼写 verdict 文本留「PM兼验待验」痕")
    x.add_argument("--reverify", action="store_true",
                   help="独立Verifier转正通道(MECH-V2)：仅 VERIFIED 卡+--as Verifier+PASS——只覆盖 verdict 痕并记 reverify 事件，状态不变")
    x.set_defaults(fn=cmd_verdict)
    x = s.add_parser("release"); x.add_argument("id"); x.add_argument("--who")
    x.set_defaults(fn=cmd_release)
    x = s.add_parser("duty", help="MECH-RESIDENT-1 岗卡在岗态三态迁移（普通卡不适用；矩阵外互窜拒）")
    x.add_argument("id"); x.add_argument("state", choices=RESIDENT_DUTY)
    x.add_argument("--by", help="操作身份（互盯岗/PM），落 events 痕")
    x.add_argument("--reason", help="迁移原因（DEGRADED 须带判据证据摘要）")
    x.set_defaults(fn=cmd_duty)
    x = s.add_parser("note"); x.add_argument("cid", nargs="?", default="",
                                             help="卡号（可选；纪律样式 note <卡号> --owner …——旧版误吞此位置参数直接 argparse 红）")
    x.add_argument("--owner", required=True); x.add_argument("--text", required=True)
    x.add_argument("--epoch", type=int, help="名牌任期——错班拒收（REQ-BEAT-ANCHOR-1）；缺省照旧放行")
    x.set_defaults(fn=cmd_note)
    x = s.add_parser("rejectlog", help="MECH-REJT-1：分类器/守卫拒绝一笔入账（看板「被拒记录」页签数据源）")
    x.add_argument("--tool", required=True); x.add_argument("--note", required=True)
    x.set_defaults(fn=cmd_rejectlog)
    x = s.add_parser("ack"); x.add_argument("key", nargs="?", help="告警键子串（--list 先看开放项）")
    x.add_argument("--list", dest="list_open", action="store_true", help="列开放中的 ALARM（last>ack）")
    x.add_argument("--who", help="处置人身份，落进 alerts.json 痕")
    x.set_defaults(fn=cmd_ack)
    x = s.add_parser("cancel"); x.add_argument("id"); x.add_argument("--reason"); x.add_argument("--force", action="store_true")
    x.set_defaults(fn=cmd_cancel)
    x = s.add_parser("purge", help="出账（seq=11）：仅 CANCELED 卡移 state/archive/，graph 除名")
    x.add_argument("id"); x.add_argument("--reason"); x.add_argument("--who", default="PM")
    x.add_argument("--force", action="store_true", help="被引用/归档同名 两类闸的显式越权")
    x.set_defaults(fn=cmd_purge)
    x = s.add_parser("thaw"); x.set_defaults(fn=cmd_thaw)
    x = s.add_parser("dag")
    x.add_argument("--json", action="store_true", help="输出物化图全量 JSON（机器消费，不再解析人话行）")
    x.add_argument("--check", action="store_true", help="只对新度：过期=ALARM rc1（reindex/dag 重建）")
    x.add_argument("--refresh", action="store_true", help="强制重算再报（digest 相同=空操作）")
    x.set_defaults(fn=cmd_dag)
    x = s.add_parser("upstream"); x.add_argument("ids", nargs="+"); x.set_defaults(fn=cmd_upstream)

    x = s.add_parser("push"); x.add_argument("id"); x.add_argument("--lanes", type=split, required=True)
    x.add_argument("--front", action="store_true"); x.add_argument("--who"); x.set_defaults(fn=cmd_push)
    x = s.add_parser("next"); x.add_argument("owner"); x.set_defaults(fn=cmd_next)
    x = s.add_parser("progress"); x.add_argument("id"); x.add_argument("text")
    x.add_argument("--who", help="缺省取 CLAW_SESSION_ID，再无='-'")
    x.add_argument("--lane", help="名牌续写①：泳道号（与 --epoch/--ring 三件齐才入账）")
    x.add_argument("--epoch", type=int, help="名牌续写②：任期号（claim 发的牌，对班校验）")
    x.add_argument("--ring", type=int, help="名牌续写③：圈号（同一任期内必须递增）")
    x.set_defaults(fn=cmd_progress)  # REQ-BEAT-ANCHOR-1 起进 WRITERS：续写=台账写事件（圈号单调需锁），
                                     # 不带三件的旁证流旧通道行为不变
    x = s.add_parser("watch"); x.add_argument("--card"); x.add_argument("-n", type=int, default=20,
                                                                        help="进度流行数上限(<=0=全量)")
    x.add_argument("--events", type=int, default=6, help="一次性档的 events 尾行数")
    x.add_argument("--follow", action="store_true", help="追新进进度行(文件+tail=观测定版,无常驻回调)")
    x.add_argument("--interval", type=float, default=2.0); x.add_argument("--ticks", type=int, default=0,
                                                                           help="有限轮数(cron/测试)，0=无限 Ctrl-C 退")
    x.set_defaults(fn=cmd_watch)
    x = s.add_parser("plan"); x.add_argument("--json", action="store_true",
                                             help="单 tick 决策快照(只读面，MECH-ENG1)")
    x.set_defaults(fn=cmd_plan)
    x = s.add_parser("panel"); x.add_argument("--out", help="输出路径(缺省 state/panel.html)")
    x.add_argument("--open", dest="open_it", action="store_true", help="生成后浏览器打开")
    x.set_defaults(fn=cmd_panel)  # MECH-VIS1：只读派生视图，不进 WRITERS（写命令收尾本就同笔自动刷）
    x = s.add_parser("reindex"); x.set_defaults(fn=cmd_reindex)
    return p


def run(argv=None):
    """执行一条命令；成功返回 0。die() 以 SystemExit 透传退出码（shell 语义不变）。"""
    a = _parser().parse_args(argv)
    # REQ-VERIFY-DRYRUN-1 发卡即考试：create/set/start 带判据=当场锁外试跑（写锁包住整个 cmd_*
    # 函数体，长执行绝不进锁——同 verdict「长执行锁外」纪律）。三口唯一调用点=本行，实现唯一=
    # commands.verify_trial_precheck（T5 差分对就摘这一行，摘后 T1/T3/T4 必红=守卫验得红）。
    if a.cmd in ("create", "set", "start"):
        verify_trial_precheck(a)
    if a.cmd in WRITERS:
        team_lock_acquire(a.cmd)
        try:
            a.fn(a)
            if a.cmd != "dag":  # dag 命令自带刷新/--check 只读语义
                from . import graph
                graph.refresh()
        finally:
            team_lock_release()
        if a.cmd == "done":  # 补50（业主设计）：done 拉起校验——锁已放，套件锁外跑，绿=瞬锁落账
            from .commands import auto_verify
            auto_verify(a.id)
    else:
        a.fn(a)
    return 0


def main(argv=None):
    # BUG-CCARDS-UTF8-1：入口钉死出侧 utf-8+errors=replace（stdout/stderr 双侧，同
    # scripts/ops/logcheck.py 先例；批五终账「打印侧也要显式 utf-8」纪律落到 CLI 本体）。
    # 病根：账本 graph.json 有历史坏字（U+FFFD，实锤=BUG-V584-REG-3 desc 73 枚），
    # cmd_show/cmd_list 打 ensure_ascii=False 的 JSON，win GBK(cp936) 控制台遇 U+FFFD
    # 直接 UnicodeEncodeError 崩栈——坏字卡反而「看不下去」，排查反被账本咬死。
    # utf-8 本身可编码 U+FFFD（原样透传），errors=replace 兜的是 utf-8 也打不出的残代理对
    # （账本若混入 \udXXX 类断字节）：宁可看到 '?' 占位，不可让命令崩。run() 进程内调用
    # （facade/ops 通道）不动出侧，只钉 CLI 进程入口，库语义不变。
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(run(argv if argv is not None else sys.argv[1:]))
    except SystemExit:
        raise
