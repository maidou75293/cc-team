# ccards — cc-team 机制核心包。
#
# 唯一写入口 = 上层 cards_dag.py（或直接 from ccards.cli import run 做进程内调用，ops 侧用）。
# 运行时对象（MECH-PURE1 清零后，账本世界只剩以下件，无任何旧代残留）：
#   事实   state/graph.json     唯一事实：cards 全字段 + 视图字段(环/ready/关键路径/拓扑) + seal 自封
#          每个写命令收尾自动刷新（digest 相同不重写；generation 只在账变时 +1）；dag --check 对账新鲜度。
#   卡锁   claims/<cid>/claim.json  防「一卡二主」（mkdir 原子）
#   道锁   state/lane-<owner>.lock/ 防「一道二活」；card=一行卡号 + meta.json{owner,pid,session_id,kind,ts}
#          取代 _one_wip 全库扫描（扫描有 TOCTOU）。done/release/REJECT/cancel 双锁同清。
#   队列   state/lane-<owner>.queue 候选 FIFO（只收 OPEN；create/push/set --lanes 入队，claim 出队，
#          release/REJECT 回队头，cancel 剔除；派生视图，reindex 可从卡面重建）
#   流水   state/events.log     append-only 审计
#   面板   state/panel.html     DAG 派生视图（随写自动重生成，MECH-VIS1；视图非事实）
#   写锁   state/.writer.lock   全部写命令串行化（修 done×release 丢更新、os.replace 缺失读竞态）
# kind 由入口定：claim=lane（dispatcher 派工；--pid 传真 worker pid 供 recycle 判活；缺省=不可回收，保守向）
#              start=session（PM 直做/临时泳道；recycle 永不 release——MECH-R3 事故定版语义）。
# session_id：env CLAW_SESSION_ID（dispatcher 注入；无则 "-" 走泳道名兼容档）；bind 可回填。
# 冲突永远以卡为准：`cards_dag.py reindex` 重建道锁/队列/图。
# 子卡(split)语义本期不做（业主定：先简单，慢点再说）；队列的 --front 即将来子卡插队的通道。
# 事实访问=ccards.graph 一处（MECH-GRF1：store 并入；Graph 实例=短命现读，写只在闸内）。
# 两族卡（MECH-RESIDENT-1 seq=8）：普通卡只走七态机；岗卡 resident=True 另带有在岗三态
#   ON-DUTY/DEGRADED/OFF（真值=ccards.config RESIDENT_DUTY_*），verdict PASS=验收合格上岗，
#   三态迁移唯一走 duty 闸，四件(duty/anchor/heartbeat/fail)缺位不许立岗卡。
version = "3.4.0"
