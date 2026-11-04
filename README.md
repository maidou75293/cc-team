# cc-team —— 你提需求，它出货：多智能体开发生产线

[![ci](https://github.com/maidou75293/cc-team/actions/workflows/ci.yml/badge.svg)](https://github.com/maidou75293/cc-team/actions/workflows/ci.yml)
![license](https://img.shields.io/badge/license-MIT-green)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![deps](https://img.shields.io/badge/runtime%20dependencies-none-yellowgreen)

**纯标准库、零外部依赖。** 把需求登记成一张带验收判据的「卡」，引擎就自动派单，
并行开动多路终端 agent 会话（Claude Code、Codex CLI 或同形态工具）写代码，
交卷后机器自动复跑你的判据，绿了自动合入主干。所有状态都是人类可读的文件——
断网、断电、进程被杀，账本不丢，重启先对账再接着跑。

> 本文件是通用发行版。现网运营记录（历史裁定、验收数字、运维台账）留在源仓库 `docs/`，
> 不随发布包分发。

## 先看它怎么跑

```
   你说需求                    引擎干活（全自动）                        你拿结果
┌───────────┐   ┌───────────────────────────────────────────┐   ┌──────────────┐
│ 登记一张卡 │ ─▶│ 派单 → N 路泳道并行写码 → 交卷 → 机器复跑  │ ─▶│ 主干上新增一  │
│ 带验收判据 │   │ 判据 → 绿=已验收 → 自动合流；红=打回重做  │   │ 个带验收痕的  │
└───────────┘   └───────────────────────────────────────────┘   │ commit        │
                                                                 └──────────────┘
```

**你只需要三步：**

**① 安装 + 初始化（各一条命令）**

```bash
git clone https://github.com/<你的镜像>/cc-team.git && cd cc-team
pipx install .        # 装好得到控制台命令 cc（不装也行：python -I ccards/oss_cli.py 等价）

cc init --dir /path/to/你的代码仓
#   ↑ 生成运行时目录骨架、config.toml、操作系统计划任务/cron 模板，并自检（末行 ALL_PASS）；
#     顺带检测终端 agent：没装只打印官方安装命令，绝不静默下载（--install-agent 才代执行）。
```

**② 开生产线（一条命令）**

```bash
python ops/ctrld.py loop --live --with-board   # 派发/回收/合流三环常驻一个进程
# 看板实时围观各卡状态： http://127.0.0.1:8790   （长期运行用 init 装好的计划任务，免守终端）
```

**③ 把一条需求登记成卡 —— 之后没有第四步**

```bash
python cards_dag.py create ORDERS-RATE-LIMIT \
  --title "给订单 API 加限流" \
  --desc  "每租户每分钟 600 次；超限返回 429 并带 Retry-After；须附并发测试" \
  --verify "pytest -q tests/test_rate_limit.py" \
  --files "src/orders/" --lanes ANY
```

卡一进账本，派单器就接手了：认领 → 起会话（多道可同时飞）→ worker 在各自 worktree
泳道里写码提交 → 会话退出钩子自动跑你写的验收命令 → 绿 = VERIFIED → 合流看护把该道
提交快进并进主干。你在看板上看着这张卡从「待领」走到「已验收」，然后你的仓库
`git log` 里多了一个**带机器验收痕**的 commit——它就是你自己部署流水线的放行信号
（部署动作留在你的管线；引擎不碰你的生产环境）。

## 一条命令，先看全链

装好之后不必读文档，先跑这个——它在一次性临时沙箱里真跑一遍
「立卡 → 双泳道并行 → 合入主干 → 机器复跑判据 → 自毁」，零真实账触碰：

```bash
cc demo        # 或 python -I ccards/oss_cli.py demo
# 末行 DEMO_PASS = 全链通；任一环节断 = DEMO_FAIL: <环节名>，且每环节是常驻红绿锁（退步即红）
```

## 三分钟现场（本机实跑原始输出，只隐去临时路径）

一个空仓库、一张坏实现的 `add()`、测试文件还不存在——从这里开跑，双泳道并行到两张卡
全部 VERIFIED，以下就是当时的运行输出（未摆拍；`…` 为哈希截短）：

```text
== 底仓就绪 b307abf（calc.add 是坏的，测试文件还不存在）
== 立卡（判据红-first：缺失产物在 files 域内=R2 豁免在册）
DEMO-ADD OPEN
DEMO-SUB OPEN
== 双泳道并行开工（各绑一棵 worktree，互不踩）
DEMO-ADD CLAIMED lane-a epoch=1
DEMO-SUB CLAIMED lane-b epoch=1
== 交卷（锚校验=自报哈希须等于泳道实树尖，错一个字节都不收）
18:34:59|DEMO-ADD|CLAIMED->DONE|lane-a|commit=cfcb54a… tip=cfcb54a… same
18:34:59|DEMO-SUB|CLAIMED->DONE|lane-b|commit=09214ea… tip=09214ea… same
== 合入主干（常驻形态由 fuse 看护快进；未并主干前 verdict 会被主干闸直接拒）
main 现在: 543f256 Merge branch 'wf-b' | cfcb54a DEMO-ADD impl+test | 09214ea DEMO-SUB impl+test
== 机器复跑卡面判据（不是人点头）
18:35:00|DEMO-ADD|DONE->VERIFIED|verifier|PASS
18:35:00|DEMO-SUB|DONE->VERIFIED|verifier|PASS
DEMO-ADD VERIFIED lane-a verify_run rc=0
DEMO-SUB VERIFIED lane-b verify_run rc=0
```

常驻运行时，上面每一步由派单器、会话退出钩子、合流看护自动完成——你只做「立卡」那一步。
（想手工复现全链：见文末附录；`cc init` 生成的模板即为起会话/结账两件套。）

## 和现成方案的差异（2026-11 对公开项目实测调研）

| 方案 | 状态存哪 | 怎么并行 | 怎么验收 | 现状/License |
|---|---|---|---|---|
| **cc-team** | **明文文件账本+追加式事件日志**，断电不丢、可入 git 评审 | git worktree 泳道 | **机器复跑卡面判据，绿了才 VERIFIED**；账本级僵尸回收与写入围栏（任期制） | 本项目 / MIT |
| Claude Squad | 本地会话配置，无任务台账 | tmux + worktree | 人审 diff 再 checkout | 活跃 / AGPL-3.0 |
| Claude Code Agent Teams | 官方文档自认：team 配置**会话结束即删**，/resume 不恢复成员，孤儿会话列于人工排障清单 | 共享任务列表认领 | hooks 槽位（闸门要自己写），实验特性默认关闭 | 专有软件 |
| Vibe Kanban | 内部 SQLite（不透明） | 看板任务→worktree | 人审 diff/PR | **公司 2026-04 关停**转社区 / Apache-2.0 |
| container-use | 容器日志+分支，无状态机 | Docker 容器 | 人审分支 | 停滞（release 止于 2025-08）/ Apache-2.0 |
| Conductor / Copilot agent | 应用/平台侧托管 | worktree / 云沙箱 | 人审后合并 | 闭源商业 / 专有 SaaS |
| LangGraph / CrewAI / AutoGen | 检查点或状态 JSON（依赖 SQLite/Postgres；社区公开指出 checkpoint≠可恢复执行） | 图节点/流程，非面向代码仓 | 无代码验收闸门概念；AutoGen 已入维护模式 | 框架类 / MIT 等 |

两条最硬的差异，调研结论原样引用其依据：
- **「验收=机器复跑判据」是全表唯一无对等项**——其余方案的收口全是"人看一眼 diff"。
- **账本必须完全归用户**：Vibe Kanban 公司关停连云端看板一起移除的反例，正是"纯文件、
  无厂商账号、本地自主"这条路线要解决的问题。

## 凭什么信它

- **「已验收」不靠人点头**：判据写在卡面，done 之后由机器统一复跑，账本存 `verify_run` 痕；
- **僵尸有收尸**：心跳断流回收、认领未起班尸检、会话身份绑锚防串卡——worker 中途死掉，
  卡自动回队头重派，不会有一张卡「卡在在制」；
- **闸门先证明红得起来**：每个防护都配「撤掉修复必红」的注入自证（`--fault` 差分对），
  五套机制套件 + 发布锁在 GitHub CI 的 Linux/Windows 真机每次推送实跑；
- **账本可审计**：`state/graph.json`（当前态）+ 追加式 `events.log`（全部转移）+
  `claims/`（认领凭证），每一步「谁、何时、依据什么」都有痕。

## 概念与账本

| 概念 | 是什么 |
|---|---|
| 卡 (card) | 一个有始有终、可验收的工作单元。状态机：OPEN→CLAIMED→DONE→VERIFIED（可 HOLD/BLOCKED/CANCELED）。 |
| 账本 | `state/graph.json`（当前态）+ `state/events.log`（全部转移，追加式）+ `claims/<卡>/claim.json`（认领凭证）。 |
| 泳道 (lane) | 执行者位。每道绑定一个 git worktree（`wf-<道名>`），同一时刻一张卡只属于一个道。 |
| 任期 (epoch) | 同一道第几次上岗。回收重派后旧任期的心跳作废，防止僵尸 worker 续牌。 |
| 圈号 (ring) | 任期内单调递增的序号，配合时间戳判定「还在干活」。 |
| 心跳 (beat) | `cards_dag.py progress`：每任期内周期打卡；超 `thresholds.beat_stale_min` 未打卡=断流，回收器自动 release。 |
| 验收 (verdict) | 卡的 `--verify` 命令串由 done 后统一实跑，绿了才 VERIFIED。 |

```
cc-team/
├── cards_dag.py          # 账本 CLI（唯一写入口，带写锁）
├── ccards/               # 引擎包：config(单配置入口) / cli(账本命令) / 支撑模块
├── ops/                  # 常驻环：派发器、控制进程、回收器、看门、钩子、看板适配等
├── tests/                # 红绿锁套件（全部沙箱自包含）
├── state/ logs/ claims/  # 现账（运行时生成；★不在发布包内，见下）
└── config.toml           # 可选：文件层配置（★不在发布包内，由 cc init 生成）
```

## 状态=属主与迁移矩阵（同源机器读区，#80 锁逐行对账）

> 下面四张标记表（`STATE-OWNER / STATE-TERMS / STATE-MATRIX / STATE-FIELDS`）是**机器契约**：
> `tests/state_std_test.py` 按标记切表，与唯一数据源 `ccards/statemachine.py`
> （TERMS / OWNER_MATRIX / TRANSITIONS / REQUIRED_FIELDS）逐行比对——改代码数据必须同批改表，
> 只改一头锁就红。表内文案勿动；表外说明可自由改写。

### 状态=属主+出口矩阵（回答「这张卡现在该谁处理、出口是什么」）

<!-- STATE-OWNER-BEGIN -->
| 状态 | 术语 | 属主（这张卡现在该谁动） | 出口 | 看护（停摆找谁） |
|---|---|---|---|---|
| DRAFT | 草稿 | PM | 补全 verify 后 set --status OPEN 发布 | 无自动看护；停摆归 PM——新增机制必须先自答「看护哪个状态」才许上岗 |
| OPEN | 待领 | dispatcher（发单侧） | claim/start → CLAIMED | 队列/next/ccops tick 报可领；无泳道映射的就绪卡由 dag ALARM 点名 |
| CLAIMED | 在制 | 本卡 owner（泳道 worker） | done → DONE | ops/recycle.py STALLED 心跳看护（pid 双侧核验+静默阈；session 档只 WARN） |
| HOLD | 挂起 | 挂 hold/deps 的人 | deps 全 DONE 且无人工 hold 时 thaw → OPEN | ccops tick 每圈自动 thaw；缺 verify 的解冻被闸拒并 ALARM |
| DONE | 待验 | Verifier（校验环节） | verdict PASS → VERIFIED / REJECT → OPEN | MECH-DONE-SWEEP-1 滞留扫描（复用勿重复造）；账龄锚=卡面 done_at |
| VERIFIED | 已验收 | — | 无（例外通道 verdict REJECT 回 OPEN，审计留痕） | 终态，不再照看 |
| CANCELED | 已作废 | — | 无（例外通道 PM set → OPEN 复活，需 verify） | 终态，不再照看 |
<!-- STATE-OWNER-END -->

### 术语表（每态一个中文术语；看板/卡面/文档文案统一，禁自造词）

<!-- STATE-TERMS-BEGIN -->
| 状态 | 中文术语（定版） | 别名（禁自造，见即改） |
|---|---|---|
| DRAFT | 草稿 | 草案 |
| OPEN | 待领 | 待派 |
| CLAIMED | 在制 | 在做／开工 |
| HOLD | 挂起 | 冻结 |
| DONE | 待验 | 交卡／已收工 |
| VERIFIED | 已验收 | 验完 |
| CANCELED | 已作废 | 取消 |
<!-- STATE-TERMS-END -->

### 迁移矩阵（单一数据表=全部合法边；代码闸 `require_edge` 与 selftest 断言同源）

<!-- STATE-MATRIX-BEGIN -->
| 从 | 到 | 通道 | 说明 |
|---|---|---|---|
| - | DRAFT | create | PM 起草（允许无判据；cli choices 同步只放 DRAFT/OPEN/HOLD） |
| - | OPEN | create | 立卡即待领（OPEN 必带 verify，闸门已锁） |
| - | HOLD | create | 挂起注册卡（PROC-LEDGER-1 收编在途交付用） |
| OPEN | CLAIMED | claim | dispatcher 取活：verify+deps+lanes+双锁全闸；写 claimed_at |
| DRAFT | CLAIMED | start | PM 直做卡（需 --verify 补判据） |
| OPEN | CLAIMED | start | PM 直做卡/临时泳道 |
| HOLD | CLAIMED | start | PM 直做卡（需先清 hold 字段） |
| CLAIMED | DONE | done | worker 勾卡：现场验 commit+files 域；写 done_at；清双锁 |
| DONE | VERIFIED | verdict PASS | Verifier 机器复跑卡面 verify 绿=落章（CC_AUTO_VERIFY 时 done 锁外拉起同路） |
| DONE | OPEN | verdict REJECT | 打回重做：清 owner/commit/output/claimed_at/done_at，回队头 |
| VERIFIED | OPEN | verdict REJECT | 验收后打回，同上（红检撤章的唯一下行通道） |
| VERIFIED | VERIFIED | verdict --reverify | 转正自环：仅 Verifier 身份+机器复跑绿，只换 verdict 痕 |
| CLAIMED | OPEN | release | 回收：清 owner/claimed_at，回队头（recycle/lane-steal） |
| HOLD | OPEN | thaw | deps 闸自动解冻（需卡面 verify；解冻后补位入队） |
| DRAFT | CANCELED | cancel | PM 作废 |
| OPEN | CANCELED | cancel | PM 作废 |
| CLAIMED | CANCELED | cancel | PM 作废（活跃 claims 需 --force 清锁） |
| HOLD | CANCELED | cancel | PM 作废 |
| VERIFIED | CANCELED | cancel | PM 作废——系统保障转性专用（业主 seq=10：卡账只收业务，保障登记移 docs/系统保障清单.md，RESIDENT 岗卡一律作废）；业务 VERIFIED 卡不享此边，命令闸另拒 |
| DRAFT | DRAFT | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| DRAFT | OPEN | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| DRAFT | HOLD | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| DRAFT | CANCELED | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| OPEN | DRAFT | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| OPEN | OPEN | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| OPEN | HOLD | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| OPEN | CANCELED | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| HOLD | DRAFT | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| HOLD | OPEN | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| HOLD | HOLD | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| HOLD | CANCELED | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| CANCELED | DRAFT | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| CANCELED | OPEN | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| CANCELED | HOLD | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
| CANCELED | CANCELED | set | PM 改字段通道顺带移态（→OPEN 需卡面已有 verify；CANCELED→OPEN=复活口子） |
<!-- STATE-MATRIX-END -->

### 每态必填字段（缺=异常；`cards_dag.py dag` 巡检按同一张表报缺）

<!-- STATE-FIELDS-BEGIN -->
| 状态 | 卡面必填 | 说明 |
|---|---|---|
| DRAFT | — | 允许无判据；发布成 OPEN 前必须补 verify |
| OPEN | verify | OPEN 无判据=调度黑洞（create/set 闸已锁死） |
| CLAIMED | owner, claimed_at | claimed_at=ISO8601 带冒号，`datetime.fromisoformat` 直接可解 |
| HOLD | — | 允许暂缺 verify：thaw 闸单独拒「缺 verify 解冻」并 ALARM |
| DONE | owner, commit, done_at | done_at 由 done 命令收口必写；存量欠账补账归 DONE-SWEEP |
| VERIFIED | verdict | 机器复跑痕 verify_run 由 VFY1 起新卡必带 |
| CANCELED | — | cancel 清 claimed_at，落终态 |
<!-- STATE-FIELDS-END -->

## 配置（一切定制走配置，默认值=引擎出厂行为）

优先级：**环境变量 > `config.toml` 文件层 > 内置默认**。文件层发现顺序：
`CC_CONFIG` 指向的文件 > 账本根（`CC_TEAM_HOME`）下的 `config.toml`。
未识别的键只告警不报错（旧版引擎吃新版配置不炸）。

内置默认全部**由仓位置/宿主环境派生**，不含任何机器名、用户名、盘符字面量——
换机器/换用户/换操作系统目录布局，不改一行代码。

常用键（`cc init` 生成的模板里有全量注释）：

| 键 | 环境变量锚 | 含义 |
|---|---|---|
| `host.fleet_root` / `host.user_home` / `host.sessions_dir` / `host.win_root` | `CC_HOME` / `CC_USER` / `CC_PROJ` | 车队根、宿主用户主目录、终端 agent 会话转录目录、Windows 挂载根探测 |
| `fleet.code_root` | `CC_CODE`、`DISPATCH_CODE_ROOT` | 泳道 worktree（`wf-<道>`）所在根 |
| `engine.runner_cmd` | `CC_RUNNER` | 起一次终端 agent 会话的包装命令 |
| `engine.powershell` | `DISPATCH_PS` | Windows 侧解释器（跨 WSL 场景） |
| `dispatch.lane_cap` | `DISPATCH_CAP` | 同时在飞泳道上限（默认 6） |
| `dispatch.heartbeat_path` | `DISPATCH_HB` | 派发器心跳文件 |
| `dispatch.bind_wait_s` / `dispatch.dead_grace_s` | `DISPATCH_BIND_WAIT` / `DISPATCH_DEAD_GRACE_S` | 起班后找会话锚的有界等待 / 尸检宽限 |
| `dispatch.redauto_every` | `DISPATCH_REDAUTO_EVERY` | 红检自动化每 N 圈跑一次（默认 10） |
| `thresholds.beat_stale_min` | `CARD_STALE_MIN` | 心跳过期判死分钟（默认 15） |
| `thresholds.silence_release_min` | `CARD_SILENCE_RELEASE_MIN` | 在制断流探针阈值分钟（默认 45） |
| `engine.verify_timeout_s` | `CARD_VERIFY_TIMEOUT` | 验收命令超时（默认 600） |
| `pool.done_sweep_hours` / `pool.done_sweep_fails` | 同名大写 | DONE 积压巡检窗/连败降级数 |
| `rings.<环名>_sec` | `CTRLD_<环名>_SEC` | 控制进程各常驻环周期秒（详见模板） |
| `tasks.ctrld` / `tasks.boot` / `tasks.fuse` | `CTRLD_TASK_NAME` / `BOOT_TASK_NAME` / `FUSE_TASK_NAME` | 操作系统计划任务名（Windows 侧） |
| `platform.api_base` | `NIGHT_LEARN_BASE` | 外接平台 API 入口（夜环上报类）。真实部署地址是环境定制项——只写本机 `config.toml`，引擎包内不落真实地址、内置默认=本机形态 |

## ops/ 常驻环一览（全部单进程时间轮，或由系统定时器触发一次后常驻）

- `dispatch.py` —— 派单器：按名册找空闲泳道，取队首可领卡，起 worker 会话并绑锚。
- `ctrld.py` —— 控制进程：一个进程内跑所有周期环（派发/回收/红检/看门/合流…），周期可配。
- `recycle.py` —— 回收器：心跳断流、认领未起班、DONE 积压三类场景的自动 release/巡检。
- `check_exit.py` —— agent 会话退出钩子：按会话身份安全结账（防串卡）。
- `fuse.py` —— 交卷未合流看护：到点自动把泳道提交并进主干（干跑净才动）。
- `inbox_watchdog.py` —— 信箱/事件看门：账本事件与外部消息通道的滞留告警。
- `lane_registry.py` —— 泳道名册与垃圾会话目录清理（白名单三源并集，宁漏不误删）。
- `board/` —— 只读看板适配（状态页、运行日志尾、告警面）。

`tests/` 为每个环节配红绿锁：`all.sh` 汇总跑五套（账本/对抗/回收守卫/判活/CC 环境检测），
`oss-release-check.py` 是发布包自身的常驻锁（敏感清零/白名单边界/默认等价三段）。

## 发布边界

发布包只含：`ccards/`、`ops/`、`tests/`、`deploy/`（Linux systemd / macOS launchd 常驻模板）、
`.github/`（CI workflow）、`cards_dag.py`、`README.md`、`pyproject.toml`、`LICENSE`、
`CHANGELOG.md`、`CONTRIBUTING.md`、`SECURITY.md`、`.gitattributes`（脚本强制 LF——
Windows autocrlf 用户 clone 后 bash 不崩的可用性判据）。
**运行现账一律不出包**：`state/`、`logs/`、`claims/`、`briefs/`、`inbox/`、`registry` 实账、
`board/` 定制层、`docs/` 运营台账、`config.toml`。
`ccards/oss_cli.py export --out <dir>`（或 `cc export`）按上述白名单复制，等价一次干净 clone；
`tests/oss-release-check.py` 对该白名单+敏感面常驻把关，改一行发布集都可能让它红。

## 一键命令（`cc`）

`pyproject.toml` 声明控制台入口 `cc = ccards.oss_cli:main`（pip/pipx 安装后可用；
无安装环境时 `python -I ccards/oss_cli.py` 等价）：

- `cc init --dir <仓根>` —— 生成运行时目录骨架、`config.toml` 默认模板、计划任务/cron 模板、
  `cards_dag.py` 直达启动器；检测 Claude Code（缺失只给官方安装指引；`--install-agent`
  才明示后代执行安装）；跑完自检输出 `ALL_PASS`。
- `cc selftest [--engine-home <dir>]` —— 跑引擎自带红绿锁套件。
- `cc export --out <dir>` —— 按发布白名单导出干净发行包。

## 附录：手工底层命令（正常流程用不到，观察/调试/无 daemon 直驱时才碰）

```bash
# 引擎自带的红绿锁（全部断言自包含，只写临时沙箱，不碰真实账）
python -I tests/oss-release-check.py   # 发布自检：敏感清零/白名单边界/默认等价
bash tests/all.sh                      # 五套机制回归：账本/对抗/回收/判活/CC 环境检测

# 不开 ctrld 的手工全链路（一张卡从立到验收走一遍，理解状态机用）
python -I cards_dag.py create REQ-DEMO-1 --title 示例卡 --verify "true" --files "src/"
python -I cards_dag.py claim REQ-DEMO-1 --owner lane-a
python -I cards_dag.py progress REQ-DEMO-1 "开工第一拍" --lane lane-a --epoch 1 --ring 1
python -I cards_dag.py done REQ-DEMO-1 --owner lane-a --commit <hash> --repo <worktree>
python -I cards_dag.py verdict REQ-DEMO-1 PASS --as Verifier
```

接入终端 agent（自动派单两件套，`cc init` 均生成模板）：
- 起会话命令 = 配置项 `engine.runner_cmd`：一个「把提示词喂给终端 agent」的包装脚本样例，
  改成你环境里的真实命令即可（`--lanes` 名册 + 本键 = 派单器知道往哪道起谁）；
- 会话结束自动结账 = 在你的 agent 的 Stop/退出钩子里调 `ops/check_exit.py`（按会话身份
  安全结算，防串卡）。

## 许可

MIT。见 `LICENSE`。
