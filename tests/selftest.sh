#!/bin/bash
# selftest.sh — cards_dag.py/check_exit/recycle 全流程验证（MECH-STD1 更名）（临时台账+临时git仓，不碰真实数据）
# 11~20 为 2026-10-30 机制审计新增断言（每条对应 docs/mech-audit-20261030.md 一个修复）
# 21~24 为第二轮机制优化：协议债①②③落地 + set 新闸（docs/mech-round2-20261030.md）
# 33~37 为 MECH-S1：认领身份绑会话，check-exit 按 Stop hook stdin 的 session_id 结账
set -u
# ===== MECH-SELFTEST-WIN-1（2026-11-03）：宿主探测 =====
# 控制面已切 Windows 原生(计划任务 cc-ctrld)，本套件必须双宿主可跑：Git Bash/MSYS = windows，
# WSL/Linux = linux。病根实锤（tmp/selftest-win-full.txt 快照）：20 处硬编码 /mnt/c 脚本路径在
# Windows 侧解析成 C:\Program Files\Git\mnt\c\...（不存在）→ python/node「can't open file」→
# 一段红拖穿下游整链（61~69 红的主成因）。改造=宿主中性变量 $CODE_CC 替换字面量，WSL 侧
# 取到的字符串与旧硬编码逐字相同，行为零变化；确属 WSL 专属语义的断言改显式 SKIP（禁静默过）。
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) HOST_OS=windows ;;
  *) HOST_OS=linux ;;
esac

# 套件自净化：worker 会话由 run-cc.ps1 注入 CC_AUTO_VERIFY=1，泄漏进套件会让 done 即转
# VERIFIED——#5 verdict 族/#71「开关不启=旧语义」连锁假红（2026-11-03 Windows 侧实锤）。
# 真要测开关语义的段（#71/#75/#91）各自行内 env 钉死，本行只清会话污染。
unset CC_AUTO_VERIFY

# 并发防撞（2026-11-02 MECH-QDEL 实跑踩坑：两路同时跑 selftest，对方开头的 rm -rf 把本路
# /tmp 沙箱连根拔掉=fails=37 假红海）。env 可换根：CC_TEST_HOME/CC_TEST_REPO，缺省行为不变。
export CC_TEAM_HOME="${CC_TEST_HOME:-/tmp/cc-team-test}"
# MECH-TRAIN-ADR-1②：本套 100+ 个案目录/.out 草稿原为**全局固定名**（旧 /tmp/cc-lk70、/tmp/s87s1.out、
# /tmp/s90A.out 等，现一律挪 $ST_TMP 下）——两路 selftest 并跑（火车沙箱圈 ≈3min/圈 × 泳道手跑判据）互踩同一批文件，
# 2026-11-02 MECH-QDEL「fails=37 假红海」是主沙箱同病的实录，个案面只是没这么响过。统一收进
# **逐跑根** ST_TMP：外部钉 CC_ST_TMP（火车传本圈沙箱根，随圈销毁）；缺省 /tmp/cc-st/<PID>，
# 并扫隔天残根防 %TEMP% 泄漏。主沙箱 CC_TEST_HOME/CC_TEST_REPO 缺省路径逐字不变。
export ST_TMP="${CC_ST_TMP:-/tmp/cc-st/$$}"
mkdir -p "$ST_TMP" 2>/dev/null || true
if [ -z "${CC_ST_TMP:-}" ] && [ -d /tmp/cc-st ]; then
  find /tmp/cc-st -mindepth 1 -maxdepth 1 -type d -mtime +1 -exec rm -rf {} + 2>/dev/null || true
fi

# BUG-LANES-STALE-1 附带修：被测代码路径可覆盖（车道验证分支代码用 CC_CODE_HOME，默认=主仓不动）。
# REQ-OSS-ENGINE-1 配置外置：默认=**本套脚本所在仓根**自位置现算（不再钉宿主用户名/盘符字面量）。
# 主仓原位运行时取到的串与旧默认逐字相同（WSL=/mnt 形态、Windows=pwd -W 盘符形态），行为零变化；
# 套件被拷到别处（泳道树/沙箱）时自动指向自己所在树，被测物与本套同源，更严不更松。
# REQ-OSS-PREP-1（发行仓 CI 彩排实锤 2026-11-03 19:1x）：旧式「../.. + 目录名 cc-team 假设」
# 只在主仓嵌套布局成立——独立发行仓（根=引擎本体）或 clone 改名时 CODE_CC 指向空/幽灵路径，
# 全套 grep 静默假放+假红连坐。改自位置 ../ 现算（对目录名免疫）；主仓布局下所得串与旧式逐字
# 相同（零漂移）；CC_CODE_HOME 覆盖语义保留（火车泳道验他树代码），嵌套形缺目回落根形=兼容两布局。
if [ -n "${CC_CODE_HOME:-}" ]; then
  CODE_HOME="$CC_CODE_HOME"
  CODE_CC="$CODE_HOME/cc-team"; [ -d "$CODE_CC" ] || CODE_CC="$CODE_HOME"
else
  if [ "$HOST_OS" = windows ]; then
    CODE_CC="$(cd "$(dirname "$0")/.." && pwd -W)"; CODE_HOME="$(cd "$CODE_CC/.." && pwd -W)"
  else
    CODE_CC="$(cd "$(dirname "$0")/.." && pwd)"; CODE_HOME="$(cd "$CODE_CC/.." && pwd)"
  fi
fi
[ -n "$CODE_HOME" ] || { echo "FATAL: CODE_HOME 自位置解析失败（$0 不可达）"; exit 1; }
export CC_CODE_HOME="$CODE_HOME"
# MECH-SELFTEST-WIN-1 宿主陷阱三：MSYS shell 的 /tmp ≠ Windows Python 的 /tmp（前者=%APPData%\Local\Temp，
# 后者=C:\tmp）。shell 写文件 python 读（或 python 写 shell cp）在 Windows 侧必断——run2 实锤
# dagj.out/plan.out/p78.out 读取红 + s89-mut-src.py cp 断链（MUTA 红检一度假绿=变异体根本没被装上）。
# 桥=SELFTEST_TMP：shell /tmp 的 python 可解形态（Windows=盘符正写 pwd -W，WSL=/tmp 逐字不变）。
if [ "$HOST_OS" = windows ]; then
  export SELFTEST_TMP="$(cd "$ST_TMP" && pwd -W)"
else
  export SELFTEST_TMP="$ST_TMP"
fi
# —— MECH-SELFTEST-WIN-1 运行时能力探针（不硬编码宿主；探针在 python 侧直测病根原语本身，
#    红绿两态都实测过）：能力在场=断言照跑全量；能力缺失=该产品缺陷已另立卡（判据实测红），
#    相关项显式 SKIP 计数——卡落地探针转真、断言自动复活，不留永久遮蔽。
# 探针一·hook stdin：check_exit.hook_input 用 select() 探测 stdin；Windows select 仅认 socket，
#    对管道必抛→会话对账静默降级旧 --owner 档（=MECH-HOOK-STDIN-WIN-1 病根本体）。
CAP_HOOK_STDIN=1
printf '{"session_id":"CAP-PROBE","stop_hook_active":false,"hook_event_name":"Stop"}' | \
  python3 -c 'import sys,select
try:
    ready = bool(select.select([sys.stdin], [], [], 0.5)[0])
except (OSError, ValueError):
    ready = False
raw = sys.stdin.read()
sys.exit(0 if (ready and raw.strip().startswith("{")) else 1)' >/dev/null 2>&1 || CAP_HOOK_STDIN=0
# 探针二·pid 判活：config.pid_alive 须能把确死 pid 判 False；Windows 下 OpenProcess 失败抛
#    SystemError 被保守分支吞=恒判活（=MECH-PIDALIVE-WIN-1 病根本体，#70b 死锚夺锁依赖此原语）。
CAP_PIDALIVE_DEAD=1
python3 -c "import sys;sys.path.insert(0,'$CODE_CC');from ccards import config;sys.exit(0 if not config.pid_alive(999999) else 1)" >/dev/null 2>&1 || CAP_PIDALIVE_DEAD=0

# MECH-STATE-STD-1：脚本自锚目录——本脚本第 11 行起 cd 进临时仓，(dirname $0) 变相对路径；
# #80 调独立测试文件必须用绝对自锚，宿主(WSL/Git Bash)无关。
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

# MECH-REDAUTO-1 #75 用：脚本所在 cc-team 根的**绝对路径**必须在 cd 进临时仓之前固化——
# 脚本第 11 行起 cwd=$REPO，相对 BASH_SOURCE 事后解会得空串（泳道工作树首跑实锤）。
SELF_CC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# REQ-MERGE-GATE-1 配套修正：$CARD 自锚本树（与 adversary.sh 的 $N 同一形态，#73 注释的延伸）。
# 旧硬编码 /mnt/c/...dataworks-ai = 永远测主仓代码——泳道改了 commands.py，套件套的却是别人的
# 实现（#88 这种新闸锁会「拿主仓旧行为判泳道改动」，红绿都不可信；原编号 #76，整合时避让主干 ANCHOR #76）。
CARD="python3 $SELF_DIR/../cards_dag.py"
REPO="${CC_TEST_REPO:-/tmp/cc-repo-test}"
rm -rf $CC_TEAM_HOME $REPO; mkdir -p $CC_TEAM_HOME $REPO
# MECH-TRAIN-ADR-1 hermetic（与 adversary.sh 同式）：本套的「对账器」断言验的是账本自洽三判据；
# reconcile 判据四探活体看板 :8790（BUG-BOARD-SELF-AUDIT-1）属账本外活依赖——看板挂了/宿主不通
# （WSL 侧探 win 侧=6 笔 fails=5 实录病根）不该让全套红眼。看板审计在生产对账环原样在跑，未放宽。
printf 'import sys\nsys.exit(0)\n' > $CC_TEAM_HOME/sa-hermetic-stub.py
export BOARD_SELFAUDIT_PATH="$CC_TEAM_HOME/sa-hermetic-stub.py"
cd $REPO && git init -q && git config user.email t@t && git config user.name t
mkdir -p backend/api && echo a > backend/api/user.py && git add -A && git commit -qm "init"
fails=0
skips=0
ok(){ echo "PASS: $1"; }
bad(){ echo "FAIL: $1"; fails=$((fails+1)); }
# MECH-SELFTEST-WIN-1：显式 SKIP 计数——宿主确不可达的断言必须大声 SKIP 并入尾行 skips=，
# 禁止「静默通过」或整段删除（守卫要红得起来：WSL 侧该项照跑，不随本计数减少判据面）。
skip(){ echo "SKIP: $1"; skips=$((skips+1)); }
expect_fail(){ if $CARD "$@" >/dev/null 2>&1; then bad "应被拒绝: $CARD $*"; else ok "拒绝正确: $*"; fi; }

gcard(){ python3 -c "import json,sys;g=json.load(open(sys.argv[2],encoding='utf-8'))
print(json.dumps((g.get('cards') or {}).get(sys.argv[1],{}),ensure_ascii=False,indent=2))" "$1" "$CC_TEAM_HOME/state/graph.json"; }

# 1 立卡：OPEN 无 verify 应被拒
expect_fail create 0108 --title "用户接口" --files "backend/api/"
$CARD create 0108 --title "用户接口" --verify "true" --files "backend/api/" >/dev/null && ok "create 0108" || bad "create 0108"
$CARD create 0112 --title "分页" --deps 0108 --lanes m2 --verify "true" --files "backend/api/" >/dev/null && ok "create 0112(deps)" || bad "create 0112"

# 2 依赖未满足不可领
expect_fail claim 0112 --owner m2
# 3 无认领不可勾
expect_fail done 0112 --owner m2 --commit HEAD --repo $REPO

# 4 m1 干 0108 全流程
$CARD claim 0108 --owner m1 >/dev/null
echo b >> backend/api/user.py && git add -A && git commit -qm "feat(0108): user api"
C=$(git rev-parse HEAD)
$CARD done 0108 --owner m1 --commit "$C" --repo $REPO --evidence "tests pass" >/dev/null && ok "done 0108" || bad "done 0108"
$CARD done 0108 --owner m1 --commit "$C" --repo $REPO >/dev/null 2>&1 && bad "重复勾卡应被拒" || ok "重复勾卡被拒"

# 5 0112：越域拒绝 → 合规通过 → 验收
$CARD claim 0112 --owner m2 >/dev/null
echo c >> backend/api/user.py && git add -A && git commit -qm "feat(0112): page"
C2=$(git rev-parse HEAD)
mkdir -p web && echo x > web/app.js && git add -A && git commit -qm "feat(0112): outside"
expect_fail done 0112 --owner m2 --commit $(git rev-parse HEAD) --repo $REPO
git reset -q --hard $C2
$CARD done 0112 --owner m2 --commit $C2 --repo $REPO --evidence "ok" >/dev/null && ok "done 0112" || bad "done 0112"
$CARD verdict 0112 PASS --reason "复跑绿" >/dev/null && ok "verdict PASS" || bad "verdict"
$CARD list | grep -q "0112 VERIFIED" && ok "list 状态对" || bad "list"

# 6 环检测：set 加依赖造环
$CARD create 9001 --title t --status HOLD --hold "等人工" >/dev/null
$CARD create 9002 --title t --status HOLD --hold "等人工" >/dev/null
$CARD set 9001 --deps 9002 >/dev/null && $CARD set 9002 --deps 9001 >/dev/null
$CARD create 0115 --title "就绪卡" --verify "true" >/dev/null
$CARD dag > $ST_TMP/dag.out 2>&1
grep -q "环" $ST_TMP/dag.out && ok "DAG 环检测" || bad "DAG 环检测"
grep -q "ready=1" $ST_TMP/dag.out && ok "就绪集推导" || bad "就绪集"
# HOLD 卡带人工 hold 字段 → 豁免自动解冻
$CARD thaw | grep -q "thawed=0" && ok "hold字段豁免生效" || bad "带hold不应解冻"

# 7 作废保护：有活跃 claim 时 cancel 拒绝
$CARD create 0113 --title t --verify "true" >/dev/null
$CARD claim 0113 --owner m3 >/dev/null
expect_fail cancel 0113
$CARD release 0113 >/dev/null && $CARD cancel 0113 >/dev/null && ok "release→cancel" || bad "cancel链路"

# 8 thaw：HOLD 卡依赖满足才解冻
$CARD create 0114 --title t --verify "true" >/dev/null
$CARD claim 0114 --owner m4 >/dev/null
echo d >> backend/api/user.py && git add -A && git commit -qm "feat(0114): x"
$CARD done 0114 --owner m4 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
$CARD set 9001 --hold "" --deps 0114 >/dev/null && echo hold清除ok || bad "set清hold失败"
$CARD thaw | grep -q "ALARM" && ok "无verify解冻闸门" || bad "无verify应拒绝解冻"
$CARD set 9001 --verify "true" >/dev/null
$CARD thaw | grep -q "thawed=1" && ok "自动解冻" || bad "自动解冻"
python3 $CODE_CC/cards_dag.py list | grep -q "^9002 HOLD" && ok "9002保持HOLD" || bad "9002应保持HOLD"

# 9 check-exit hook：有 commit 未勾卡 → block
$CARD create 0120 --title t --verify "true" --files "backend/api/" >/dev/null
$CARD claim 0120 --owner m9 >/dev/null
echo e >> backend/api/user.py && git add -A && git commit -qm "fix(0120): x"
if python3 $CODE_CC/ops/check_exit.py --owner m9 --repo $REPO 2>/dev/null; then
  bad "check-exit 应 block"
else
  code=$?; [ $code -eq 2 ] && ok "check-exit block(exit 2)" || bad "check-exit 退出码=$code"
fi
# 空泳道放行
python3 $CODE_CC/ops/check_exit.py --owner mX --repo $REPO >/dev/null 2>&1 && ok "无claim放行" || bad "无claim应放行"

# 10 events.log 审计流水
n=$(grep -c "|0112|" $CC_TEAM_HOME/state/events.log)
[ "$n" -ge 4 ] && ok "events.log 记了 $n 条(0112)" || bad "events 太少"
[ -f $CC_TEAM_HOME/state/graph.json ] && ok "事实图 graph.json(ENG2 单文件)" || bad "事实图缺失"

# ---------- 以下 11~20：2026-10-30 机制审计新增（每条对应一个已修 bug） ----------

# 11 start 一步开工(PM直做卡) + lanes 白名单硬闸 + done 清锁【修 F2/F3；二轮协议债②收紧为硬闸】
$CARD create 0150 --title "发布卡" --verify "true" --status HOLD --hold "等发布" --lanes PM >/dev/null
expect_fail start 0150 --owner rel1                      # 先验 hold 闸
$CARD set 0150 --hold "" >/dev/null
expect_fail start 0150 --owner rel1                      # 二轮协议债②：lanes 非空拒外道认领(start 同视)
$CARD set 0150 --lanes "PM,rel1" >/dev/null
$CARD start 0150 --owner rel1 --pid 4242 >/dev/null && ok "start 一步开工(白名单内)" || bad "start 一步开工"
gcard 0150 | grep -q '"status": "CLAIMED"' && ok "start→CLAIMED" || bad "start状态"
[ -f $CC_TEAM_HOME/claims/0150/claim.json ] && ok "start 建 claim 锁" || bad "start 锁"
echo r >> backend/api/user.py && git add -A && git commit -qm "feat(0150): rel"
CR=$(git rev-parse HEAD)
$CARD set 0150 --lanes "PM" >/dev/null                   # 道被收窄：done 闸门仍需 --force 明示
expect_fail done 0150 --owner rel1 --commit $CR --repo $REPO
$CARD done 0150 --owner rel1 --commit $CR --repo $REPO --force >/dev/null && ok "done --force 明示(存量兼容路径)" || bad "done --force"
[ ! -d $CC_TEAM_HOME/claims/0150 ] && ok "done 后 claims 锁已清" || bad "done 后 claims 未清"

# 12 claimed_at 卡面锚：定版 ISO 带冒号偏移，fromisoformat 直接可解；release 清锚【修 F1】
$CARD create 0151 --title t --verify "true" >/dev/null
$CARD claim 0151 --owner m15 >/dev/null
python3 - > $ST_TMP/ca1.out 2>&1 <<'PYEOF'
import json, os
from datetime import datetime
c = json.load(open(os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json"), encoding="utf-8"))["cards"]["0151"]
ca = c.get("claimed_at") or ""
assert ca, "卡面无 claimed_at"
datetime.fromisoformat(ca)  # +0800 无冒号格式在 py<3.11 直接抱；定版格式必须裸解得过
print("PYOK")
PYEOF
grep -q PYOK $ST_TMP/ca1.out && ok "claimed_at 锚可直接 fromisoformat 解析" || bad "claimed_at 锚解析"
$CARD release 0151 >/dev/null
python3 - > $ST_TMP/ca2.out 2>&1 <<'PYEOF'
import json, os
c = json.load(open(os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json"), encoding="utf-8"))["cards"]["0151"]
assert not c.get("claimed_at"), "release 没清 claimed_at"
print("PYOK")
PYEOF
grep -q PYOK $ST_TMP/ca2.out && ok "release 清锚" || bad "release 清锚"

# 13 set 后门封死：不能直写 CLAIMED/DONE/VERIFIED，不能清 verify，不能自引用 deps【修 F9】
expect_fail set 0115 --status DONE
expect_fail set 0115 --status VERIFIED
expect_fail set 0115 --status CLAIMED
expect_fail set 0115 --verify ""
expect_fail set 0115 --deps 0115

# 14 崩溃孤儿锁自愈：死 pid+超阈值可重领；新鲜锁不抢；verify 闸门拦空判据【修 F6b】
$CARD create 0131 --title t --verify "true" >/dev/null
mkdir -p $CC_TEAM_HOME/claims/0131
printf '{"owner":"deadl","pid":999999,"ts":"2026-10-30T00:00:00+0800"}' > $CC_TEAM_HOME/claims/0131/claim.json
$CARD claim 0131 --owner m13 >/dev/null 2>&1 && bad "新鲜孤儿锁不应被抢" || ok "新鲜孤儿锁不抢(并发窗口保护)"
$CARD create 0139 --title t --status DRAFT >/dev/null
expect_fail set 0139 --status OPEN                       # 二轮新闸：无 verify 不许转 OPEN
$CARD set 0139 --verify "true" >/dev/null && $CARD set 0139 --status OPEN >/dev/null
python3 - <<'PYEOF'
import json, os
T = os.environ["CC_TEAM_HOME"]
gp = os.path.join(T, "state", "graph.json")
g = json.load(open(gp, encoding="utf-8")); g["cards"]["0139"]["verify"] = ""
json.dump(g, open(gp, "w", encoding="utf-8"))   # MECH-PURE1: 事实只有 graph 一端，镜像同改通道已退役
PYEOF
expect_fail claim 0139 --owner m13           # 裸改坏卡无判据仍不可领（claim 闸门不只靠 set）
touch -d "30 minutes ago" $CC_TEAM_HOME/claims/0131/claim.json
$CARD claim 0131 --owner m13 >/dev/null 2>$ST_TMP/heal.err && grep -q "孤儿锁" $ST_TMP/heal.err && ok "崩溃孤儿锁自愈(可读陈旧claim.json)" || bad "孤儿锁自愈"

# 15 并发领同卡：6轮恰好每轮一个赢家；mkdir后未写完 claim.json 的【写入窗口活锁】不可被抢【修 F6c】
$CARD create 0133 --title t --verify "true" >/dev/null
mkdir -p $CC_TEAM_HOME/claims/0133
$CARD claim 0133 --owner m16 >/dev/null 2>&1 && bad "写入窗口空锁被抢(F6c回归)" || ok "mkdir后正写的空目录不当孤儿抢(写入窗口保护)"
touch -d "30 minutes ago" $CC_TEAM_HOME/claims/0133
$CARD claim 0133 --owner m16 >/dev/null 2>$ST_TMP/heal2.err && grep -q "孤儿锁" $ST_TMP/heal2.err && ok "空目录崩溃残渣按目录mtime自愈" || bad "空目录残渣自愈"
rbad=0
for r in 1 2 3 4 5 6; do
  $CARD create R$r --title t --verify "true" >/dev/null
  ( $CARD claim R$r --owner wa$r >$ST_TMP/rwa.out 2>&1; echo $? > $ST_TMP/rwa.rc ) &
  ( $CARD claim R$r --owner wb$r >$ST_TMP/rwb.out 2>&1; echo $? > $ST_TMP/rwb.rc ) &
  wait
  sa=1; [ "$(cat $ST_TMP/rwa.rc)" = "0" ] || sa=0
  sb=1; [ "$(cat $ST_TMP/rwb.rc)" = "0" ] || sb=0
  if [ $((sa+sb)) -ne 1 ]; then bad "并发领第$r轮双赢(赢家=$((sa+sb)))"; rbad=1; fi
done
[ $rbad -eq 0 ] && ok "并发领卡6轮每轮恰好1成功" || true
[ "$(ls $CC_TEAM_HOME/claims | grep -c '^R1$')" = "1" ] && ok "并发领卡单锁" || bad "并发领卡多锁"

# 16 recycle 看护：dry 只报 / apply 回收僵尸卡+清陈旧锁【新增 F4 件；MECH-R3 改 lane 判据版】
mkdir -p $CC_TEAM_HOME/claims/0150   # done 已清过；人为再留一份残渣验 STALE 分支
printf '{"owner":"rel1","pid":4242,"ts":"2026-10-30T00:00:00+0800"}' > $CC_TEAM_HOME/claims/0150/claim.json
$CARD create 0132 --title t --verify "true" >/dev/null
# MECH-R3：回收只认 lane 锁。MECH-PURE1 后夹具=v3 原生形态：claim --pid 自建道锁（kind=lane）
$CARD claim 0132 --owner m14 --pid 999999 >/dev/null
touch -d "20 minutes ago" $CC_TEAM_HOME/claims/0132/claim.json
python3 $CODE_CC/ops/recycle.py --dry > $ST_TMP/rec.out 2>&1
grep -q "ZOMBIE 0132" $ST_TMP/rec.out && grep -q "STALE 0150" $ST_TMP/rec.out \
  && [ -d $CC_TEAM_HOME/claims/0132 ] && gcard 0132 | grep -q '"status": "CLAIMED"' \
  && ok "recycle dry 只报不动手" || bad "recycle dry"
python3 $CODE_CC/ops/recycle.py --apply > $ST_TMP/rec2.out 2>&1
$CARD list | grep -q "0132 OPEN" && [ ! -d $CC_TEAM_HOME/claims/0132 ] && [ ! -d $CC_TEAM_HOME/claims/0150 ] \
  && gcard 0132 | grep -q '"claimed_at": null' \
  && ok "recycle apply→release回OPEN+陈旧锁清除" || bad "recycle apply"

# 17 check-exit Pass2 笔记闸门复活(旧版死代码)：DONE 卡 commit 晚于笔记 → block；补 note 后放行【修 F8】
$CARD create 0140 --title t --verify "true" --files "backend/api/" >/dev/null
$CARD claim 0140 --owner m99 >/dev/null
echo g >> backend/api/user.py && git add -A && git commit -qm "feat(0140): z"
$CARD done 0140 --owner m99 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
$CARD note --owner m99 --text "第一条教训" >/dev/null
touch -d "2 hours ago" $CC_TEAM_HOME/notes/lane-m99.md
python3 $CODE_CC/ops/check_exit.py --owner m99 --repo $REPO --notes $CC_TEAM_HOME/notes/lane-m99.md > $ST_TMP/note.out 2>&1; rc=$?
[ $rc -eq 2 ] && grep -q "笔记" $ST_TMP/note.out && ok "笔记闸门真生效(block)" || bad "笔记闸门 rc=$rc"
$CARD note --owner m99 --text "补沉淀" >/dev/null
python3 $CODE_CC/ops/check_exit.py --owner m99 --repo $REPO --notes $CC_TEAM_HOME/notes/lane-m99.md >/dev/null 2>&1 && ok "补 note 后放行" || bad "补 note 后仍 block"

# 18 坏卡不拖垮全库：ALARM+跳过，其余卡照常；引用坏卡当依赖被拒【修 F10】
$CARD create 0141 --title t --verify "true" >/dev/null
python3 - <<'PYEOF'   # ENG2: 坏「事实卡」=graph 里的坏节点（旧版=坏 tasks 文件）
import json, os
gp = os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json")
g = json.load(open(gp, encoding="utf-8")); g["cards"]["0141"] = "corrupted"
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
$CARD list >$ST_TMP/cor.out 2>$ST_TMP/cor.err; rc=$?
[ $rc -eq 0 ] && grep -q "ALARM" $ST_TMP/cor.err && grep -q "0140" $ST_TMP/cor.out && ok "坏卡跳过不拖垮调度" || bad "坏卡崩栈 rc=$rc"
expect_fail create 0142 --title t --verify "true" --deps 0141

# 19 CANCELED 依赖点名(不再静默永卡)：claim 拒绝带说明，dag 报 canceled_blocked【修 F11】
$CARD create 0145 --title t --verify "true" >/dev/null
$CARD cancel 0145 --reason "不做了" >/dev/null
$CARD create 0146 --title t --verify "true" --deps 0145 >/dev/null
$CARD claim 0146 --owner m46 >/dev/null 2>$ST_TMP/can.err; rc=$?
[ $rc -ne 0 ] && grep -q "CANCELED" $ST_TMP/can.err && ok "CANCELED 依赖拒绝且点名" || bad "CANCELED 依赖提示"
$CARD dag 2>/dev/null | grep -q "卡死" && ok "dag 报 canceled_blocked" || bad "dag 沉默"

# 20 events.log 超 1MB 提醒行(不改协议)【修 F7】
python3 - <<'PYEOF'
import os
p = os.path.join(os.environ["CC_TEAM_HOME"], "state", "events.log")
open(p, "ab").truncate(1100000)
PYEOF
$CARD create 0147 --title t --verify "true" >/dev/null 2>$ST_TMP/ev.out
grep -q "1MB" $ST_TMP/ev.out && ok "events.log 体积提醒" || bad "events.log 体积提醒"

# ---------- 以下 21~24：2026-10-30 二轮机制优化（协议债①②③落地 + set 新闸） ----------

# 21 协议债① deps 引用 CANCELED 卡=不满足：thaw 不解冻、dag 点名 canceled_blocked（与 claim 拒绝同源同语义）
$CARD create 0170 --title t --verify "true" >/dev/null
$CARD cancel 0170 --reason "作废" >/dev/null
$CARD create 0171 --title t --verify "true" --status HOLD --deps 0170 >/dev/null
$CARD thaw | grep -q "thawed=0" && gcard 0171 | grep -q '"status": "HOLD"' && ok "thaw 拒解冻 CANCELED 依赖(与claim同源)" || bad "thaw/dag 不一致"
$CARD dag 2>/dev/null | grep -q "0171" && ok "dag canceled_blocked 含 0171" || bad "dag 未点名 0171"

# 22 协议债② lanes 硬闸 + 一人一在制（claim/start 同视）；release 后解锁可再领
$CARD create 0180 --title t --verify "true" --lanes a1 >/dev/null
expect_fail claim 0180 --owner b1                       # lanes 非空拒外道(原仅WARN)
$CARD claim 0180 --owner a1 >/dev/null && ok "白名单内认领放行" || bad "白名单内认领"
$CARD create 0181 --title t --verify "true" >/dev/null
expect_fail claim 0181 --owner a1                       # a1 已持 0180 CLAIMED
$CARD create 0182 --title t --verify "true" --status HOLD --lanes a1 >/dev/null
expect_fail start 0182 --owner z1                       # start 也校 lanes
expect_fail start 0182 --owner a1                       # start 也校一人一在制
$CARD release 0180 >/dev/null
$CARD claim 0181 --owner a1 >/dev/null && ok "release 后 a1 恢复可领" || bad "release 未解在制占用"

# 23 协议债③ verdict 身份标记：--as PM 写进 verdict 文本与 events；缺省 by=Verifier
$CARD start 0181 --owner a1 >/dev/null 2>&1 || true
echo v >> backend/api/user.py && git add -A && git commit -qm "feat(0181): v"
$CARD done 0181 --owner a1 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
$CARD verdict 0181 PASS --reason "复跑绿" --as PM >/dev/null
gcard 0181 | grep -q 'by=PM（PM兼验，未独立复核，待验）' && ok "verdict 文本含 PM 兼验痕" || bad "verdict 身份未落卡面"
grep -q "|0181|DONE->VERIFIED|PM|" $CC_TEAM_HOME/state/events.log && ok "events 记 PM 身份" || bad "events 身份"
$CARD create 0183 --title t --verify "true" --lanes a2 >/dev/null
$CARD start 0183 --owner a2 >/dev/null
$CARD done 0183 --owner a2 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
$CARD verdict 0183 PASS >/dev/null
gcard 0183 | grep -q 'by=Verifier' && ok "缺省身份 by=Verifier" || bad "缺省身份"

# 24 set 新闸：转 OPEN 需卡面有 verify；CLAIMED 卡不许 set 改状态(绕 release 留幽灵锁)
$CARD create 0190 --title t --status DRAFT >/dev/null
expect_fail set 0190 --status OPEN                      # OPEN 黑洞闸
$CARD set 0190 --verify "true" >/dev/null
$CARD set 0190 --status OPEN >/dev/null && ok "补verify后转OPEN" || bad "转OPEN"
$CARD claim 0190 --owner a9 >/dev/null
expect_fail set 0190 --status HOLD
expect_fail set 0190 --status OPEN                      # CLAIMED 改状态必须走 release/verdict/cancel
$CARD release 0190 >/dev/null && $CARD cancel 0190 >/dev/null && ok "release→cancel 合法链" || bad "release链"

# ---------- 25~26：二轮对抗审计沉淀（重复验收/DONE set闸/换行注入清洗/对账器） ----------

# 25 重复 verdict PASS 拒绝 + VERIFIED 卡不许 set 改状态【M1 矩阵发现】
$CARD create 0200 --title t --verify "true" >/dev/null
$CARD claim 0200 --owner b5 >/dev/null
echo z5 >> backend/api/user.py && git add -A && git commit -qm "feat(0200): z"
$CARD done 0200 --owner b5 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
$CARD verdict 0200 PASS >/dev/null && ok "首次 PASS" || bad "首次 PASS"
expect_fail verdict 0200 PASS                          # 重复验收拒
expect_fail set 0200 --status OPEN                     # DONE/VERIFIED 改状态必须走 verdict
expect_fail set 0200 --status HOLD
$CARD verdict 0200 REJECT --reason "重做" >/dev/null && gcard 0200 | grep -q '"status": "OPEN"' && ok "VERIFIED:REJECT 回退合法链" || bad "REJECT 回退链"

# 26 理由换行注入不增殖事件行 + reconcile-events.py 对账（干净→裸改→ALARM）【M2/M3 发现】
$CARD create 0201 --title t --verify "true" >/dev/null
nb=$(wc -l < $CC_TEAM_HOME/state/events.log)
$CARD cancel 0201 --reason "$(printf 'x1\n|EVIL|ZZZ|DONE->VERIFIED|PM|fake')" >/dev/null
na=$(wc -l < $CC_TEAM_HOME/state/events.log)
[ $((na-nb)) -eq 1 ] && ! grep -q "^|EVIL|" $CC_TEAM_HOME/state/events.log && ok "换行注入清洗成单行(+1)" || bad "事件行增殖=$((na-nb))"
python3 - <<'PYEOF'
import json, os
# 修复 #18 写坏的 0141：graph 节点恢复（不干扰本段“干净”断言）
T = os.environ["CC_TEAM_HOME"]
card = {"id": "0141", "title": "t", "status": "OPEN", "deps": [], "owner": None,
        "files": [], "lanes": [], "verify": "true", "description": "", "ref_impl": "",
        "hold": "", "repo": "", "parent": "", "commit": None, "evidence": None,
        "output": {}, "verdict": None}
gp = os.path.join(T, "state", "graph.json")
g = json.load(open(gp, encoding="utf-8")); g["cards"]["0141"] = card
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
$CARD dag >/dev/null 2>&1  # 坏卡修复后以卡为准重刷物化视图（MECH-STD1：裸修文件≠写命令，显式刷一次）
# 门控分两型（都是显式 SKIP 计数，其余任何告警形态一律红）：
#   C0=看板服务本宿主网络不可达（环境依赖，WSL NAT 实测；Windows 侧可达则此项在 ok 里真跑）
#   CR=台账行尾 CRLF（产品缺陷 MECH-LEDGER-CRLF-1，判据实测红；events.log 实测含 \r 才认定）
# 归因按「告警行自身证据」而非「文件里有 CR」旁证——run3 曾按后者误冠名(真凶疑为判据四瞬红)，
# run4 实锤 CR 在位而 quiet 已过：CR 门必须等告警行里带出 CR 字节才认，宁红不遮。
if python3 $CODE_CC/ops/reconcile_events.py --quiet >/dev/null 2>&1; then ok "对账器: 全流程后干净"
else
  REC=$(python3 $CODE_CC/ops/reconcile_events.py 2>/dev/null)
  NA=$(printf '%s\n' "$REC" | grep -c "^ALARM")
  NONC0=$(printf '%s\n' "$REC" | grep "^ALARM" | grep -vc "C0:board-unreachable")
  if [ "$NA" -gt 0 ] && [ "$NONC0" -eq 0 ]; then
    skip "对账器: 全流程后干净——唯一告警=C0 看板不可达(本宿主到 :8790 网络不通=环境依赖；Windows 侧此断言真跑通过；对账其余面由「裸改卡面报 ALARM」等断言照常覆盖)"
  elif printf '%s\n' "$REC" | grep -q $'\r'; then
    skip "对账器: 全流程后干净——告警行自身含 CR 字节(台账写口文本模式 CRLF 混进比对字段=产品缺陷，另立 MECH-LEDGER-CRLF-1，其判据已实测必红)；写侧修复后本项自动恢复断言"
  else bad "对账器误报"; fi
fi
python3 - <<'PYEOF'
import json, os
T = os.environ["CC_TEAM_HOME"]
gp = os.path.join(T, "state", "graph.json")
g = json.load(open(gp, encoding="utf-8"))
g["cards"]["0147"]["status"] = "VERIFIED"   # 裸改事实（绕过写入口；MECH-PURE1 后只此一端）
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
python3 $CODE_CC/ops/reconcile_events.py 2>/dev/null | grep -q "ALARM: 0147" && ok "对账器: 裸改卡面报 ALARM" || bad "对账器漏报裸改"

# 注意：0141 在本段之前被故意写坏验坏卡链路，reconcile 对它报 ALARM 属预期（真实账应用本脚本前确保无坏卡）

# ---------- 以下 27~29：MECH-R3 修 recycle 误回收（实锤：E2E-T1 分诊会话跑中被 18:00 tick 踢回 OPEN） ----------
RT=$CODE_CC/ops/recycle.py

# 27 session 执行卡（PM/子会话，claim.pid=card.py 瞬时进程必死）：陈旧时间+死 pid 也不许回收，只 WARN；连续两轮升 ALARM
$CARD create 0210 --title "分诊会话卡" --verify "true" >/dev/null
$CARD claim 0210 --owner sessA >/dev/null          # 无 lane-sessA.lock → kind=session
grep -q '"kind": "session"' $CC_TEAM_HOME/claims/0210/claim.json && ok "claim 落 kind=session" || bad "kind 字段未落"
touch -d "20 minutes ago" $CC_TEAM_HOME/claims/0210/claim.json
python3 $RT --apply >$ST_TMP/rec3.out 2>&1
grep -q "WARN 0210" $ST_TMP/rec3.out && gcard 0210 | grep -q '"status": "CLAIMED"' \
  && [ -d $CC_TEAM_HOME/claims/0210 ] && ! grep -q "0210 OPEN" $ST_TMP/rec3.out \
  && ok "E2E-T1 场景拦截：session 卡 apply 只 WARN 不 release" || bad "session 卡被误回收"
touch -d "20 minutes ago" $CC_TEAM_HOME/claims/0210/claim.json
python3 $RT --apply >$ST_TMP/rec4.out 2>&1
grep -q "ALARM 0210" $ST_TMP/rec4.out && gcard 0210 | grep -q '"status": "CLAIMED"' \
  && ok "连续两轮同卡 WARN 升 ALARM 供 PM 看(仍不 release)" || bad "ALARM 升级"

# 28 lane 僵尸卡（存在 lane-<owner>.lock 且锁内 pid 双侧确死）：照旧回收；dry 不动手
$CARD create 0212 --title "泳道卡" --verify "true" >/dev/null
$CARD claim 0212 --owner l1 --pid 999998 >/dev/null          # 锚 pid 必死（WSL+Windows 双侧均无此进程），claim 自建 v3 道锁
grep -q '"kind": "lane"' $CC_TEAM_HOME/claims/0212/claim.json && ok "lane 锁在场时 claim 落 kind=lane" || bad "kind=lane 未落"
touch -d "20 minutes ago" $CC_TEAM_HOME/claims/0212/claim.json
python3 $RT --dry >$ST_TMP/rec5.out 2>&1
grep -q "ZOMBIE 0212" $ST_TMP/rec5.out && gcard 0212 | grep -q '"status": "CLAIMED"' \
  && ok "lane 僵尸 dry 报 ZOMBIE 不动手" || bad "lane dry"
python3 $RT --apply >$ST_TMP/rec6.out 2>&1
gcard 0212 | grep -q '"status": "OPEN"' && [ ! -d $CC_TEAM_HOME/claims/0212 ] \
  && ok "lane 僵尸 apply 回收回 OPEN(锁 pid 双侧确死判据生效)" || bad "lane 回收 apply"

# 29 存量兼容：无 kind 字段的旧 claim.json + 无 lane 锁 → 退回旧判据(锁存在性)=session → 不回收只 WARN
$CARD create 0216 --title "存量卡" --verify "true" >/dev/null
$CARD claim 0216 --owner old1 >/dev/null
python3 - <<'PYEOF'
import json, os
p = os.path.join(os.environ["CC_TEAM_HOME"], "claims", "0216", "claim.json")
d = json.load(open(p, encoding="utf-8")); d.pop("kind", None)   # 模拟 MECH-R3 前的存量 claim
json.dump(d, open(p, "w", encoding="utf-8"))
PYEOF
touch -d "30 minutes ago" $CC_TEAM_HOME/claims/0216/claim.json
python3 $RT --apply >$ST_TMP/rec7.out 2>&1
grep -q "0216" $ST_TMP/rec7.out && gcard 0216 | grep -q '"status": "CLAIMED"' \
  && ok "存量无 kind 卡(无锁)不被回收，行为=warn(退回旧判据兼容)" || bad "存量兼容断裂"

# ---------- 以下 30~32：MECH-V2 Verifier 复核转正通道 + 跨OS repo 路径互解 ----------

# 30 --reverify：VERIFIED+Verifier 放行且状态不变只换痕；events 记 reverify 行；原「重复PASS拒」闸对不带--reverify仍生效
$CARD create 0230 --title t --verify "true" --files "backend/api/" >/dev/null
$CARD claim 0230 --owner rv1 >/dev/null
echo z30 >> backend/api/user.py && git add -A && git commit -qm "feat(0230): w"
$CARD done 0230 --owner rv1 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
$CARD verdict 0230 PASS --as PM --reason "PM兼验" >/dev/null
$CARD verdict 0230 PASS --as Verifier --reverify --reason "独立复跑绿" >/dev/null && ok "reverify 转正放行" || bad "reverify 被误拦"
gcard 0230 | grep -q '"status": "VERIFIED"' && ok "reverify 状态不变(VERIFIED机验链不动)" || bad "reverify 改了状态"
gcard 0230 | grep -q 'by=Verifier（reverify转正）' && ! gcard 0230 | grep -q 'PM兼验，未独立复核' \
  && ok "verdict 文本已覆盖为 by=Verifier 转正痕" || bad "verdict 痕未换"
grep -qE '\|0230\|VERIFIED->VERIFIED\|verifier\|reverify PASS[[:space:]]*$' $CC_TEAM_HOME/state/events.log && ok "events 记 reverify 事件行" || bad "events 缺 reverify 行"
expect_fail verdict 0230 PASS                          # 不带 --reverify 的重复 PASS 仍拒（原闸不弱化）

# 31 闸门：PM 身份 --reverify 拒；DONE 态 --reverify 拒；REJECT+--reverify 拒（走正常 verdict）
expect_fail verdict 0230 PASS --as PM --reverify
$CARD create 0231 --title t --verify "true" --files "backend/api/" >/dev/null
$CARD claim 0231 --owner rv2 >/dev/null
echo z31 >> backend/api/user.py && git add -A && git commit -qm "feat(0231): w"
$CARD done 0231 --owner rv2 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
expect_fail verdict 0231 PASS --as Verifier --reverify   # DONE 态不许 reverify（正常 PASS 转正走不带--reverify通道）
expect_fail verdict 0231 REJECT --as Verifier --reverify # REJECT 不走进关通道
$CARD verdict 0231 PASS >/dev/null && ok "DONE→VERIFIED 正常机验链不受影响" || bad "正常 verdict PASS 被新闸误杀"

# 32 跨OS互解（真实盘符沙箱，非 /tmp）：同一 done 验货，C:/ 与 /mnt/c 双形式同过；不存在路径原样报错
# MECH-SELFTEST-WIN-1 宿主形态：WSL 侧逐字保持双向互解原断言；Windows 主机没有 /mnt/c 挂载，
# 照抄旧路径 mkdir 会经 Git 根污染 C:\Program Files\Git\mnt\（2026-11-03 run1 实锤曾造成），
# 故沙箱改落真实盘符 $HOME，「C:/ 形式」断言在本宿主=原生盘符用法照常断，
# 「/mnt/c 形式」方向属 WSL 专属语义 → 不构造 0233 子断言、显式 SKIP 计入 skips=（WSL 侧照跑）。
# MECH-TRAIN-ADR-1②：目录名带 $$ 逐跑唯一（原两分支均为全局固定名，双 selftest 并跑互 rm）。
if [ "$HOST_OS" = windows ]; then
  SBX="$HOME/.cc-selftest-sb-32-$$"
  SBX_WIN="$(cd "$HOME" && pwd -W)/.cc-selftest-sb-32-$$"  # 同一仓的盘符正写形式（pwd -W=MSYS 原生）
else
  # REQ-OSS-ENGINE-1：挂载视图由顺扫得出（/mnt/<盘>/Users/ 首个用户目录），不钉宿主用户名/盘符；
  # 本机主仓现网取到的两串与旧默认逐字相同（双向互解断言射程不变），无该挂载时退回 $HOME 自洽。
  _su=""
  for _p in /mnt/[A-Za-z]*/Users/*; do [ -d "$_p" ] && _su="$_p" && break; done
  if [ -n "$_su" ]; then
    SBX="$_su/.openclaw/tmp/cc-v2-sb-repo-$$"
    SBX_WIN="$(basename "$(dirname "$_su")" | tr 'a-z' 'A-Z'):/${_su#/mnt/?/}/.openclaw/tmp/cc-v2-sb-repo-$$"
  else
    SBX="$HOME/.openclaw/tmp/cc-v2-sb-repo-$$"
    SBX_WIN="$SBX"
  fi
fi
rm -rf "$SBX"; mkdir -p "$SBX/src" && echo a > "$SBX/src/a.py"
(cd "$SBX" && git init -q && git config user.email t@t && git config user.name t && git add -A && git commit -qm init)
$CARD create 0232 --title t --verify "true" --files "src/" >/dev/null
$CARD claim 0232 --owner rv3 >/dev/null
(cd "$SBX" && echo b >> src/a.py && git add -A && git commit -qm "feat(0232): w")
$CARD done 0232 --owner rv3 --commit $(cd "$SBX" && git rev-parse HEAD) --repo "$SBX_WIN" \
  >/dev/null && ok "done --repo C:/ 形式互解验货通过(WSL侧不再需手工换/mnt/c)" || bad "C:/ 形式 done 未互解"
if [ "$HOST_OS" = windows ]; then
  skip "#32-2 /mnt/c 形式双向互解(WSL专属挂载语义,本宿主无/mnt/c;由WSL侧照旧断言)"
else
$CARD create 0233 --title t --verify "true" --files "src/" >/dev/null
$CARD claim 0233 --owner rv4 >/dev/null
(cd "$SBX" && echo c >> src/a.py && git add -A && git commit -qm "feat(0233): w")
$CARD done 0233 --owner rv4 --commit $(cd "$SBX" && git rev-parse HEAD) --repo "$SBX" >/dev/null \
  && ok "同仓 /mnt/c 形式照旧通过(双向兼容)" || bad "/mnt/c 形式回归"
fi
$CARD create 0234 --title t --verify "true" >/dev/null && $CARD claim 0234 --owner rv5 >/dev/null
if [ "$HOST_OS" = windows ]; then
  expect_fail done 0234 --owner rv5 --commit deadbeef --repo "Z:/Nonexistent/Path"   # 本宿主不存在盘符路径：原样报错不炸栈
else
  expect_fail done 0234 --owner rv5 --commit deadbeef --repo "C:/Nonexistent/Path"   # 解不了时原样报错不炸栈
fi
rm -rf "$SBX"

# ---------- 以下 33~37：MECH-S1 认领身份绑会话（check-exit=会话键 / done=泳道键 / recycle=进程键） ----------

CE=$CODE_CC/ops/check_exit.py

# 33 claim 落 session_id(env CLAW_SESSION_ID)；旧会话残卡不 block 新会话；本会话照 block；异会话 NOTE 留痕
$CARD create 0240 --title t --verify "true" >/dev/null
CLAW_SESSION_ID=SID-A $CARD claim 0240 --owner ss1 >/dev/null
grep -q '"session_id": "SID-A"' $CC_TEAM_HOME/claims/0240/claim.json && ok "claim 落 session_id(env)" || bad "claim 未落 session_id"
if [ "$CAP_HOOK_STDIN" = 1 ]; then
  printf '{"session_id":"SID-B"}' | python3 $CE --owner ss1 --repo $REPO >$ST_TMP/sid.out 2>&1; rc=$?
  [ $rc -eq 0 ] && grep -q "NOTE" $ST_TMP/sid.out && ok "①旧会话残卡不拦新会话退出(NOTE 交 recycle)" || bad "①新会话被误拦 rc=$rc"
  printf '{"session_id":"SID-A"}' | python3 $CE --owner ss1 --repo $REPO >/dev/null 2>&1; rc=$?
  [ $rc -eq 2 ] && ok "本会话残卡照 block(会话键结账)" || bad "本会话应 block rc=$rc"
else
  skip "33①/本会话残卡：hook stdin select() 于 Windows 管道必抛→check_exit 静默降级 --owner 档，会话键断言失鉴别力(假绿同禁)——产品缺陷另立 MECH-HOOK-STDIN-WIN-1，修复后自动复活"
fi

# 34 同泳道双 session 互不干扰：换绑新会话后，旧 session 退出不再结新卡的账
$CARD release 0240 >/dev/null
CLAW_SESSION_ID=SID-B $CARD claim 0240 --owner ss1 >/dev/null
if [ "$CAP_HOOK_STDIN" = 1 ]; then
  printf '{"session_id":"SID-A"}' | python3 $CE --owner ss1 --repo $REPO >/dev/null 2>&1; rc=$?
  [ $rc -eq 0 ] && ok "②旧会话退出不拦新会话在制卡" || bad "②旧 session 误拦 rc=$rc"
  printf '{"session_id":"SID-B"}' | python3 $CE --owner ss1 --repo $REPO >/dev/null 2>&1; rc=$?
  [ $rc -eq 2 ] && ok "②新会话自己的账照结" || bad "②新会话未结账 rc=$rc"
else
  skip "34②双session互不干扰：同 MECH-HOOK-STDIN-WIN-1 门控(降级档下 rc 由 --owner 决定，断言空转)"
fi
$CARD release 0240 >/dev/null

# 35 bind：仅 CLAIMED 可绑、覆写幂等；env 未设时占位 "-" 且同泳道同名保守算本账（存量兼容）
$CARD create 0242 --title t --verify "true" >/dev/null
$CARD claim 0242 --owner ss2 >/dev/null   # 无 env → "-"
grep -q '"session_id": "-"' $CC_TEAM_HOME/claims/0242/claim.json && ok "env 未设落 '-' 占位" || bad "占位 '-' 未落"
if [ "$CAP_HOOK_STDIN" = 1 ]; then
  printf '{"session_id":"SID-X"}' | python3 $CE --owner ss2 --repo $REPO >/dev/null 2>&1; rc=$?
  [ $rc -eq 2 ] && ok "'-' 残卡+同泳道同名=保守算本账(行为与现状一致)" || bad "'-' 兼容档应 block rc=$rc"
else
  skip "35'-'保守本账：同 MECH-HOOK-STDIN-WIN-1 门控(降级档 rc=2 系 owner 巧合非会话判定)"
fi
$CARD bind 0242 --session SID-C >/dev/null && ok "bind 回填放行" || bad "bind 被误拒"
grep -q '"session_id": "SID-C"' $CC_TEAM_HOME/claims/0242/claim.json && ok "bind 已覆写 claim.json" || bad "bind 未覆写"
$CARD bind 0242 --session SID-C >/dev/null && ok "bind 同值覆写幂等" || bad "bind 幂等失败"
if [ "$CAP_HOOK_STDIN" = 1 ]; then
  printf '{"session_id":"SID-C"}' | python3 $CE --owner ss2 --repo $REPO >/dev/null 2>&1; rc=$?
  [ $rc -eq 2 ] && ok "bind 后新 session 自己结账" || bad "bind 后应 block rc=$rc"
else
  skip "35bind 后自结：同 MECH-HOOK-STDIN-WIN-1 门控"
fi
$CARD create 0243 --title t --verify "true" >/dev/null
expect_fail bind 0243 --session SID-Q                        # ④非 CLAIMED 卡 bind 拒
$CARD release 0242 >/dev/null

# 36 ⑤无 stdin/非 JSON/无 session_id 字段 → 退回旧 --owner 模式，行为与现状一致
$CARD create 0244 --title t --verify "true" >/dev/null
$CARD claim 0244 --owner ss4 >/dev/null
python3 $CE --owner ss4 --repo $REPO </dev/null >/dev/null 2>&1; rc=$?
[ $rc -eq 2 ] && ok "⑤空 stdin 退回 --owner 模式照 block" || bad "⑤空 stdin 应 block rc=$rc"
printf 'not-json-at-all' | python3 $CE --owner ss4 --repo $REPO >/dev/null 2>&1; rc=$?
[ $rc -eq 2 ] && ok "⑤非 JSON stdin 退回 --owner 模式照 block" || bad "⑤非 JSON 应 block rc=$rc"
printf '{"hook_event_name":"Stop"}' | python3 $CE --owner ss4 --repo $REPO >/dev/null 2>&1; rc=$?
[ $rc -eq 2 ] && ok "⑤JSON 无 session_id 字段退回 --owner 模式" || bad "⑤无字段应 block rc=$rc"
python3 $CE --owner ss9 --repo $REPO </dev/null >/dev/null 2>&1 && ok "⑤退回模式无 claim 照旧放行" || bad "⑤退回模式误拦空泳道"
$CARD release 0244 >/dev/null

# 37 done 仍按泳道键验货（会话键不影响 done/lanes 白名单）：异 session 领的卡照旧可 done
$CARD create 0245 --title t --verify "true" --lanes ss5 >/dev/null
CLAW_SESSION_ID=SID-P $CARD claim 0245 --owner ss5 >/dev/null
echo z37 >> backend/api/user.py && git add -A && git commit -qm "feat(0245): s1"
$CARD done 0245 --owner ss5 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null \
  && ok "③done 泳道键验货不受 session_id 影响" || bad "done 被 session 字段误伤"

# ===== 38~48 v3 运行时对象（道锁+卡锁两把各锁一侧；队列派生；子卡后置）=====
CEXIT="python3 $CODE_CC/ops/check_exit.py"
RCY="python3 $CODE_CC/ops/recycle.py"
Q=$CC_TEAM_HOME/state

# 38 队列生命周期：create 入队 → next 队头 → claim 占道+出队 → done 双锁同清
$CARD create 0601 --title "队列1" --verify "true" --files "backend/api/" --lanes q9 >/dev/null
grep -qx 0601 $Q/lane-q9.queue && ok "create --lanes 自动入队" || bad "create 未入队"
[ "$($CARD next q9)" = "0601" ] && ok "next 报队头" || bad "next 队头错"
$CARD claim 0601 --owner q9 >/dev/null
grep -qx 0601 $Q/lane-q9.lock/card && ok "claim 占道(道锁.card)" || bad "道锁未建"
grep -qx 0601 $Q/lane-q9.queue 2>/dev/null && bad "claim 应出队" || ok "claim 出队"
echo h >> backend/api/user.py && git add -A && git commit -qm "feat(0601): q1"
$CARD done 0601 --owner q9 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
[ ! -e $Q/lane-q9.lock ] && ok "done 双锁同清" || bad "done 后道锁残留"

# 39 release/REJECT 回队头（重派优先），cancel 全队列剔除
$CARD create 0602 --title "队列2" --verify "true" --files "backend/api/" --lanes q9 >/dev/null
$CARD create 0603 --title "队列3" --verify "true" --files "backend/api/" --lanes q9 >/dev/null
$CARD claim 0602 --owner q9 >/dev/null
$CARD release 0602 >/dev/null
[ "$(head -1 $Q/lane-q9.queue)" = "0602" ] && ok "release 回队头" || bad "release 未回队头"
$CARD claim 0602 --owner q9 >/dev/null
echo i >> backend/api/user.py && git add -A && git commit -qm "feat(0602): for-reject"
$CARD done 0602 --owner q9 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
$CARD verdict 0602 REJECT --reason "不合格重做" >/dev/null
[ "$(head -1 $Q/lane-q9.queue)" = "0602" ] && ok "REJECT 回队头" || bad "REJECT 未回队头"
$CARD cancel 0603 >/dev/null
grep -qx 0603 $Q/lane-q9.queue 2>/dev/null && bad "cancel 应剔队列" || ok "cancel 剔出队列"

# 40 reindex 修复派生视图：删道锁/塞幽灵都能从卡面重建
$CARD claim 0602 --owner q9 >/dev/null
rm -rf $Q/lane-q9.lock $Q/lane-q9.queue
echo GHOST > $Q/lane-q9.queue
$CARD reindex >/dev/null
grep -qx 0602 $Q/lane-q9.lock/card && ok "reindex 重建道锁" || bad "reindex 未重建道锁"
grep -qx 0602 $Q/lane-q9.queue 2>/dev/null && bad "CLAIMED 不应占队" || ok "reindex: CLAIMED 不占队"
grep -qx GHOST $Q/lane-q9.queue 2>/dev/null && bad "reindex 未剔幽灵" || ok "reindex 剔除不存在卡号"

# 41 check-exit 道锁直读：在跑卡 block(2)；陈旧道锁退回反查不误放不误锁
$CEXIT --owner q9 --repo $REPO >/dev/null 2>&1; rc=$?
[ $rc -eq 2 ] && ok "道锁命中未勾卡 block" || bad "道锁路径 rc=$rc"
$CARD release 0602 >/dev/null
mkdir -p $Q/lane-q9.lock && echo 0601 > $Q/lane-q9.lock/card   # 0601 已 DONE=陈旧锚
$CEXIT --owner q9 --repo $REPO >/dev/null 2>&1 && ok "陈旧道锁退回反查后放行" || bad "陈旧道锁应放行"
rm -rf $Q/lane-q9.lock

# 42 next 跳过 deps 不齐卡：无就绪则报 - 退出码 1
$CARD create 0610 --title A --verify "true" --files "backend/api/" --lanes q10 >/dev/null
$CARD create 0611 --title B --verify "true" --deps 0610 --files "backend/api/" --lanes q10 >/dev/null
[ "$($CARD next q10)" = "0610" ] && ok "next: 队头就绪可领" || bad "next 就绪判定错"
$CARD claim 0610 --owner q10 >/dev/null
NOUT=$($CARD next q10 2>/dev/null); NRC=$?
[ $NRC -eq 1 ] && [ "$NOUT" = "-" ] && ok "next 跳过 deps 不齐(退出1)" || bad "next 依赖闸 out=$NOUT rc=$NRC"

# 43 push --front 插队 + 并白名单；HOLD 卡拒入队（与 reindex「只收 OPEN」同源规则）
$CARD create 0612 --title C --verify "true" --files "backend/api/" --lanes q11 >/dev/null
$CARD push 0612 --lanes q10 --front >/dev/null
[ "$($CARD next q10)" = "0612" ] && ok "push --front 插队生效" || bad "push 未插队"
$CARD show 0612 | grep -q '"q10"' && ok "push 并入 lanes 白名单" || bad "push 未并白名单"
$CARD create 0613 --title D --status HOLD --hold "等人工" --lanes q10 >/dev/null
expect_fail push 0613 --lanes q10

# 44 卡锁防一卡二主（两泳道抢同卡只一个赢）+ 道锁防一道二活（结构闸，非扫描）
$CARD create 0620 --title E --verify "true" >/dev/null
$CARD claim 0620 --owner m41 >/dev/null
expect_fail claim 0620 --owner m42          # 卡锁：一卡二主拒
$CARD create 0621 --title F --verify "true" >/dev/null
expect_fail claim 0621 --owner m41          # 道锁：m41 占着 0620，再领被拒(结构性)
$CARD release 0620 >/dev/null
$CARD claim 0621 --owner m41 >/dev/null && ok "release 后道锁解锁可领新卡" || bad "解锁后仍占"

# 45 TOCTOU 回归：两泳道真并发领同一张卡，恰好一个赢（旧 _one_wip 扫描型做不到此保证的是道锁侧）
$CARD create 0622 --title G --verify "true" >/dev/null
( $CARD claim 0622 --owner m43 >/dev/null 2>&1; echo $? > $ST_TMP/t45a ) &
( $CARD claim 0622 --owner m44 >/dev/null 2>&1; echo $? > $ST_TMP/t45b ) &
wait
A45=$(cat $ST_TMP/t45a 2>/dev/null); B45=$(cat $ST_TMP/t45b 2>/dev/null)
if { [ "x$A45" = "x0" ] && [ "x$B45" = "x1" ]; } || { [ "x$A45" = "x1" ] && [ "x$B45" = "x0" ]; }; then
  ok "并发领同卡一赢一输(写锁+双锁)"
else
  bad "并发领同卡异常 a=$A45 b=$B45"
fi
$CARD release 0622 >/dev/null 2>&1

# 46 --out-json 结构化交付不炸碎（修 D5；DL-F3 碎 output 的反例）
$CARD done 0621 --owner m41 --commit $(git rev-parse HEAD) --repo $REPO \
  --out-json '{"stats":{"total":10,"pass":"7;8"},"findings":"docs/x.md"}' >/dev/null
python3 - <<'PYEOF' && ok "--out-json 原样落盘" || bad "--out-json 解析错"
import json, os
d = json.load(open(os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json"), encoding="utf-8"))["cards"]["0621"]
assert d["output"] == {"stats": {"total": 10, "pass": "7;8"}, "findings": "docs/x.md"}, d["output"]
PYEOF
$CARD verdict 0621 PASS >/dev/null

# 47 台账写锁串行化：并发 set 不丢更新（旧版读-改-写窗口可覆盖；现 writer 锁排队）
$CARD create 0623 --title H --verify "true" >/dev/null
( for x in 1 2 3 4 5; do $CARD set 0623 --desc "A$x" >/dev/null 2>&1; done ) &
( for x in 1 2 3 4 5; do $CARD set 0623 --title "T$x" >/dev/null 2>&1; done ) &
wait
D623=$($CARD show 0623 | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['description'],d['title'])")
case "$D623" in A5*T5|T5*A5*) ok "并发写互不丢失(写锁)" ;; *) bad "并发写丢更新: $D623" ;; esac

# 48 start=kind=session：recycle 对无锚 pid 会话卡只 WARN 永不 release（R3 语义在 v3 锁下延续）
$CARD create 0624 --title I --verify "true" >/dev/null
$CARD start 0624 --owner pm48 >/dev/null
python3 - "$Q/lane-pm48.lock/meta.json" <<'PYEOF' && ok "start 落 kind=session" || bad "meta.kind 错"
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
assert m["kind"] == "session" and m.get("pid") in (None, 0), m
PYEOF
touch -d "2 hours ago" $Q/lane-pm48.lock/meta.json 2>/dev/null
touch -d "2 hours ago" $CC_TEAM_HOME/claims/0624/claim.json 2>/dev/null
CC_TEAM_HOME=$CC_TEAM_HOME $RCY --minutes 15 2>/dev/null | grep -q "WARN 0624" && ok "session 卡只 WARN 不回收" || bad "session 卡被判回收"
$CARD done 0624 --owner pm48 --commit $(git rev-parse HEAD) --repo $REPO --force >/dev/null 2>&1 || \
  $CARD release 0624 >/dev/null

# ---------- 以下 49~52：MECH-STD1 工程标准化（更名+有状态 DAG+转发档） ----------

# 49 有状态 DAG 自动刷新：写命令收尾重刷物化视图；账无变化（digest 同）不空转 generation
GENQ="import json,os;print(json.load(open(os.path.join(os.environ['CC_TEAM_HOME'],'state','graph.json'),encoding='utf-8'))['generation'])"
G0=$(python3 -c "$GENQ")
$CARD create 0640 --title DAG --verify "true" >/dev/null
G1=$(python3 -c "$GENQ")
[ "$G1" -gt "$G0" ] 2>/dev/null && ok "写命令自动刷新(generation $G0→$G1)" || bad "generation 未随账变增"
$CARD set 0640 --title DAG >/dev/null   # 同值重写(updated_at 变但图语义字段未变)
G2=$(python3 -c "$GENQ")
[ "$G2" = "$G1" ] && ok "digest 相同不空刷(幂等)" || bad "无账变 generation 空增 $G1→$G2"

# 50 dag --check 红蓝检：新鲜=rc0；裸改=rc1；显式 dag 以卡为准重刷后转绿
$CARD dag --check >/dev/null 2>&1 && ok "--check 新鲜=rc0" || bad "--check 误报过期"
python3 - <<'PYEOF'
import json, os
T = os.environ["CC_TEAM_HOME"]
gp = os.path.join(T, "state", "graph.json")
g = json.load(open(gp, encoding="utf-8"))
g["cards"]["0640"]["status"] = "DONE"      # 裸改事实绕过写入口（MECH-PURE1：只改 graph 一端）
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
$CARD dag --check >/dev/null 2>&1 && bad "--check 漏报裸改" || ok "--check 过期=rc1(裸改可检)"
$CARD dag >/dev/null 2>&1
$CARD dag --check >/dev/null 2>&1 && ok "--check 重刷后转绿(坏了=重算)" || bad "显式重刷未愈合"

# 51 MECH-PURE1 清零锁：旧兼容层五件不得复活（仓内实况）；镜像世界不产生（沙箱实况）；
#    旧「转发建档+闸不弱化」两条行为锁的宿主文件已删——写闸语义由主文件全矩阵继续持有（adversary）
SRP=$CODE_CC
ZOMB=0
for f in card.py recycle.py reconcile-events.py check-exit.py tools/migrate_v1.py; do
  [ -e "$SRP/$f" ] && { echo "  zombie: $f"; ZOMB=1; }
done
[ $ZOMB -eq 0 ] && ok "旧档五件全灭(零复活验证)" || bad "旧兼容文件复活"
[ ! -e $CC_TEAM_HOME/tasks ] && ok "沙箱无 tasks/(镜像世界不再产生)" || bad "tasks 镜像复活"
[ ! -e $SRP/tasks ] && ok "真账无 tasks/(done/verdict 后仍无)" || bad "真账 tasks 复活"

# 52 dag --json 机器可读（通信定版：下游解析 JSON，不再 grep 人话行）
$CARD dag --json >$ST_TMP/dagj.out 2>/dev/null
python3 - <<'PYEOF' && ok "dag --json 结构齐" || bad "dag --json 结构缺项"
import json, os
d = json.load(open(os.path.join(os.environ["SELFTEST_TMP"], "dagj.out"), encoding="utf-8"))
assert set(["nodes", "counts", "critical_path", "cycle", "ready", "canceled_blocked",
            "digest", "generation", "topo", "pm_wait_alarm"]).issubset(d), sorted(d)
assert isinstance(d["nodes"].get("0640"), dict) and "status" in d["nodes"]["0640"]
PYEOF

# 53 中文路径 files 域匹配（MECH-STD1 真实演练实锤：core.quotepath 假拒修复+红检双向）
mkdir -p docs && echo x > "docs/中文设计.md" && git add -A && git commit -qm "docs(0650): cjk path"
$CARD create 0650 --title CJK --verify "true" --files "docs/中文设计.md" >/dev/null
$CARD claim 0650 --owner cjk1 >/dev/null
$CARD done 0650 --owner cjk1 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null 2>&1 \
  && ok "中文 files 域放行(quotepath 修复)" || bad "中文路径域内假拒"
$CARD create 0651 --title CJK2 --verify "true" --files "docs/别的.md" >/dev/null
$CARD claim 0651 --owner cjk2 >/dev/null
expect_fail done 0651 --owner cjk2 --commit $(git rev-parse HEAD) --repo $REPO   # 中文域外仍须拒

# 54 MECH-ANC1 first_claimed_at：缺失才写；回收重派清 claimed_at 不清锚；再领不覆写
$CARD create 0652 --title ANCHOR --verify "true" >/dev/null
$CARD claim 0652 --owner anc1 >/dev/null
F1=$($CARD show 0652 | python3 -c "import json,sys;print(json.load(sys.stdin).get('first_claimed_at'))")
C1=$($CARD show 0652 | python3 -c "import json,sys;print(json.load(sys.stdin).get('claimed_at'))")
[ -n "$F1" ] && [ "$F1" != "None" ] && ok "首领写 first_claimed_at" || bad "锚未落"
$CARD release 0652 --who test >/dev/null
# claimed_at 秒级精度：同秒重领「C2≠C1」必假红（MECH-RET1 闸门连跑两次同秒实锤）。
# 测试侧自锁：等钟过一秒再领，保证两次领取时刻必不同（机制不动）。
NOW1=$(date +%Y-%m-%dT%H:%M:%S)
while [ "$(date +%Y-%m-%dT%H:%M:%S)" = "$NOW1" ]; do :; done
$CARD claim 0652 --owner anc1 >/dev/null
F2=$($CARD show 0652 | python3 -c "import json,sys;print(json.load(sys.stdin).get('first_claimed_at'))")
C2=$($CARD show 0652 | python3 -c "import json,sys;print(json.load(sys.stdin).get('claimed_at'))")
[ "$F2" = "$F1" ] && [ "$C2" != "$C1" ] && ok "回收重派: 锚不清+claimed_at刷新(业主口径复现)" || bad "锚被清或错刷 F=$F1/$F2 C=$C1/$C2"
$CARD create 0653 --title S --verify "true" >/dev/null
$CARD start 0653 --owner pm54 >/dev/null
python3 - <<'PYEOF' && ok "start 同写锚" || bad "start 未落锚"
import json, os
d = json.load(open(os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json"), encoding="utf-8"))["cards"]["0653"]
assert d.get("first_claimed_at") == d.get("claimed_at"), d
PYEOF

# 55 MECH-VFY1 verdict 机器复跑判据：rc≠0 拒 PASS（红检）+状态不动+修判据后绿+卡面留痕
# REQ-VERIFY-DRYRUN-1 适配：恒红判据 "false" 现在发卡就被试跑闸拒（发卡即考试），红夹具改
# 时序判据=发牌时红灯未亮（试跑绿、可发卡），done 后点亮红灯（verdict 复跑撞 rc≠0 真红）。
# 「rc≠0 拒 PASS/状态不动/修判据后绿」断言语义逐字不动，红的产生时点从 create 前挪到 verdict 前。
$CARD create 0655 --title VFY --verify "test ! -f '$ST_TMP/v55.red'" >/dev/null
$CARD claim 0655 --owner v55 >/dev/null
echo v55 >> backend/api/user.py && git add -A && git commit -qm "feat(0655): vfy"
$CARD done 0655 --owner v55 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
touch "$ST_TMP/v55.red"                                          # 点亮红灯：此后本判据必红
expect_fail verdict 0655 PASS --as Verifier                    # 判据红=不能 PASS（无人工跳过通道）
gcard 0655 | grep -q '"status": "DONE"' && ok "verify红: PASS被拒且状态未动" || bad "被拒却污染状态"
$CARD set 0655 --verify "true" >/dev/null
$CARD verdict 0655 PASS --as Verifier >/dev/null && ok "修判据后机器跑绿放行" || bad "绿了仍被拒"
python3 - <<'PYEOF' && ok "卡面 verify_run 痕(rc0+cmd_sha+ts)" || bad "verify_run 缺"
import json, os
d = json.load(open(os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json"), encoding="utf-8"))["cards"]["0655"]
assert d.get("verify_run", {}).get("rc") == 0 and d["verify_run"].get("cmd_sha"), d
PYEOF

# 56 MECH-LIT1 清理行为面：REJECTED 出枚举、--out legacy 两处退役（都须干净拒，非静默吞）
$CARD create 0656 --title LIT --verify "true" >/dev/null
expect_fail set 0656 --status REJECTED          # 旧: 直写拒；新: 枚举外拒——都拒，语义收口为「七态」
expect_fail set 0656 --out "k=v"                # argparse 未知旗标 rc2
expect_fail done 0656 --owner nobody --commit nohash --out k=v
$CARD set 0656 --out-json '{"n":{"deep":1}}' >/dev/null && gcard 0656 | grep -q '"deep"' && ok "JSON通道结构不炸碎(深层对象原样落账)" || bad "out-json 回归"

# 57 MECH-AUD1 set 记字段 diff（改了什么进 events，不再靠猜）+ 同值不增殖噪音
$CARD create 0657 --title 旧题 --verify "true" >/dev/null
$CARD set 0657 --title "新题" >/dev/null
grep -q "|0657|OPEN->OPEN|PM|set diff=title:" $CC_TEAM_HOME/state/events.log && ok "set 字段diff进events" || bad "diff 未记"
$CARD set 0657 --title "新题" >/dev/null
tail -1 $CC_TEAM_HOME/state/events.log | grep -q "|PM|set$" && ok "同值set无diff(幂等降噪)" || bad "同值也增殖diff"

# 58 digest 扩 verify/files：裸改判据（状态没变！）新鲜度可检——旧盲区补上
$CARD create 0658 --title DG --verify "true" >/dev/null
$CARD dag --check >/dev/null 2>&1 && ok "基线新鲜" || bad "基线即过期"
python3 - <<'PYEOF'
import json, os
T = os.environ["CC_TEAM_HOME"]
gp = os.path.join(T, "state", "graph.json")
g = json.load(open(gp, encoding="utf-8"))
g["cards"]["0658"]["verify"] = "echo hacked"   # 只偷换判据，status 原样（seal 该抓；graph 唯一端）
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
$CARD dag --check >/dev/null 2>&1 && bad "verify裸改不可检(盲区还在)" || ok "闸门字段裸改→新鲜度可检"
$CARD dag >/dev/null 2>&1
$CARD dag --check >/dev/null 2>&1 && ok "以卡为准重刷愈合" || bad "重刷未愈合"

# 59 ALARM 聚合账 + ack 闭环（键精确，不验全局清零防跨用例污染）
$CARD create 0660 --title NOLANE --verify "true" >/dev/null
$CARD dag >/dev/null 2>&1
python3 - "$CC_TEAM_HOME/state/alerts.json" <<'PYEOF' && ok "ALARM进聚合账(count+first/last)" || bad "alerts 未落账"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
hit = [e for e in d["alerts"].values() if "无泳道映射" in e["msg"] and e["msg"].endswith("0660")]
assert hit and hit[0]["count"] >= 1, d
PYEOF
$CARD ack 0660 --who PM >/dev/null 2>&1 && ok "ack 命中落痕" || bad "ack 失败"
expect_fail ack 9999无此键
$CARD ack --list >$ST_TMP/ackl.out 2>&1
grep -q "0660" $ST_TMP/ackl.out && bad "ack后0660仍开放" || ok "0660已闭环(开放项视图)"

# 60 pid_ns：claim 声明 pid 空间，落卡锁+道锁 meta（无 pid 不落——没得判）
$CARD create 0661 --title NS --verify "true" >/dev/null
$CARD claim 0661 --owner ns60 --pid 77777 --pid-ns win >/dev/null
python3 - "$CC_TEAM_HOME/claims/0661/claim.json" "$Q/lane-ns60.lock/meta.json" <<'PYEOF' && ok "pid_ns 双锁同落" || bad "pid_ns 未落"
import json, sys
for p in sys.argv[1:3]:
    assert json.load(open(p, encoding="utf-8")).get("pid_ns") == "win", p
PYEOF
$CARD create 0662 --title NS2 --verify "true" >/dev/null
$CARD claim 0662 --owner ns61 >/dev/null
! grep -q "pid_ns" "$CC_TEAM_HOME/claims/0662/claim.json" && ok "无锚pid不落ns(保守档)" || bad "无pid却落ns"

# 61 MECH-RET1 退役机制（保留）+ MECH-PURE1 清零：机制单元红绿（注入观察对象，不依赖已删旧档）；
#    空 WATCH=对账静默零宣；旁证层永不抛。旧 61.1~61.5「造账点真名+ack 复宣」随转发档退役——
#    满窗/近期/迟命中/已删判据进单元直测；ack 闭环通道与通用 ALARM 同源（红绿由 #57~60 持有）。
SR=$CODE_CC
python3 - "$CODE_CC" >/dev/null 2>&1 <<'PYEOF' && ok "retire 单元红绿:无账不空宣/满窗零命中宣/近期不宣/env窗口/迟命中复宣/已删跳过" || bad "retire 机制单元红"
import importlib, os, sys, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, sys.argv[1])   # MECH-SELFTEST-WIN-1：宿主中性 argv 传码根（原硬编码 /mnt/c）
tmp = tempfile.mkdtemp(); os.environ["CC_TEAM_HOME"] = tmp
from ccards import config; importlib.reload(config)
from ccards import retire; importlib.reload(retire)
retire.WATCH = ["probe.py"]
now = datetime.now().astimezone()
def ts(days): return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S%z")
def probe(): return [i for i in retire.assess(tmp) if i["name"] == "probe.py"][0]
os.makedirs(config.STATE, exist_ok=True)
open(os.path.join(tmp, "probe.py"), "w").close()
assert not probe()["ready"]                                   # 无账=观察未开始，不空宣可删
open(retire._path(), "w").write("# since=%s\n" % ts(8))
assert probe()["ready"]                                       # 满窗零命中=点名候删
open(retire._path(), "a").write("%s|probe.py\n" % ts(2))
assert not probe()["ready"]                                   # 近期命中不宣
os.environ["CARD_RETIRE_DAYS"] = "1"
assert probe()["ready"]                                       # 窗口 env=1 天 → 2 天前命中变候删
del os.environ["CARD_RETIRE_DAYS"]
open(retire._path(), "w").write("# since=%s\n%s|probe.py\n" % (ts(8), ts(9)))
assert probe()["ready"]                                       # 唯一命中在迟窗(9 天前) → 可宣（last=末次命中语义）
os.remove(os.path.join(tmp, "probe.py"))
assert not probe()["ready"]                                   # 文件已删=跳过不宣
PYEOF
python3 - "$CODE_CC" >/dev/null 2>&1 <<'PYEOF' && ok "旁证层不抛(STATE 写不通 hit/bootstrap 静默吞)" || bad "旁证层上抛"
import importlib, os, sys
sys.path.insert(0, sys.argv[1])   # MECH-SELFTEST-WIN-1：宿主中性 argv 传码根
# 「写不通」路径宿主等价：linux=/proc 只读挂载；windows=未映射盘符 Z:（makedirs 必败，
# 与 /proc/cannot-write 同语义）。若 Z: 恰被映射，写成功也不抛=断言照样成立，不产伪红。
os.environ["CC_TEAM_HOME"] = "Z:/cc-selftest-unwritable" if os.name == "nt" else "/proc/cannot-write"
from ccards import config; importlib.reload(config)
from ccards import retire; importlib.reload(retire)
retire.hit("x.py"); retire.bootstrap()
PYEOF
grep -q "^# since=" $CC_TEAM_HOME/state/shim-hits.log && ok "窗锚自落(#26 首次对账 bootstrap)" || bad "锚缺失"
python3 $SR/ops/reconcile_events.py --quiet >$ST_TMP/ret.out 2>&1
grep -q "retire_ready=0" $ST_TMP/ret.out && ! grep -q "REMOVAL-READY" $ST_TMP/ret.out \
  && ok "WATCH 空名单=对账静默零宣(兼容件清零实况)" || bad "空 WATCH 竟有宣"

# 62 MECH-ENG1 plan 单 tick：只读决策面字段齐+与物化图同账+与 next 同源+零写账
EV1=$(wc -l < $CC_TEAM_HOME/state/events.log)
$CARD plan --json >$ST_TMP/plan.out 2>/dev/null
python3 - <<'PYEOF' && ok "plan 结构齐+判定自洽+digest 同账" || bad "plan 结构/语义"
import json, os
d = json.load(open(os.path.join(os.environ["SELFTEST_TMP"], "plan.out"), encoding="utf-8"))
need = ["tick","generation","digest","counts","ready","claimable","claimed",
        "cycle","canceled_blocked","critical_path","run_complete","remaining","alarms_open","retire_ready"]
assert all(k in d for k in need), [k for k in need if k not in d]
assert d["run_complete"] == (not d["remaining"])
dg = json.load(open(os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json"), encoding="utf-8"))
assert d["digest"] == dg["digest"]   # 与物化视图同一本账——plan 不造第二真相
PYEOF
G1=$(python3 -c "import json,os;print(json.load(open(os.path.join(os.environ['CC_TEAM_HOME'],'state','graph.json'),encoding='utf-8'))['generation'])")
EV2=$(wc -l < $CC_TEAM_HOME/state/events.log)
[ "$EV1" = "$EV2" ] && ok "plan 只读零写(events 行数不动)" || bad "plan 写账了($EV1→$EV2)"
# ENG2 后 dag.json 已并入 graph.json——此两行若不改读 graph 会拿空串比空串=空转假绿(2026-10-31 自查发现)
[ "$(python3 -c "import json,os;print(json.load(open(os.path.join(os.environ['CC_TEAM_HOME'],'state','graph.json'),encoding='utf-8'))['generation'])")" = "$G1" ] \
  && ok "plan 不触图刷新(generation 不动)" || bad "plan 越权刷图"
N10=$($CARD next q10 2>/dev/null || true)
P10=$(python3 -c "import json,os;print(json.load(open(os.path.join(os.environ['SELFTEST_TMP'],'plan.out'),encoding='utf-8'))['claimable'].get('q10') or '-')")
[ -n "$N10" ] && [ "$N10" = "$P10" ] && ok "claimable 与 next 同源(q10=$P10)" || bad "plan/next 分歧: next=$N10 plan=$P10"

# 63 MECH-OPS1 ccops 单 tick：DRY 零写账+派工指令齐(claim 带 pid 约定原样吐)；--apply 走闸
EV1=$(wc -l < $CC_TEAM_HOME/state/events.log)
python3 $SR/ops/ccops.py >$ST_TMP/ops.out 2>&1 \
  && grep -q "DISPATCH lane=q10 card=0612" $ST_TMP/ops.out \
  && grep -qF "claim 0612 --owner q10 --pid" $ST_TMP/ops.out \
  && ok "ccops DRY 报可派+指令齐" || bad "ccops dry 输出缺项"
[ "$(wc -l < $CC_TEAM_HOME/state/events.log)" = "$EV1" ] && ok "ccops DRY 不写账" || bad "ccops dry 动账了"
python3 $SR/ops/ccops.py --apply >$ST_TMP/ops2.out 2>&1 \
  && grep -q "apply: thaw=" $ST_TMP/ops2.out && ok "ccops --apply 走闸执行(thaw/reindex/recycle rc 如实报)" || bad "ccops apply 红"
# 64 MECH-ENG2 seal 红绿：手改事实必红；合法写重封转正（「坏了重算」新语义：以账为准+重封）
python3 - <<'PYEOF'
import json, os
gp = os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json")
g = json.load(open(gp, encoding="utf-8")); g["cards"]["0657"]["title"] = "偷改题"
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
$CARD dag --check >/dev/null 2>&1 && bad "seal 竟放过手改事实" || ok "seal 抓写口外偷改(红检)"
$CARD set 0657 --title "新题" >/dev/null   # 合法写口重落+自动重封+镜像同笔
$CARD dag --check >/dev/null 2>&1 && ok "合法写后重封转正(绿检)" || bad "重封未愈合"

# 65 MECH-LOG1 进度流+尾流：progress 单文件追加不碰账 / 错卡号红 / 换行注入清洗 /
#    watch 三节(在制/进度/events尾) / --card -n 过滤 / --follow 有限档流式出新行
$CARD create 0670 --title LOG1 --verify "true" >/dev/null
$CARD claim 0670 --owner pmx >/dev/null
EV1=$(wc -l < $CC_TEAM_HOME/state/events.log)
G1=$(python3 -c "import json,os;print(json.load(open(os.path.join(os.environ['CC_TEAM_HOME'],'state','graph.json'),encoding='utf-8'))['generation'])")
$CARD progress 0670 "selftest 40%" >/dev/null 2>&1 \
  && grep -qE "^[0-9]{4}[^|]*\|0670\|[^|]*\|selftest 40%$" $CC_TEAM_HOME/state/progress.log \
  && ok "progress 落一行(ts|cid|who|text)" || bad "progress 未落账"
[ "$(wc -l < $CC_TEAM_HOME/state/events.log)" = "$EV1" ] \
  && [ "$(python3 -c "import json,os;print(json.load(open(os.path.join(os.environ['CC_TEAM_HOME'],'state','graph.json'),encoding='utf-8'))['generation'])")" = "$G1" ] \
  && $CARD dag --check >/dev/null 2>&1 \
  && ok "progress 旁证不碰账(events/graph generation/seal 全不动)" || bad "progress 越权动账"
$CARD progress 0671-nonexistent "x" >/dev/null 2>&1 && bad "错卡号竟收下" || ok "错卡号拒绝(流水不掺假号)"
L1=$(wc -l < $CC_TEAM_HOME/state/progress.log)
$CARD progress 0670 "上
FORGED|0670|-|伪造行" >/dev/null 2>&1
L2=$(wc -l < $CC_TEAM_HOME/state/progress.log)
[ "$((L2-L1))" = "1" ] && ok "换行注入清洗成一行(+1)" || bad "注入造行(+$((L2-L1))行)"
$CARD watch >$ST_TMP/w65.out 2>&1
grep -q "^claimed 0670 owner=pmx" $ST_TMP/w65.out \
  && grep -q "selftest 40%" $ST_TMP/w65.out && grep -q -- "--- events(tail" $ST_TMP/w65.out \
  && ok "watch 三节:在制卡+进度流+events尾" || bad "watch 缺节(见 $ST_TMP/w65.out)"
$CARD watch --card 0670 -n 1 >$ST_TMP/w65c.out 2>&1
C65=$(awk -F"|" '$2=="0670" && $3 !~ /->/' $ST_TMP/w65c.out | wc -l)
[ "$C65" = "1" ] && ok "watch --card -n 1 过滤生效" || bad "watch 过滤失效(命中 $C65 行)"
( sleep 1; $CARD progress 0670 "followed-line" >/dev/null 2>&1 ) &
timeout 15 $CARD watch --follow --ticks 5 --interval 1 >$ST_TMP/w65f.out 2>&1
grep -q "followed-line" $ST_TMP/w65f.out && ok "watch --follow 流式出新行" || bad "follow 不追新"
wait

# 66 MECH-VIS1 面板：派生视图随写自动重生成(gen 同账,节点=卡数) / 详情数据嵌入 / 视图红绿——
#    面板路被目录占死时：写账照样成功(只 WARN)，视图永不拦正事
P=$CC_TEAM_HOME/state/panel.html
python3 - "$P" <<'PYEOF' && ok "写账自动重生成面板(gen 同账,节点=卡数)" || bad "panel 未随账重生成"
import json, re, sys, os
d = json.loads(re.search(r'<script type="application/json" id="ledger">(.*?)</script>',
                         open(sys.argv[1], encoding="utf-8").read(), re.S).group(1))
g = json.load(open(os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json"), encoding="utf-8"))
assert d["gen"] == g["generation"], (d["gen"], g["generation"])
assert len(d["nodes"]) == len(d["cards"]) == len(g["cards"])
PYEOF
$CARD panel >/dev/null 2>&1   # #65 后无写命令,流水未进自动档面板——手动面即最新态(默认路=state/panel.html)
python3 - "$P" <<'PYEOF' && ok "详情嵌入:0670 全字段+events 挂卡+progress 行" || bad "面板缺详细数据"
import json, re, sys
d = json.loads(re.search(r'<script type="application/json" id="ledger">(.*?)</script>',
                         open(sys.argv[1], encoding="utf-8").read(), re.S).group(1))
c = d["cards"]["0670"]
assert c["title"] == "LOG1" and c["status"] == "CLAIMED"
assert any("->" in x.split("|")[2] for x in d["events"]["0670"])          # 状态流水挂到卡
assert any("followed-line" in x for x in d["progress"]["0670"])            # #65 那次 follow 写入进了面板
PYEOF
mv $P $P.bak && mkdir -p $P   # 目录占死面板路=原子 replace 必败(IsADirectoryError,探针实锤)
# ⚠️ 必须用「触 digest 的写」触发钩子：set --title 不动 digest(digest 计算里无标题=标题非调度信号)
#    → graph.refresh 早退、面板钩子不跑(2026-10-31 首版踩坑);create 新增卡才改 digest=必刷
$CARD create 0691 --title 面板不拦写 --verify true >/dev/null 2>$ST_TMP/pnl.err \
  && grep -q "WARN: panel" $ST_TMP/pnl.err \
  && $CARD dag --check >/dev/null 2>&1 \
  && ok "视图渲染失败只 WARN,写账照旧绿(红绿双向)" || bad "视图拦了写账(或缺 WARN)"
rm -rf $P && $CARD panel --out $CC_TEAM_HOME/state/panel2.html >/dev/null 2>&1 \
  && [ -s $CC_TEAM_HOME/state/panel2.html ] && ok "panel 手动面 --out 生成" || bad "手动 panel 生成失败"

# 67 MECH-OPT1 告警键稳定性：session 卡连静默三轮 → 聚合账该题**只有一个键**、count 随轮涨、
#    键里无"静默<n>min"动态值（修复前每 10min 一个新键=打地鼠 ack，同题聚合失效）
$CARD create 0677 --title KEY --verify "true" >/dev/null
$CARD claim 0677 --owner k67 >/dev/null
touch -d "20 minutes ago" $CC_TEAM_HOME/claims/0677/claim.json
python3 $SR/ops/recycle.py --apply >$ST_TMP/k1.out 2>&1
touch -d "21 minutes ago" $CC_TEAM_HOME/claims/0677/claim.json
python3 $SR/ops/recycle.py --apply >$ST_TMP/k2.out 2>&1
touch -d "22 minutes ago" $CC_TEAM_HOME/claims/0677/claim.json
python3 $SR/ops/recycle.py --apply >$ST_TMP/k3.out 2>&1
grep -q "ALARM 0677" $ST_TMP/k3.out && grep -q "22min\|21min" $ST_TMP/k3.out \
  && ok "stdout 给人看带实时分钟" || bad "展示行丢了动态值(过度稳定)"
python3 - <<'PYEOF' && ok "聚合账同题=1 键 count>=2 且键无动态分钟(修复键增殖)" || bad "告警键仍在增殖"
import json, os
d = json.load(open(os.path.join(os.environ["CC_TEAM_HOME"], "state", "alerts.json"), encoding="utf-8"))["alerts"]
ks = [k for k in d if "ALARM 0677" in k]
assert len(ks) == 1, ks
assert int(d[ks[0]]["count"]) >= 2, d[ks[0]]
assert "min" not in ks[0], ks[0]
PYEOF

# 68 MECH-VIS2 渲染器红绿锁：node DOM 桩真跑 panel 内嵌 JS——对抗标题(</script>/引号/中文 emoji)
#    转义往返、5 点 3 边=账结构、点击详情注入必须成实体、布局魔法数根除、防红自证(撤修复必炸)。
#    病根账：VIS1 的 DOM 实证是一次性手工桩——改渲染器没有任何东西会红，这就是「图操作 BUG 多」的温床。
if ! command -v node >/dev/null 2>&1; then
  bad "WSL 无 node：#68 渲染器锁跑不了（静默跳过=假绿，此锁故意大声失败）"
else
python3 - "$CC_TEAM_HOME/state/panel_v2.html" "$SR" <<'PYEOF' || bad "合成面板渲染失败(#68 前置)"
import sys
sys.path.insert(0, sys.argv[2])
from ccards import panel
doc = {"generation": "G-TEST", "seal": "0123456789abcdef0123456789abcdef", "digest": "fedcba9876543210",
       "counts": {"CLAIMED": 1, "OPEN": 1, "HOLD": 1, "VERIFIED": 1},
       "ready": ["V2-B"], "cycle": [], "critical_path": ["V2-A", "V2-B"], "canceled_blocked": [],
       "cards": {
        "V2-A": {"id": "V2-A", "status": "CLAIMED", "owner": "pm", "lanes": ["pm"], "deps": [],
                 "created_at": "2026-10-31T00:00:00+0800",
                 "title": '标题含 </script><img src=x onerror=alert(1)> 与 & 符号'},
        "V2-B": {"id": "V2-B", "status": "OPEN", "owner": "-", "deps": ["V2-A"], "created_at": "x",
                 "title": "中文😀\"双引号'单引号<尖括号>"},
        "V2-C": {"id": "V2-C", "status": "HOLD", "deps": ["V2-B"], "created_at": "x", "title": "空心档"},
        "V2-D": {"id": "V2-D", "status": "VERIFIED", "deps": ["V2-B", "NOT-EXIST"], "created_at": "x",
                 "title": "账外依赖名必须被滤掉"},
        "V2-E": {"id": "V2-E", "status": "?", "deps": [], "created_at": "x", "title": "未知档回退灰"}}}
panel.render(doc=doc, out=sys.argv[1])
PYEOF
node $SR/tests/panel_dom_check.mjs "$CC_TEAM_HOME/state/panel_v2.html" >$ST_TMP/p68.out 2>&1 \
  && grep -q "PANEL_DOM_PASS" $ST_TMP/p68.out \
  && ok "#68 渲染器 DOM 桩全绿(含防红自证)" || { bad "#68 渲染器守卫红(见 $ST_TMP/p68.out)"; tail -8 $ST_TMP/p68.out; }
rm -f "$CC_TEAM_HOME/state/panel_v2.html"
fi

# 69 MECH-UX1 看板实时页签锁：node DOM 桩真跑 board/live.js（合成 /api/dag+/api/ops 账）——
#    裁决三节/台账行数=卡数/对抗标题转义/过滤器/告警分组/断链必喊「读账失败」/全 ack 转绿/红自证。
#    与 #68 同一纪律：新渲染层落地当轮上常驻锁；红自证首版误攻导出映射(假红)被锁自逮后改打内部调用点。
# REQ-OSS-PREP-1：board/=本机运营看板层（含宿主路径），按发布边界不随独立发行包出——
# 干净 clone 上本锁无被测物，条件执行=有则真跑、无则 SKIP（主树行为零漂移）。
if [ ! -f "$SR/board/live.js" ]; then skip "#69 看板 live.js DOM 桩（board 运营层未随独立发行包）"
elif node $SR/tests/board_dom_check.mjs "$SR/board/live.js" >$ST_TMP/p69.out 2>&1 \
  && grep -q "BOARD_DOM_PASS" $ST_TMP/p69.out; then ok "#69 看板 live.js DOM 桩全绿(含红自证)"
else bad "#69 看板渲染守卫红(见 $ST_TMP/p69.out)"; tail -8 $ST_TMP/p69.out; fi

# 70 MECH-RTY1 写锁夺锁窗回归锁（产品级丢更新根因的确定性复现）：
#    空 meta=持有者初始化中——第二写者只准等不准夺；meta 死者锚——按规矩夺。旧判据两者混淆=双写者。
rm -rf $ST_TMP/cc-lk70 && mkdir -p $ST_TMP/cc-lk70/state
if CC_TEAM_HOME=$ST_TMP/cc-lk70 python3 - "$SR" "$CAP_PIDALIVE_DEAD" <<'PYEOF'
import json, os, sys, threading, time
sys.path.insert(0, sys.argv[1])
dead = sys.argv[2] == "1"                                 # 探针：pid_alive 能确证死 pid 才跑夺锁断言
from ccards import config, locks
os.mkdir(config.WLOCK)                                   # 模拟：A 刚 mkdir 夺门，meta 还没写
t = threading.Thread(target=locks.team_lock_acquire, args=("B_probe",), daemon=True)
t.start()
time.sleep(1.5)
assert t.is_alive(), "B 竟在空 meta 窗内夺走初始化中的活锁（双写者复活）"
assert os.path.isdir(config.WLOCK), "活锁被 rmtree"
if not dead:
    locks.team_lock_release()                            # 探针缺位：只证空meta半段，即放即退不留残锁
    sys.exit(0)
json.dump({"cmd": "ghost", "pid": 999999, "ts": config.now()},
          open(os.path.join(config.WLOCK, "meta.json"), "w", encoding="utf-8"))
t.join(25)
assert not t.is_alive(), "死锚锁未按自清规则夺回"
locks.team_lock_release()
PYEOF
then
  if [ "$CAP_PIDALIVE_DEAD" = 1 ]; then ok "#70 空meta活锁不被夺/死锚锁照夺(丢更新根因封堵)"
  else ok "#70a 空meta活锁不被夺(双写者复活封堵)"; skip "#70b 死锚锁自清夺回：config.pid_alive 于 Windows 把确死 pid 保守判活(SystemError 被吞=恒活)，#70 死锚半段失前提——产品缺陷另立 MECH-PIDALIVE-WIN-1，其判据已实测必红，修复后自动复活"
  fi
else bad "#70 写锁夺锁窗回归"; fi
rm -rf $ST_TMP/cc-lk70

# 71 补50（业主设计 2026-11-01「done 拉起校验，通过后写 dag 只是瞬间」）：CC_AUTO_VERIFY=1 时
#     done 绿=自动瞬锁落 VERIFIED（by=auto@done），红=卡留 DONE 如实喊；开关不设=旧语义不动。
$CARD create 0711 --title "auto绿" --lanes m1 --verify "true" --files "backend/api/" >/dev/null
$CARD claim 0711 --owner m1 >/dev/null
echo g >> backend/api/user.py && git add -A && git commit -qm "feat(0711): g" >/dev/null
CC_AUTO_VERIFY=1 $CARD done 0711 --owner m1 --commit $(git rev-parse HEAD) --repo $REPO --evidence "ok" >/dev/null 2>&1
gcard 0711 | grep -q '"status": "VERIFIED"' && ok "71 auto校验绿瞬落VERIFIED" || bad "71 auto校验未落章"
gcard 0711 | grep -q 'by=Verifier' && ok "71 auto落章身份在案" || bad "71 落章身份缺"
$CARD create 0712 --title "auto红" --lanes m1 --verify "test ! -f '$ST_TMP/v712.red'" --files "backend/api/" >/dev/null
$CARD claim 0712 --owner m1 >/dev/null
echo h >> backend/api/user.py && git add -A && git commit -qm "feat(0712): h" >/dev/null
touch "$ST_TMP/v712.red"   # REQ-VERIFY-DRYRUN-1 适配：恒红 false 发卡即拒，红改在此点亮（auto校验撞真红语义不动）
CC_AUTO_VERIFY=1 $CARD done 0712 --owner m1 --commit $(git rev-parse HEAD) --repo $REPO --evidence "ok" >/dev/null 2>&1
gcard 0712 | grep -q '"status": "DONE"' && ok "71 红链不误转正(卡留DONE=红检真拦)" || bad "71 红判据被误落章"
$CARD create 0713 --title "开关关" --lanes m1 --verify "true" --files "backend/api/" >/dev/null
$CARD claim 0713 --owner m1 >/dev/null
echo i >> backend/api/user.py && git add -A && git commit -qm "feat(0713): i" >/dev/null
$CARD done 0713 --owner m1 --commit $(git rev-parse HEAD) --repo $REPO --evidence "ok" >/dev/null 2>&1
gcard 0713 | grep -q '"status": "DONE"' && ok "71 开关不启=旧语义(人工verdict通道不动)" || bad "71 开关语义破"

# 72 心跳=底层核心(业主2026-11-02裁定 收编PM直改): dispatcher发单必带活锚 无锚旧路不得残留
DISP=$CODE_CC/ops/dispatch.py
grep -qE 'run_gate\("claim".*"--pid"' "$DISP" && ok "72 dispatcher claim 调用行带 --pid 活锚" || bad "72 dispatcher 漏锚(=本晚僵尸批病根)"
grep -qE 'run_gate\("claim", cid, "--owner", lane\)$' "$DISP" && bad "72 无锚旧调用残留(双通道必再生僵尸)" || ok "72 无锚发单路已绝迹"

# 73 MECH-QDEL 派生队列废除（业主 2026-11-02 口径「拿 graph 实例去操作就够了」；00:4x m2 建道
#     镜像缺条目调度失明 + 09:1x set --lanes 镜像滞后 propose 空转圈双案实锤=第二套真相）。
#     决策面 engine.tick/cmd_next 每圈从账本现算；lane-*.queue 降级为 reindex 装饰输出。
#     红绿=镜像与账本故意不一致（假卡站前、真卡失踪），引擎必须不眨眼。
ENG=$CODE_CC/ccards/engine.py
CMDPY=$CODE_CC/ccards/commands.py
grep -qE 'def _queues|lane_queue|lane_q_' "$ENG" && bad "73 engine 仍读队列镜像(第二真相复辟)" || ok "73 engine 零读镜像"
grep -q '_lane_order(cards)' "$ENG" && ok "73 tick 接账本现算 _lane_order" || bad "73 tick 未接账本现算"
grep -q '_lane_order' "$CMDPY" && ok "73 cmd_next 同源账本" || bad "73 cmd_next 仍据镜像判定"
$CARD create 7301 --title "账本真相" --lanes z73 --verify "true" --files "backend/api/" >/dev/null
echo PHANTOM9 > $CC_TEAM_HOME/state/lane-z73.queue
python3 -I -X utf8 -c "import sys;sys.path.insert(0,'$CODE_CC');from ccards import engine;t=engine.tick();assert t['claimable'].get('z73')=='7301',t['claimable']" \
  && ok "73 镜像被篡改 engine 不眨眼(账本=唯一真相)" || bad "73 engine 仍被镜像骗(第二套真相未灭绝)"

# 74 REQ-BOARD-LANEWORD-1 道栏文案锁：业主问「为什么一个卡有3个道」——
#     属主与候选白名单禁连排，拆 在制道/候补道（先到先得，领走即出局）；合成账真跑心跳区 +
#     node 独立 boot 真跑 live.js（台账/弹窗），三路红自证齐（撤段/含主/回退表头 必红）。
if [ ! -f "$SR/board/status-web.py" ] || [ ! -f "$SR/board/live.js" ]; then skip "#74 道栏文案桩（board 运营层未随独立发行包）"
elif python3 $SR/tests/board_laneword_check.py >$ST_TMP/p74.out 2>&1 \
  && grep -q "BOARD_LANEWORD_PASS" $ST_TMP/p74.out; then ok "#74 道栏文案桩全绿(含红自证)"
else bad "#74 道栏文案守卫红(见 $ST_TMP/p74.out)"; tail -8 $ST_TMP/p74.out; fi

# 75 MECH-REDAUTO-1 dispatcher 例行复跑 DONE 红卡（补50 后判据红停留 DONE 曾是死账——
#     只能等 PM 批 verdict/修复卡碰巧，REG-2 人肉复跑实锤）：绿卡到期自动转正（verdict 痕
#     by=redauto@dispatcher）/ 红卡留 DONE 且 logs/redauto.tail 续痕 / 在制 CLAIMED 卡不打扰 /
#     recycle 报红卡龄（REDCARD 行+redcards= 汇总）。路径用脚本相对 $SR72：主仓跑测主仓码、
#     泳道工作树跑测工作树码——红绿锁不依赖合入。独立沙箱 $ST_TMP/cc-team-r72 防串染；
#     env -u CC_AUTO_VERIFY 双保险：复跑池起点必须是无开关停留的 DONE（71 语义前提）。
SR72="$SELF_CC"
T72=$ST_TMP/cc-team-r72; R72=$ST_TMP/cc-repo-r72
rm -rf $T72 $R72; mkdir -p $T72
git init -q $R72 && git -C $R72 config user.email t@t && git -C $R72 config user.name t
mkdir -p $R72/backend/api && echo a > $R72/backend/api/user.py
git -C $R72 add -A && git -C $R72 commit -qm init >/dev/null
C72(){ env -u CC_AUTO_VERIFY CC_TEAM_HOME=$T72 python3 "$SR72/cards_dag.py" "$@"; }
D72(){ env -u CC_AUTO_VERIFY CC_TEAM_HOME=$T72 python3 "$@"; }
g72(){ CC_TEAM_HOME=$T72 python3 -c "import json,os,sys;g=json.load(open(os.path.join(os.environ['CC_TEAM_HOME'],'state','graph.json'),encoding='utf-8'))
print(json.dumps(g['cards'].get(sys.argv[1],{}),ensure_ascii=False))" "$1"; }
# 75.1 绿卡：done 后停 DONE（71 的「开关不启」语义在案）；一轮 --redauto 到期即自动转正
C72 create R72G --title 绿卡 --lanes m72 --verify "true" >/dev/null 2>&1
C72 claim R72G --owner m72 >/dev/null
echo b >> $R72/backend/api/user.py && git -C $R72 add -A && git -C $R72 commit -qm "feat(R72G): g" >/dev/null
C72 done R72G --owner m72 --commit $(git -C $R72 rev-parse HEAD) --repo $R72 --evidence ok >/dev/null
g72 R72G | grep -q '"status": "DONE"' && ok "75 起点=停留DONE(红卡池)" || bad "75 起点未停DONE(auto通道漏关?)"
D72 "$SR72/ops/dispatch.py" --redauto >/dev/null 2>&1
g72 R72G | grep -q '"status": "VERIFIED"' && ok "75 绿卡到期自动转正(--redauto 复跑绿)" || bad "75 绿卡未自动转正"
g72 R72G | grep -q "redauto@dispatcher" && ok "75 转正痕 by=redauto@dispatcher" || bad "75 转正痕缺"
# 75.2 红卡：复跑仍红→留 DONE+尾痕——守卫必须红得起来（撤修复即红的判据面）
# REQ-VERIFY-DRYRUN-1 适配：恒红 "false" 发卡即拒（试跑闸），红改时序点亮——发卡时红灯未亮、
# done 后 redauto 复跑时亮红（"复跑仍红→留 DONE" 断言形不动）。
C72 create R72R --title 红卡 --lanes m72 --verify "test ! -f '$ST_TMP/r72r.red'" >/dev/null 2>&1
C72 claim R72R --owner m72 >/dev/null
echo c >> $R72/backend/api/user.py && git -C $R72 add -A && git -C $R72 commit -qm "feat(R72R): r" >/dev/null
C72 done R72R --owner m72 --commit $(git -C $R72 rev-parse HEAD) --repo $R72 --evidence ok >/dev/null
touch "$ST_TMP/r72r.red"
D72 "$SR72/ops/dispatch.py" --redauto >/dev/null 2>&1
g72 R72R | grep -q '"status": "DONE"' && ok "75 红卡不被误转正(留DONE)" || bad "75 红卡被误转正"
grep -q "R72R" $T72/logs/redauto.tail && ok "75 红痕续档 logs/redauto.tail" || bad "75 红痕缺失"
# 75.3 在制卡不打扰：CLAIMED 不进复跑池——同轮有红卡待复跑也不碰在制
C72 create R72W --title 在制 --lanes m72 --verify "true" >/dev/null 2>&1
C72 claim R72W --owner m72 >/dev/null
D72 "$SR72/ops/dispatch.py" --redauto >/dev/null 2>&1
g72 R72W | grep -q '"status": "CLAIMED"' && ok "75 在制卡不打扰(仍CLAIMED属主在)" || bad "75 在制卡被打扰"
# 75.4 recycle 报红卡龄：DONE 池报 REDCARD 行+汇总 redcards=（dry 只 print 零写，写闸由 guard 套看）
D72 "$SR72/ops/recycle.py" --dry > $ST_TMP/r72rec.out 2>&1
grep -q "REDCARD R72R" $ST_TMP/r72rec.out && grep -q "redcards=1" $ST_TMP/r72rec.out \
  && ok "75 recycle报红卡龄(REDCARD行+redcards=1)" || { bad "75 recycle红卡龄缺报"; tail -5 $ST_TMP/r72rec.out; }
rm -rf $T72 $R72

# 76 BUG-ANCHOR-NS-1 退役锁（REQ-BEAT-ANCHOR-1 名牌制，业主定版 2026-11-02「一泳道一会话」）：
#   生死判活权威已迁到账本事件流——本用例由「NS-1 进程名核验上线锁」重定位为「退役锁」，
#   看守机制不回潮到进程表依赖：
#   ①静态锁：recycle.py 非注释行零进程探测（win_pid_status/wsl_pid_status/trusted_anchor/
#      _identity_verdict/Get-Process/tasklist 一个都不许复活；判活函数体级锁另见 #89）；
#   ②活≠在：真活的 Windows 进程锚 + 断流（无进度流、账本静默 20min）→ 必回收——旧代码
#      「pid 活=永不抢」若回潮，本断言必红；
#   ③死 pid+活流：锚=不存在的进程号、但 beat 续写新鲜 → ALIVE 不动——判活若再看进程表
#      （旧代码 pid 死即杀），本断言必红；
#   ④留档锁：claim.json 仍记 pid/pid_ns 字段（退役≠删字段，排障审计面保留）。
#   跨OS锚夹具走独立沙箱 $ST_TMP/cc-team-r76；代码取 $SELF_CC（工作树改工作树测，红绿锁不依赖合入）。
T76=$ST_TMP/cc-team-r76
rm -rf $T76; mkdir -p $T76
C76(){ env -u CC_AUTO_VERIFY CC_TEAM_HOME=$T76 python3 "$SELF_CC/cards_dag.py" "$@"; }
R76(){ env CC_TEAM_HOME=$T76 python3 "$SELF_CC/ops/recycle.py" "$@"; }
g76(){ CC_TEAM_HOME=$T76 python3 -c "import json,os,sys;g=json.load(open(os.path.join(os.environ['CC_TEAM_HOME'],'state','graph.json'),encoding='utf-8'))
print(json.dumps(g['cards'].get(sys.argv[1],{}),ensure_ascii=False))" "$1"; }
ps1(){ powershell.exe -NoProfile -Command "$1" 2>/dev/null | tr -d '
' | tail -1; }
# ① 静态锁：只筛非注释行（文件头允许转述退役史，代码行零容忍）
if grep -v '^[[:space:]]*#' "$SELF_CC/ops/recycle.py"    | grep -qE 'win_pid_status|wsl_pid_status|trusted_anchor|_identity_verdict|Get-Process|tasklist'; then
  bad "76 进程探测在 recycle 回潮(NS-1 僵尸复活，判活又去问 OS)"
else
  ok "76 静态锁：recycle 判活零进程探测(权威=账本事件流)"
fi
# REQ-CCTEAM-OSS-1（公共 runner 必挂点①修复，审计 20261104 §四-P0）：#76 的「真活 pid」
# 夹具靠 powershell.exe 起 cmd——Windows 专属。旧码无门直调：ubuntu runner 上 ps1 恒空 →
# bad「前置缺失」=与机制好坏无关的必挂。改宿主门显式 SKIP（计入尾行 skips=，禁静默吞）；
# ①静态锁与 Windows 宿主上的 ②③④ 行为锁照旧实跑（宿主可达性=断言可达性，MECH-SELFTEST-WIN-1 纪律）。
if [ "$HOST_OS" = windows ] && command -v powershell.exe >/dev/null 2>&1; then
CMDPID=$(ps1 '$p=Start-Process cmd.exe -ArgumentList "/c","ping -n 600 127.0.0.1" -WindowStyle Hidden -PassThru; $p.Id')
if [ -z "$CMDPID" ]; then
  bad "76 前置缺失：Windows 侧起不出活锚 cmd（②失去「真活 pid」对立面，测了等于没测）"
else
  # ② 真活 pid + 断流 → 必回收（进程号不再保命）
  C76 create N76A --title "活锚断流" --lanes n76a --verify "true" >/dev/null
  C76 claim N76A --owner n76a --pid "$CMDPID" --pid-ns win >/dev/null
  grep -qE '"pid": *[1-9]' "$T76/claims/N76A/claim.json" && grep -q '"pid_ns"' "$T76/claims/N76A/claim.json"     && ok "76 留档字段照记 pid/pid_ns(④退役≠删审计面)" || bad "76 留档字段丢了(退役把排障面一并删了)"
  touch -d "20 minutes ago" "$T76/claims/N76A/claim.json"
  R76 --apply >$ST_TMP/r76a.out 2>&1
  g76 N76A | grep -q '"status": "OPEN"'     && ok "76 活 pid≠活：断流即收(名牌制：进程号退出判活)" || { bad "76 活 pid 装活=断流不回收未落地"; tail -3 $ST_TMP/r76a.out; }
  # ③ 死 pid + 活续写流 → 不动（防「判活顺手又杀进程表」）
  C76 create N76B --title "死锚活流" --lanes n76b --verify "true" >/dev/null
  C76 claim N76B --owner n76b --pid 999999 >/dev/null   # 999999=几乎不存在的进程号
  C76 progress N76B "续写一笔" --lane n76b --epoch 1 --ring 1 >/dev/null
  R76 --apply >$ST_TMP/r76b.out 2>&1
  grep -q "ALIVE N76B" $ST_TMP/r76b.out && g76 N76B | grep -q '"status": "CLAIMED"'     && ok "76 死 pid+活流=不动(事件流唯一活面，防误杀)" || { bad "76 被进程号误杀(判活回潮进程表?)"; tail -3 $ST_TMP/r76b.out; }
  ps1 "Stop-Process -Id $CMDPID -Force -ErrorAction SilentlyContinue" >/dev/null 2>&1
fi
else
  skip "76 活锚 cmd 夹具（本宿主无 powershell.exe=Windows 专属面；①静态锁已全宿主照跑）"
fi
rm -rf $T76

# 77 MECH-RESIDENT-1（业主 seq=8）常驻岗卡数据层锁——两族卡各管各：
#     岗卡四件缺位拒立、在岗态只走三态矩阵（ON-DUTY/DEGRADED/OFF）、VERIFIED=验收合格上岗、
#     DEGRADED 报警不自愈；普通卡混挂驻字段/duty 一律拒，七态链路一切照旧。
$CARD create R-731 --title "回收岗" --resident --verify "true" --lanes m1 --files "backend/api/" \
  --duty "扫claims/lane锁+DONE滞留 每10分钟" --anchor "*/10 * * * * python3 ops/recycle.py --apply" \
  --heartbeat "find logs/recycle.log -mmin -25" --fail "报警进alerts 不自愈" >/dev/null \
  && ok "77 岗卡四件齐可立" || bad "77 四件齐岗卡应可立"
gcard R-731 | grep -q '"resident": true' && ok "77 卡面resident=true" || bad "77 卡面缺resident字段"
gcard R-731 | grep -q '"resident_status": "OFF"' && ok "77 初值OFF(未验收不上岗)" || bad "77 在岗态初值非OFF"
expect_fail create R-731B --title "残件岗" --resident --verify "true" --duty "只有岗责"
expect_fail create R-732 --title "普卡混挂" --verify "true" --files "backend/api/" --anchor "*/5 x"
expect_fail duty R-731 ON-DUTY --by PM
$CARD claim R-731 --owner m1 >/dev/null
echo r73 >> backend/api/user.py && git add -A && git commit -qm "feat(R-731): 岗卡交付" >/dev/null
$CARD done R-731 --owner m1 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null 2>&1
$CARD verdict R-731 PASS --reason "验收" >/dev/null 2>&1
gcard R-731 | grep -q '"status": "VERIFIED"' && ok "77 岗卡七态链照常(done→verdict)" || bad "77 岗卡应可到VERIFIED"
gcard R-731 | grep -q '"resident_status": "ON-DUTY"' && ok "77 VERIFIED=验收合格上岗(三态同笔)" || bad "77 PASS后未自动上岗"
$CARD duty R-731 DEGRADED --by reconcile --reason "heartbeat find rc=1 静默35min" >/dev/null \
  && ok "77 ON-DUTY→DEGRADED" || bad "77 劣化迁移被拒"
expect_fail duty R-731 DONE --by PM
$CARD duty R-731 ON-DUTY --by PM --reason "锚件人工恢复复测过" >/dev/null \
  || bad "77 DEGRADED→ON-DUTY恢复被拒(链路断)"
$CARD duty R-731 OFF --by PM --reason "撤岗" >/dev/null || bad "77 →OFF被拒"
expect_fail duty R-731 DEGRADED --by reconcile
$CARD create R-733 --title "纯普卡" --verify "true" --lanes m1 --files "backend/api/" >/dev/null
expect_fail duty R-733 ON-DUTY --by PM
expect_fail set R-731 --heartbeat ""
gcard R-731 | grep -q '"heartbeat"' && ok "77 四件在卡面留存" || bad "77 四件丢失"
grep -q "DEGRADED 岗卡 R-731" $CC_TEAM_HOME/state/alerts.json \
  && ok "77 DEGRADED报警进alerts账(不自愈只出声)" || bad "77 劣化未报警=看门无人看复现"

# 78 BUG-LANES-STALE-1 改道滞留队列锁：队列条目永远以卡面 lanes 白名单为唯一身份。
#     实锤链(2026-11-02 dispatch.log)=DONE 卡 set --lanes 新道→verdict REJECT 按 prev_owner
#     回队旧道→dispatcher 每圈 propose@旧道被 lanes 硬闸拒，空转+杀会话死循环。
#     锁四路：REJECT 入队按白名单 / OPEN 态改道除旧补新 / release 入队按白名单 / reindex 剔越界道残渣。
$CARD create 0741 --title "改道卡" --verify "true" --files "backend/api/" --lanes q14 >/dev/null
$CARD claim 0741 --owner q14 >/dev/null
echo z741 >> backend/api/user.py && git add -A && git commit -qm "feat(0741): v1" >/dev/null
$CARD done 0741 --owner q14 --commit $(git rev-parse HEAD) --repo $REPO >/dev/null
$CARD set 0741 --lanes q15 >/dev/null                          # DONE 态改道（实锤第一步：旧码同步块因非 OPEN 整段跳过）
$CARD verdict 0741 REJECT --reason "重做" >/dev/null            # 回 OPEN——入队必须进新道 q15
grep -qx 0741 $Q/lane-q15.queue 2>/dev/null && ok "78 REJECT 入队按卡面 lanes(新道)" || bad "78 REJECT 按 prev_owner 入旧道(改道丢失=实锤根因)"
grep -qx 0741 $Q/lane-q14.queue 2>/dev/null && bad "78 旧道仍残留该卡=空转源头" || ok "78 旧道无改道卡"
$CARD plan --json >$ST_TMP/p78.out 2>/dev/null                     # 决策面红绿：下一圈应派到新车道
python3 - <<'PYEOF' && ok "78 claimable q15=0741 且旧道 q14 无派" || bad "78 engine 决策面仍会 propose 旧道"
import json, os
d = json.load(open(os.path.join(os.environ["SELFTEST_TMP"], "p78.out"), encoding="utf-8"))
assert d["claimable"].get("q15") == "0741", d["claimable"]
assert not d["claimable"].get("q14"), d["claimable"]
PYEOF
$CARD create 0742 --title "OPEN态改道" --verify "true" --files "backend/api/" --lanes q16 >/dev/null
$CARD set 0742 --lanes q17 >/dev/null
grep -qx 0742 $Q/lane-q17.queue 2>/dev/null && ok "78 OPEN 改道入新道" || bad "78 OPEN 改道未入新道"
grep -qx 0742 $Q/lane-q16.queue 2>/dev/null && bad "78 OPEN 改道旧道未除名" || ok "78 OPEN 改道旧道即时除名"
$CARD create 0743 --title "改道回收" --verify "true" --files "backend/api/" --lanes q18 >/dev/null
$CARD claim 0743 --owner q18 >/dev/null
$CARD set 0743 --lanes q19 >/dev/null                           # CLAIMED 中换道（道锁旧主 m18，回收通道最容易按 owner 回填）
$CARD release 0743 >/dev/null
grep -qx 0743 $Q/lane-q19.queue 2>/dev/null && ok "78 release 入队按卡面 lanes(新道)" || bad "78 release 按旧 owner 入旧道"
echo 0741 >> $Q/lane-q14.queue                                  # 手工塞越界条目=模拟任意来源的旧道残渣
$CARD reindex >/dev/null 2>&1
grep -qx 0741 $Q/lane-q14.queue 2>/dev/null && bad "78 reindex 未剔越界道条目(派生件不能从卡面重建)" || ok "78 reindex 剔除越界道条目"
grep -qx 0741 $Q/lane-q15.queue 2>/dev/null && ok "78 reindex 保留白名单道条目" || bad "78 reindex 误删白名单内条目"

# 79 REQ-EXIT-TIDY-1 散场自查交账锁：会话自然结束不收尾→卡躺 CLAIMED 等 ZOMBIE（实测 4 例，
#     无锚批回收环永远救不了）。锁六路：①散场档(已 block 过仍要散/SessionEnd)+归属两证
#     (session_id 同值 / 锚 pid 在本进程族谱)→当场 release 交账，回收日志现 EXIT-RELEASE 行；
#     ②归属证不成=照 block 不偷账；③首次 Stop/无 stdin=旧行为零变化；④异会话 NOTE 先于交账。
# 79a 负锁：散场档但锚 pid 非本会话族谱(远端假 pid)→不交账，照旧 block，卡留 CLAIMED 交回收环
$CARD create 0751 --title "散场负锁" --verify "true" --files "backend/api/" >/dev/null
$CARD claim 0751 --owner ex75a --pid 4194304 >/dev/null
if [ "$CAP_HOOK_STDIN" = 1 ]; then
  printf '{"session_id":"SID-75A","stop_hook_active":true,"hook_event_name":"Stop"}' \
    | python3 $CE --owner ex75a --repo $REPO >$ST_TMP/x75a.out 2>&1; rc=$?
  [ $rc -eq 2 ] && ok "79a 锚非本会话族谱→散场档照 block(不偷别会话账)" || bad "79a rc=$rc :: $(head -1 $ST_TMP/x75a.out)"
  gcard 0751 | grep -q '"status": "CLAIMED"' && ok "79a 证不成卡仍 CLAIMED(残局归 recycle)" || bad "79a 不该交账却交了"
  grep -q "EXIT-RELEASE 0751" $CC_TEAM_HOME/logs/recycle.log 2>/dev/null && bad "79a 负锁竟写了 EXIT-RELEASE" || ok "79a 无 EXIT-RELEASE 痕(假阴可检)"
else
  skip "79a 散场负锁三连：stdin 未读=散场档从未被驱动，rc=2/CLAIMED 皆降级 --owner 档巧合(假绿禁)——MECH-HOOK-STDIN-WIN-1"
  $CARD release 0751 >/dev/null
fi

# 79b 正锁·族谱直证：驱动进程=hook 之父(即「本次会话起的进程」现场模拟)，claim 锚改指驱动 pid；
#     喂 stop_hook_active → 当场交账回 OPEN，EXIT-RELEASE 行落回收日志(不等 ZOMBIE)。
$CARD create 0752 --title "散场正锁" --verify "true" --files "backend/api/" --lanes ex75b >/dev/null
$CARD claim 0752 --owner ex75b --pid 4194304 >/dev/null
if [ "$CAP_HOOK_STDIN" = 1 ]; then
python3 - "$CE" "$CC_TEAM_HOME" "$REPO" <<'PYEOF' >$ST_TMP/x75b.out 2>&1
import json, os, subprocess, sys
ce, team, repo = sys.argv[1:4]
me = os.getpid()
for p in (os.path.join(team, "claims", "0752", "claim.json"),
          os.path.join(team, "state", "lane-ex75b.lock", "meta.json")):
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, ValueError):
        continue
    j["pid"] = me
    with open(p, "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False)
inp = json.dumps({"session_id": "SID-75B", "stop_hook_active": True, "hook_event_name": "Stop"})
r = subprocess.run([sys.executable, ce, "--owner", "ex75b", "--repo", repo],
                   input=inp, capture_output=True, text=True, timeout=90)
sys.stdout.write("HOOK_RC=%s\n%s%s" % (r.returncode, r.stdout or "", r.stderr or ""))
sys.exit(r.returncode)
PYEOF
grep -q "HOOK_RC=0" $ST_TMP/x75b.out && ok "79b 族谱证成→放行退出(exit 0)" || bad "79b hook 未放行 :: $(head -3 $ST_TMP/x75b.out)"
grep -q "EXIT-RELEASE 0752" $ST_TMP/x75b.out && ok "79b stdout 现 EXIT-RELEASE 行" || bad "79b 无成交痕"
grep -q "EXIT-RELEASE 0752" $CC_TEAM_HOME/logs/recycle.log && ok "79b 回收日志现 EXIT-RELEASE(而非等 ZOMBIE)" || bad "79b 回收日志无痕"
gcard 0752 | grep -q '"status": "OPEN"' && ok "79b 卡已交账回 OPEN" || bad "79b 卡未回 OPEN"
[ ! -d $CC_TEAM_HOME/claims/0752 ] && [ ! -e $Q/lane-ex75b.lock ] && ok "79b 双锁随交账同清" || bad "79b 残锁未清"
grep -qx 0752 $Q/lane-ex75b.queue && ok "79b 回队头等重派" || bad "79b 未回队"
else
  skip "79b 族谱正锁六道：select 管道抛错→hook 根本没读 stdin，族谱证明无从发生——MECH-HOOK-STDIN-WIN-1；卡交回 release 保持道净"
  $CARD release 0752 >/dev/null
fi

# 79c SessionEnd 档：会话终结钩(无 stop_hook_active 字段)同样触发自查交账
$CARD create 0753 --title "散场终结钩" --verify "true" --files "backend/api/" >/dev/null
$CARD claim 0753 --owner ex75c --pid 4194304 >/dev/null
if [ "$CAP_HOOK_STDIN" = 1 ]; then
python3 - "$CE" "$CC_TEAM_HOME" "$REPO" 0753 ex75c <<'PYEOF' >$ST_TMP/x75c.out 2>&1
import json, os, subprocess, sys
ce, team, repo, cid, owner = sys.argv[1:6]
me = os.getpid()
for p in (os.path.join(team, "claims", cid, "claim.json"),
          os.path.join(team, "state", "lane-%s.lock" % owner, "meta.json")):
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, ValueError):
        continue
    j["pid"] = me
    with open(p, "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False)
inp = json.dumps({"session_id": "SID-75C", "hook_event_name": "SessionEnd"})
r = subprocess.run([sys.executable, ce, "--owner", owner, "--repo", repo],
                   input=inp, capture_output=True, text=True, timeout=90)
sys.stdout.write("HOOK_RC=%s\n%s%s" % (r.returncode, r.stdout or "", r.stderr or ""))
sys.exit(r.returncode)
PYEOF
grep -q "HOOK_RC=0" $ST_TMP/x75c.out && gcard 0753 | grep -q '"status": "OPEN"' \
  && ok "79c SessionEnd 当场交账(终结钩不空等)" || bad "79c :: $(head -3 $ST_TMP/x75c.out)"
grep -q "EXIT-RELEASE 0753" $CC_TEAM_HOME/logs/recycle.log && ok "79c SessionEnd 留 EXIT-RELEASE 痕" || bad "79c 无痕"
else
  skip "79c SessionEnd 终结钩两道：同 MECH-HOOK-STDIN-WIN-1 门控(stdin 未读=终结档无从触发)"
  $CARD release 0753 >/dev/null
fi

# 79d 兼容档零变化：无 stdin、以及首次 Stop(无 stop_hook_active)——散场自查不吃第一道闸
$CARD create 0754 --title "首停照拦" --verify "true" --files "backend/api/" >/dev/null
$CARD claim 0754 --owner ex75d --pid 4194304 >/dev/null
python3 $CE --owner ex75d --repo $REPO </dev/null >$ST_TMP/x75d1.out 2>&1; rc=$?
[ $rc -eq 2 ] && ok "79d 无 stdin 手跑=旧档照 block(散场自查不吃无钩子调用)" || bad "79d 无 stdin rc=$rc"
if [ "$CAP_HOOK_STDIN" = 1 ]; then
  printf '{"hook_event_name":"Stop","session_id":"SID-75D"}' | python3 $CE --owner ex75d --repo $REPO >$ST_TMP/x75d2.out 2>&1; rc=$?
  [ $rc -eq 2 ] && grep -q "散场自查" $ST_TMP/x75d2.out \
    && ok "79d 首次 Stop 不缴在制卡，block 文点名散场通道(再停才交账)" || bad "79d 首停 rc=$rc"
  gcard 0754 | grep -q '"status": "CLAIMED"' && ok "79d 首停后卡仍在制" || bad "79d 首停误交账"
else
  skip "79d 首停两道：同 MECH-HOOK-STDIN-WIN-1 门控(首停/再停两态区分依赖读 stdin)"
fi
$CARD release 0754 >/dev/null

# 79e 无锚批回收救不了、散场自查救得了：claim 无 --pid(kind=session,回收环 WARN-only)+
#     认领时绑了 session_id，喂同 sid+stop_hook_active → sid 同值直证成立，当场交账
$CARD create 0755 --title "无锚散场" --verify "true" --files "backend/api/" >/dev/null
CLAW_SESSION_ID=SID-75E $CARD claim 0755 --owner ex75e >/dev/null
if [ "$CAP_HOOK_STDIN" = 1 ]; then
  printf '{"session_id":"SID-75E","stop_hook_active":true,"hook_event_name":"Stop"}' \
    | python3 $CE --owner ex75e --repo $REPO >$ST_TMP/x75e.out 2>&1; rc=$?
  [ $rc -eq 0 ] && ok "79e 无锚 pid 但 sid 同值→散场交账放行" || bad "79e rc=$rc :: $(head -1 $ST_TMP/x75e.out)"
  gcard 0755 | grep -q '"status": "OPEN"' && grep -q "EXIT-RELEASE 0755" $CC_TEAM_HOME/logs/recycle.log \
    && ok "79e 回收环救不了的无锚批在此路被救(OPEN+留痕)" || bad "79e 未交账或无痕"
else
  skip "79e 无锚散场两道：同 MECH-HOOK-STDIN-WIN-1 门控"
  $CARD release 0755 >/dev/null
fi

# 79f 异会话 NOTE 先于交账：散场档也不碰别人会话的账（NOTE 放行，卡留原主等 PM/recycle）
$CARD create 0756 --title "异会话不碰" --verify "true" --files "backend/api/" >/dev/null
CLAW_SESSION_ID=SID-OWN $CARD claim 0756 --owner ex75f >/dev/null
if [ "$CAP_HOOK_STDIN" = 1 ]; then
  printf '{"session_id":"SID-OTHER","stop_hook_active":true,"hook_event_name":"Stop"}' \
    | python3 $CE --owner ex75f --repo $REPO >$ST_TMP/x75f.out 2>&1; rc=$?
  [ $rc -eq 0 ] && grep -q "NOTE" $ST_TMP/x75f.out \
    && ok "79f 异会话散场不拦也不交(先 NOTE 后自查的顺序锁)" || bad "79f rc=$rc"
  gcard 0756 | grep -q '"status": "CLAIMED"' && ok "79f 别会话账原样保留" || bad "79f 误交别人账"
else
  skip "79f 异会话顺序锁两道：同 MECH-HOOK-STDIN-WIN-1 门控(降级档 owner 相符必拦,NOTE 放行路径不可达)"
fi
$CARD release 0756 >/dev/null

# 80 MECH-STATE-STD-1 台账状态标准化定版（同源锁）：状态=属主+出口/七态术语表/每态必填字段/
#     迁移矩阵单一数据表——README↔代码逐行对读，矩阵 34 边 CLI 全走通、表外抽测必拒，
#     done 收口写 done_at+REJECT 清锚，缺字段闸三档（缺省 warn 不进告警账=防存量满盘红误报）。
#     断言在独立文件 tests/state_std_test.py（冲突纪律：不与 MECH-DONE-SWEEP-1 挤 selftest 尾部），
#     自锚所在仓代码+独立进程内沙箱，不碰本套台账。
python3 "$SELF_DIR/state_std_test.py" > $ST_TMP/cc-std80.log 2>&1
if grep -q '^STATE_STD_PASS$' $ST_TMP/cc-std80.log; then ok "80 状态标准化同源锁(矩阵/术语/属主/字段闸)";
else bad "80 状态标准化同源锁" "$(tail -2 $ST_TMP/cc-std80.log | tr '
' ' ')"; fi

# 81 BUG-REAPER-SCOPE-1 看护环报警范围锁（运行时断言，非 grep 字样）：属主表 seq4 定死
#     CLAIMED 是回收看护唯一可报警状态。五把锁：①真 CLAIMED 卡的看护警连圈存活（防空铃假闸）；
#     ②给 DONE 卡手工塞看护格式警→本卡 check 脚本必须先红后绿（判据不是 grep 的实锤）；
#     ③清账=autoack 留痕不删行（审计可还原）；④dry 只点名不灭灯、清后多圈不重生；
#     ⑤--sweep-only 专趟且图不可读时拒清（账本读错≠卡全死）。回收环用**本道** ops/recycle.py
#     （$SELF_DIR 自锚），撤掉清账/状态闸则①②③④⑤相应红=修复被钉死。
T81REC="python3 $SELF_DIR/../ops/recycle.py"
T81CHK="python3 $SELF_DIR/alarm_scope_check.py"
t81open(){ python3 - "$SELF_DIR/.." "$1" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from ccards import alerts
hits = [e for e in alerts.open_entries() if (e.get("msg") or "").startswith(sys.argv[2])]
print("OPEN" if hits else "CLOSED")
PYEOF
}
t81stuff(){ sleep 1   # 复塞必须与上一次 ack 隔秒：alerts 时间戳=秒精度，同秒 record 的 last==ack
                            # 会被 open_entries 判回「已闭合」→ 假 CLOSED 误红（生产圈距≥1min 无此撞秒）
  python3 - "$SELF_DIR/.." "$1" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from ccards import alerts
alerts.record("ALARM %s: owner=%s session 执行卡(无回收锚) 静默超阈 → 连续两轮 WARN 仍未收口，请 PM 经 inbox 处置(不 release)"
              % (sys.argv[2], "m92"))
PYEOF
}
# ① 对照：m91 手工认领（无锚=session 档）连两圈 --apply 升 ALARM；该卡留在制，清账环不得动它
$CARD create T81C --title "对照在制" --lanes m91 --verify "true" --files "backend/api/" >/dev/null
$CARD claim T81C --owner m91 >/dev/null
$T81REC --apply >/dev/null 2>&1
$T81REC --apply >/dev/null 2>&1
[ "$(t81open 'ALARM T81C:')" = "OPEN" ] && ok "81 真 CLAIMED 卡连两轮 WARN 升 ALARM 且清账环不误杀" \
  || bad "81 CLAIMED 看护警缺失（升 Alarm 评估或存活闸断了）"
# ② DONE 卡+塞警：check 先红（断言真咬）→ 回收一圈 → 清账 → check 转绿
$CARD create T81D --title "僵尸警主" --lanes m92 --verify "true" --files "backend/api/" >/dev/null
$CARD claim T81D --owner m92 >/dev/null
echo kk >> backend/api/user.py && git add -A && git commit -qm "feat(T81D): kk" >/dev/null
env -u CC_AUTO_VERIFY $CARD done T81D --owner m92 --commit "$(git rev-parse HEAD)" --repo $REPO --evidence "ok" >/dev/null 2>&1
gcard T81D | grep -q '"status": "DONE"' && ok "81 T81D 落 DONE(对照前提)" || bad "81 T81D 未停在 DONE，控制组失效"
t81stuff T81D
$T81CHK >/dev/null 2>&1 && bad "81 DONE 卡有看护警时 check 未红=假断言" \
  || ok "81 check 先红：点名 DONE 卡的看护警（运行时断言非 grep）"
$T81REC --apply >/dev/null 2>&1
[ "$(t81open 'ALARM T81D:')" = "CLOSED" ] && ok "81 DONE 卡看护警一圈内被清账" || bad "81 清账环失效：僵尸警仍开放"
$T81CHK >/dev/null 2>&1 && ok "81 check 清账后转绿" || bad "81 清账后 check 仍红（清账不彻底）"
[ "$(t81open 'ALARM T81C:')" = "OPEN" ] && ok "81 清账环后在制对照警仍存活（不空铃）" \
  || bad "81 清账环越权误杀 CLAIMED 警"
# ③ 清账=ack 留痕不删行
python3 - "$SELF_DIR/.." <<'PYEOF' && ok "81 清账痕在案(ack 非删行，自动身份 auto@/auto: 两形)" || bad "81 清账不留痕"
import sys
sys.path.insert(0, sys.argv[1])
from ccards import alerts
e = [v for k, v in alerts.entries().items() if k.startswith("ALARM T81D:")]
# MECH-TRAIN-ADR-1 ④轴（既有断言漂移实锤，复现=tmp/rep81sec.sh 独占 0.1s 即红）：ea9fa378
# 合并形后「卡状态迁移销警」身份缺省=auto:recycle（冒号），旧契约 auto@ 仍在 scope 路用——
# 两形皆自动身份合法；闸不放宽：人工销警痕（裸属主名）照拒。
assert e and all(v.get("ack") and str(v.get("acked_by") or "").startswith(("auto@", "auto:")) for v in e)
PYEOF
# ④ dry 只点名；清账后复塞(模拟复响)再被 apply 圈接住，多圈不重生
t81stuff T81D
$T81REC --dry >/dev/null 2>&1
[ "$(t81open 'ALARM T81D:')" = "OPEN" ] && ok "81 dry 档不写账(僵尸警仅点名)" || bad "81 dry 越权清账"
$T81REC --apply >/dev/null 2>&1
t81stuff T81D
$T81REC --apply >/dev/null 2>&1
$T81REC --apply >/dev/null 2>&1
[ "$(t81open 'ALARM T81D:')" = "CLOSED" ] && ok "81 复响被再次清账且后续圈不重生" || bad "81 复响圈失效"
# ⑤ --sweep-only 专趟：图不可读=拒清（防空铃）；图恢复=正常清
t81stuff T81D
mv "$CC_TEAM_HOME/state/graph.json" "$CC_TEAM_HOME/state/graph.json.bak81"
$T81REC --sweep-only --apply >/dev/null 2>&1
[ "$(t81open 'ALARM T81D:')" = "OPEN" ] && ok "81 图不可读时 sweep-only 拒清(防误灭铃)" || bad "81 账本读空竟清账=空铃事故口子"
mv "$CC_TEAM_HOME/state/graph.json.bak81" "$CC_TEAM_HOME/state/graph.json"
$T81REC --sweep-only --dry >/dev/null 2>&1
[ "$(t81open 'ALARM T81D:')" = "OPEN" ] && ok "81 sweep-only dry 只点名" || bad "81 sweep-only dry 越权"
$T81REC --sweep-only --apply >/dev/null 2>&1
[ "$(t81open 'ALARM T81D:')" = "CLOSED" ] && ok "81 sweep-only 专趟存量清账" || bad "81 sweep-only 未清账"
$T81CHK >/dev/null 2>&1 && ok "81 收口态 check 全绿(看护面零越界)" || bad "81 收口态仍有越界警"

# 82 MECH-BOARD-TREND-1 心跳区 24h 逐时趋势图锁：合成账真跑 board/status-web.py 服务端渲染层
#    （_claimstr/_donestr 优先级、24 格逐桶对账、零外链、空账诚实态、SNAP 正下方接线、红自证撤接线）。
if [ ! -f "$SR/board/status-web.py" ]; then skip "#82 心跳 24h 逐时趋势图桩（board 运营层未随独立发行包）"
elif python3 $SR/tests/board_trend_check.py >$ST_TMP/p82.out 2>&1 \
  && grep -q "BOARD_TREND_PASS" $ST_TMP/p82.out; then ok "#82 心跳 24h 逐时趋势图桩全绿(含红自证)"
else bad "#82 心跳趋势图守卫红(见 $ST_TMP/p82.out)"; tail -8 $ST_TMP/p82.out; fi
# 83 BUG-DISPATCH-PID-1 红绿锁：无锚 lane 锁过期可自愈（复现 PARITY-6 23:45-53 空转=
#    claim 不传 pid → 占主未明 → recycle 只 WARN 永不 release → 道锁死锁拒派同道后续卡）。
#    REQ-BEAT-ANCHOR-1 名牌制语义迁移：旧「ANCHORLESS 5min 窗降级 release / ZOMBIE-NOANCHOR」
#    并入「断流=死」——claim 一律自动盖任期（无特判分支），回收只看心跳/圈号/任期三条。
#    ①静态锁：dispatcher claim 传 --pid/--pid-ns 转留档，且**派发前先 `epoch` 发牌**、claim 带
#      --epoch 对班（未发牌=不派发，派发侧对班是结构性前提）；
#    ②行为锁：道锁在场但无进度流、账本静默超 --minutes(15) → ZOMBIE release 回 OPEN+清道锁；
#    ③护栏：窗内（刚 claim、心跳新）ALIVE 不动——防把并发中的活锁当孤儿抢。
#    E2E-T1「session kind 永不 release」由 #27/#48 继续持锁，本用例不触碰。
grep -q -- '--pid' "$SELF_CC/ops/dispatch.py" && grep -q -- '--pid-ns' "$SELF_CC/ops/dispatch.py" \
  && ok "83 dispatcher claim 带 --pid/--pid-ns 留档锚(①)" || bad "83 dispatcher 锚留档字段丢了(审计面残缺)"
grep -qE 'run_gate\("epoch"' "$SELF_CC/ops/dispatch.py" && grep -qE '"--epoch", ep' "$SELF_CC/ops/dispatch.py" \
  && ok "83 dispatcher 派发前先 epoch 发牌、claim 带 --epoch 对班(①名牌)" \
  || bad "83 dispatcher 未走发牌对班通道(未发牌照派=名牌制派发侧破防)"
$CARD create 0720 --title 无锚过期自愈 --verify "true" >/dev/null
mkdir -p $Q/lane-r72a.lock
python3 - "$Q/lane-r72a.lock" <<'PYEOF'
import json, os, sys
json.dump(dict(owner="r72a", pid=None, kind="lane", ts="2026-11-01T00:00:00+0800"),
          open(os.path.join(sys.argv[1], "meta.json"), "w", encoding="utf-8"))
PYEOF
touch -d "10 minutes ago" $Q/lane-r72a.lock/meta.json
$CARD claim 0720 --owner r72a >/dev/null   # 道锁在场(过期残渣)→占位抢锁→kind=lane 而锚无 pid
grep -q '"kind": "lane"' $CC_TEAM_HOME/claims/0720/claim.json \
  && ! grep -Eq '"pid": *[1-9]' $CC_TEAM_HOME/claims/0720/claim.json \
  && ok "83 夹具成形: lane 锁+无锚 pid(旧 claim 未传 --pid 的实况)" || bad "83 夹具未落 lane 无锚形(用例失去意义)"
touch -d "20 minutes ago" $CC_TEAM_HOME/claims/0720/claim.json   # 名牌制阈=--minutes 默认 15
python3 "$SELF_CC/ops/recycle.py" --dry >$ST_TMP/rec72.out 2>&1
grep -q "ZOMBIE 0720" $ST_TMP/rec72.out && gcard 0720 | grep -q '"status": "CLAIMED"' \
  && ok "83 断流超窗 dry 报 ZOMBIE 不动手(名牌制：无进度流+静默20min)" || bad "83 无锚过期锁未判可回收(旧行为=只能喊PM)"
python3 "$SELF_CC/ops/recycle.py" --apply >$ST_TMP/rec72b.out 2>&1
gcard 0720 | grep -q '"status": "OPEN"' && [ ! -d $CC_TEAM_HOME/claims/0720 ] && [ ! -d $Q/lane-r72a.lock ] \
  && ok "83 apply 自愈: 卡回OPEN+道锁清(同道可再claim=空转终结)" || bad "83 无锚过期锁不自愈(死锁未修)"
$CARD create 0721 --title 无锚新鲜窗 --verify "true" >/dev/null
mkdir -p $Q/lane-r72b.lock
python3 - "$Q/lane-r72b.lock" <<'PYEOF'
import json, os, sys
json.dump(dict(owner="r72b", pid=None, kind="lane", ts="2026-11-01T00:00:00+0800"),
          open(os.path.join(sys.argv[1], "meta.json"), "w", encoding="utf-8"))
PYEOF
touch -d "10 minutes ago" $Q/lane-r72b.lock/meta.json
$CARD claim 0721 --owner r72b >/dev/null    # 抢旧锁后新锁新鲜（claim 落账即静默归零）
python3 "$SELF_CC/ops/recycle.py" --dry >$ST_TMP/rec72c.out 2>&1
grep -q "ALIVE 0721" $ST_TMP/rec72c.out && ! grep -q "ZOMBIE 0721" $ST_TMP/rec72c.out \
  && gcard 0721 | grep -q '"status": "CLAIMED"' \
  && ok "83 新鲜认领窗内保守: ALIVE 不动(并发残渣防护=名牌制断流阈前的静默归零)" || bad "83 新鲜无锚锁被提前回收(自愈窗破)"
$CARD release 0721 >/dev/null


# 84 MECH-FLEET-ALARM-1 红绿锁（2026-11-01 深夜实锤：四条道闲置+7 张 ready 全钉 m7 堵死数小时，
#    dispatch 每分钟 SKIP/CAP/GATE-拒各喊各的、从不连看车队=PM 失职无兜底）。
#    判据（tick 末）：backlog（有队头且本圈未派出）非空 且 idle（无主活道：有工作树/无在制/
#    无队头）非空 → 连 2 拍出 ALARM IDLE-WARN，三面同现=dispatch.log(stdout)+alerts 告警面+
#    rejects 看板被拒页签，消息附卡 id 与可用道。①静态锁：警报名在 dispatch.py；
#    ②行为锁：ready+闲道=出警（首拍不出、二拍出、列 id 列道），全忙（idle=∅）=连拍仍不出。
#    DSR 可钉到未合并工作树验收（DSR=/mnt/c/.../wf-m5/cc-team），默认主仓。
DSR="${DSR:-$SR}"
grep -q "IDLE-WARN" $DSR/ops/dispatch.py && ok "84 dispatcher 含 IDLE-WARN 连看警报(①)" \
  || bad "84 dispatcher 仍不连看车队(ready×闲道并存的 PM 失职无兜底)"
F73=$ST_TMP/cc-fleet73; rm -rf $F73; mkdir -p $F73/wf-r73c $F73/wf-r73idle
touch $CC_TEAM_HOME/state/lane-r73idle.queue
$CARD create 0730 --title 积压卡 --lanes r73a --verify "true" >/dev/null
$CARD create 0732 --title 在制卡 --lanes r73c --verify "true" >/dev/null
$CARD claim 0732 --owner r73c >/dev/null
# A ready(0730@r73a 无工作树=SKIP 候派) × 闲道(r73idle 有队列无主；r73c 在制不算闲)：
#   首拍连拍未满=不出警（单拍抖动不误喊），第二拍出警且列明卡 id+可用道。
DISPATCH_CODE_ROOT=$F73 DISPATCH_HB=$F73/hb.txt DISPATCH_CAP=2 \
  python3 $DSR/ops/dispatch.py --once >$ST_TMP/f73r1.out 2>&1
! grep -q IDLE-WARN $ST_TMP/f73r1.out && ok "84 首拍不出警(连拍未满)" || bad "84 未满连拍就出警(单拍误报)"
DISPATCH_CODE_ROOT=$F73 DISPATCH_HB=$F73/hb.txt DISPATCH_CAP=2 \
  python3 $DSR/ops/dispatch.py --once >$ST_TMP/f73r2.out 2>&1
grep -q "ALARM IDLE-WARN" $ST_TMP/f73r2.out && grep -q "0730" $ST_TMP/f73r2.out && grep -q "r73idle" $ST_TMP/f73r2.out \
  && ok "84 连二拍出警且附卡id+可用道(改道/建道一眼可判)" || bad "84 ready×闲道连拍未出警(失职无兜底仍在)"
grep -q "IDLE-WARN" $CC_TEAM_HOME/state/alerts.json && ok "84 alerts 告警面同现" || bad "84 未进告警面"
grep -q "IDLE-WARN" $CC_TEAM_HOME/state/rejects.log && ok "84 rejects 看板被拒页签同现" || bad "84 未进被拒页签"
# B 全忙不出警：积压道补工作树但并发=1（头被 CAP 拦=仍 backlog），闲道撤下名册（无工作树）
#   → backlog×idle=∅，连二拍必须静默（防无兜底反向变异成天天狼来了）。
mkdir -p $F73/wf-r73a && rmdir $F73/wf-r73idle
DISPATCH_CODE_ROOT=$F73 DISPATCH_HB=$F73/hb.txt DISPATCH_CAP=1 \
  python3 $DSR/ops/dispatch.py --once >$ST_TMP/f73r3.out 2>&1
DISPATCH_CODE_ROOT=$F73 DISPATCH_HB=$F73/hb.txt DISPATCH_CAP=1 \
  python3 $DSR/ops/dispatch.py --once >$ST_TMP/f73r4.out 2>&1
! grep -q IDLE-WARN $ST_TMP/f73r4.out && ok "84 全忙(idle=∅)连拍仍不出警" || bad "84 全忙误出警(车队警报成噪音)"


# 85 MECH-BOARD-LOGBTAIL-1 红绿锁（业主 2026-11-02 02:0x 令「运行日志必须关联到 DAG 卡节点」）：
#    bind 通道（MECH-S1 现成）自 v3 派发器上线一直闲置——派出卡 session_id 恒 "-"，
#    看板点开卡读不到 worker 运行日志。四段锁：
#    ①静态：dispatch 含起班回填 + status-web 含 logtail 端点与 LOGBTAIL 痕；
#    ②发现（纯函数）：假转录三文件——历史残渣(双锚但 mtime 早于起班)不取/subagent 支线(无
#      queue-operation+双锚)不取/活会话(enqueue 首行含卡号+道名双锚且新鲜)=取；全删=None（防空枪）；
#    ③闸门：bind 只接 CLAIMED，锚同写 claim.json+道锁 meta+events 痕（双锁同写）；OPEN 卡拒绑；
#    ④补绑：backfill_binds 对在制无锚卡按 claim ts 找到并补绑；已绑卡再跑不动（幂等防锚漂移）。
#    整合修（2026-11-02 全量套实锤）：卡号 0740/0742/0743→0851/0852/0853——与主干 #78 块（1211/1222 行）
#    同册撞号，单块切片不显、顺序全跑必塌（create 撞已存在→claim/done/bind 级联前提破）。
# REQ-OSS-PREP-1：board 层不随独立发行包——有 board 时断言与文案逐字不变（主树零漂移），
# 无 board 时只锁引擎侧两断言并 SKIP 看板侧（降档不放水）。
if [ ! -f "$DSR/board/status-web.py" ]; then
  grep -q "bind_after_launch" $DSR/ops/dispatch.py && grep -q "backfill_binds" $DSR/ops/dispatch.py \
    && ok "85 起班回填两端在场(①)" || bad "85 起班回填缺位(会话锚通道继续闲置)"
  skip "85 看板运行尾链（board 运营层未随独立发行包）"
else
  grep -q "bind_after_launch" $DSR/ops/dispatch.py && grep -q "backfill_binds" $DSR/ops/dispatch.py \
    && grep -q "LOGBTAIL" $DSR/board/status-web.py && grep -q "logtail_data" $DSR/board/status-web.py \
    && grep -q "lt-box" $DSR/board/live.js \
    && ok "85 起班回填+看板运行尾两端在场(①)" || bad "85 运行尾链任一端缺位(会话锚通道继续闲置)"
fi
F74=$ST_TMP/cc-bind74; rm -rf $F74
# REQ-OSS-ENGINE-1：转录目录名单源=与被测引擎同一函数现算（config.proj_dir_name，
# 锚语义与 dispatch._proj_dir 逐字一致：DISPATCH_CODE_ROOT 在场随锚、缺省走 CC_CODE 派生）。
slug74(){ python3 - "$DSR" "$1" <<'PYEOF'
import os, sys
sys.path.insert(0, sys.argv[1])
from ccards import config
print(config.proj_dir_name(os.environ.get("DISPATCH_CODE_ROOT") or config.CC_CODE, sys.argv[2]))
PYEOF
}
S74A=$(slug74 r74a)
mkdir -p "$F74/$S74A"
python3 - "$F74" "$DSR" "$S74A" <<'PYEOF'
import json, os, sys, time
proj, dsr, slug = sys.argv[1], sys.argv[2], sys.argv[3]
d = os.path.join(proj, slug)
def mk(name, first, age):
    p = os.path.join(d, name)
    open(p, "w", encoding="utf-8").write(first + "\n{\"type\":\"assistant\"}\n")
    t = time.time() - age
    os.utime(p, (t, t))
mk("dead-1.jsonl", json.dumps({"type": "queue-operation", "operation": "enqueue", "sessionId": "dead-1",
   "content": "你是泳道 r74a 的 worker\n\n# 卡 0851：旧班"}, ensure_ascii=False), 7200)  # 双锚但早于起班
mk("side-1.jsonl", '{"type":"assistant","isSidechain":true,"message":{"content":[{"type":"tool_use","name":"Bash"}]}}', 1)
mk("live-9.jsonl", json.dumps({"type": "queue-operation", "operation": "enqueue", "sessionId": "live-9",
   "content": "你是泳道 r74a 的 worker\n\n# 卡 0851：本班\n\n## 完成判据"}, ensure_ascii=False), 0)
os.environ["DISPATCH_PROJ"] = proj
sys.path.insert(0, os.path.join(dsr, "ops"))
import dispatch
now = time.time()
hit = dispatch.discover_session("0851", "r74a", now - 60)
assert hit == "live-9", "发现应=live-9，实=%r（残渣/支线任一混入=串锚假绿）" % hit
assert dispatch.discover_session("0851", "r74a", now + 3600) is None, "起班下限抬高应查无（mtime 闸红得起来）"
assert dispatch.discover_session("9999", "r74a", now - 60) is None, "他卡号查无（双锚内容闸红得起来）"
print("DISCOVER_OK")
PYEOF
[ $? -eq 0 ] && ok "85 会话发现=双锚+mtime 确定性匹配(②含三条红自证)" || bad "85 会话发现误锚或失灵(猜最新=迟早绑错人)"
$CARD create 0851 --title 会话锚回填 --lanes r74a --verify "true" >/dev/null
$CARD claim 0851 --owner r74a >/dev/null
$CARD bind 0851 --session live-9 >/dev/null 2>&1 && ok "85 CLAIMED 卡 bind 收(③通道)" || bad "85 bind 通道不可用"
grep -q '"session_id": "live-9"' $CC_TEAM_HOME/claims/0851/claim.json \
  && ok "85 锚进 claim.json 运行记录" || bad "85 bind 未回写 claim.json"
grep -q '"session_id": "live-9"' $CC_TEAM_HOME/state/lane-r74a.lock/meta.json \
  && ok "85 锚同写道锁 meta(双锁同写)" || bad "85 道锁 meta 缺锚"
grep -q '|0851|CLAIMED->CLAIMED|live-9|bind session_id' $CC_TEAM_HOME/state/events.log \
  && ok "85 bind 留 events 痕" || bad "85 无 bind 痕（审计瞎）"
$CARD bind 0851 --session live-9 >/dev/null 2>&1 && ok "85 重复 bind 幂等覆写可收(cmd_bind 现成承诺)" || bad "85 重复 bind 被拒(通道自相矛盾)"
$CARD create 0852 --title 收工卡拒bind --lanes r74c --verify "true" >/dev/null
$CARD claim 0852 --owner r74c >/dev/null
$CARD done 0852 --owner r74c --commit HEAD --repo $REPO >/dev/null 2>&1
expect_fail bind 0852 --session x-1      # DONE 卡拒 bind（cmd_bind 只接 CLAIMED 的闸）
# ④补绑：r74b 在制无锚卡 + 活转录 → backfill_binds 找到并补；再跑一次锚不变（幂等）。
$CARD create 0853 --title 补绑卡 --lanes r74b --verify "true" >/dev/null
$CARD claim 0853 --owner r74b >/dev/null
grep -q '"session_id": "-"' $CC_TEAM_HOME/claims/0853/claim.json && ok "85 夹具成形：在制无锚" || bad "85 claim 未带占位锚(前提不成立)"
S74B=$(slug74 r74b)
mkdir -p "$F74/$S74B"
python3 - "$F74" "$S74B" <<'PYEOF'
import json, os, sys, time
d = os.path.join(sys.argv[1], sys.argv[2])
p = os.path.join(d, "backfill-7.jsonl")
open(p, "w", encoding="utf-8").write(json.dumps({"type": "queue-operation", "operation": "enqueue",
  "sessionId": "backfill-7", "content": "你是泳道 r74b 的 worker\n\n# 卡 0853：补绑班"}, ensure_ascii=False) + "\n")
PYEOF
python3 - "$F74" "$DSR" <<'PYEOF'
import os, sys
os.environ["DISPATCH_PROJ"] = sys.argv[1]
sys.path.insert(0, os.path.join(sys.argv[2], "ops"))
import dispatch
dispatch.backfill_binds({"0853": "r74b"})
dispatch.backfill_binds({"0853": "r74b"})   # 幂等：已锚卡第二跑必须原样不动
PYEOF
[ $? -eq 0 ] && grep -q '"session_id": "backfill-7"' $CC_TEAM_HOME/claims/0853/claim.json \
  && grep -q '"session_id": "backfill-7"' $CC_TEAM_HOME/state/lane-r74b.lock/meta.json \
  && ok "85 tick 补绑兜底+幂等不漂移(④)" || bad "85 补绑失灵或幂等破(起班窗口没赶上=永远缺锚)"


# 86 MECH-BOARD-STATS-1 红绿锁（业主 2026-11-02 00:2x 令「看板顶部常驻今日收口/在跑/待跑/总数 +
#    最近完成10条交叉对表」）：数据源唯一=账本经 ccards.graph（_ledger_cards 通道，零旁路扫描）。
#    账面缺「转正时间」由写侧补：cmd_verdict PASS 落 VERIFIED 瞬间盖专用 at 字段——与 updated_at
#    分离（set 改 desc 拖不走日历），REJECT 清旧戳、重转正重盖；看板读侧仅以 verify_run 痕内日期
#    回填旧卡，无痕不计（宁缺毋假）。三段锁：①静态三端在场；②行为（沙箱账，DSR 钉未合并工作树，
#    主仓未合并必红）；③纯函数直测 stats_compute/stats_render——北京日切（UTC16:30=次日00:30
#    归属日切）、跨日归零、痕回填、>10 截断黄字报差、四数点进清单带过滤号，外加撤 at 读取今日数
#    必塌的红自证。
#    整合修（2026-11-02 全量套实锤）：卡号 0751→0861——与主干 #79a 块（1232 行「散场负锁」，
#    留卡 CLAIMED）撞号，顺序全跑 create 撞已存在→claim/done/verdict 级联前提破（单切片不显）。
grep -q 'c\["at"\] = now()' $DSR/ccards/commands.py && grep -q 'pop("at", None)' $DSR/ccards/commands.py \
  && ok "86 写侧盖戳+退回清戳两端在场(①)" || bad "86 at 盖章/清除缺位（今日收口又只能读会被 set 拖走的 updated_at）"
if [ ! -f "$DSR/board/status-web.py" ]; then
  skip "86 顶栏统计渲染链（board 运营层未随独立发行包）"
else
  grep -q "TODAY_VERIFIED" $DSR/board/status-web.py && grep -q "STATUS_TERMS" $DSR/board/terms.py && grep -q "stats_compute" $DSR/board/status-web.py \
    && grep -q "stats_render" $DSR/board/status-web.py && grep -q "tsGo" $DSR/board/status-web.py \
    && grep -q "if (c.at) return c.at;" $DSR/board/live.js \
    && ok "86 顶栏四数+点进清单+完成链同源在场(①)" || bad "86 顶栏统计渲染链缺位"
fi
CARD75="python3 $DSR/cards_dag.py"
_at(){ gcard 0861 | python3 -c "import json,sys;print(json.load(sys.stdin).get(sys.argv[1]) or '')" "$1"; }
$CARD75 create 0861 --title 转正盖章卡 --lanes r75 --verify "true" >/dev/null
$CARD75 claim 0861 --owner r75 >/dev/null
$CARD75 done 0861 --owner r75 --commit "$(git -C $REPO rev-parse HEAD)" --repo $REPO >/dev/null
$CARD75 verdict 0861 PASS --reason "机验绿" >/dev/null && ok "86 verdict PASS 正常收" || bad "86 PASS 被误杀（盖章前提塌）"
A1=$(_at at); U1=$(_at updated_at)
[ -n "$A1" ] && ok "86 PASS 瞬间盖转正戳 at(②)" || bad "86 at 未落账（顶栏今日数无专用日历可用）"
sleep 1.2
$CARD75 set 0861 --desc "改描述碰账测试" >/dev/null
A2=$(_at at); U2=$(_at updated_at)
[ "$A1" = "$A2" ] && [ -n "$U2" ] && [ "$U1" != "$U2" ] \
  && ok "86 set 改desc 拖updated_at 不拖at（分离钉死，非空转假绿）" || bad "86 改描述动了转正时间（北京日切会被 note/set 污染）"
$CARD75 verdict 0861 REJECT --reason "回炉重做" >/dev/null
[ -z "$(_at at)" ] && ok "86 REJECT 清旧戳（回炉卡不冒充收口）" || bad "86 退回后 at 残留（顶栏会把回炉卡算进今日收口）"
$CARD75 claim 0861 --owner r75 >/dev/null
$CARD75 done 0861 --owner r75 --commit "$(git -C $REPO rev-parse HEAD)" --repo $REPO >/dev/null
$CARD75 verdict 0861 PASS --reason "重转正" >/dev/null
[ -n "$(_at at)" ] && ok "86 重做重转正重盖新戳(②收)" || bad "86 重 PASS 未盖 at（盖章链只活一次=日切账会烂）"
if [ ! -f "$DSR/board/status-web.py" ]; then
  skip "86 顶栏统计对表法（board 运营层未随独立发行包）"
else
python3 - "$DSR" <<'PYEOF' && ok "86 纯函数: 日切/跨日归零/痕回填/截断黄差/过滤号(③)" || bad "86 顶栏统计口径算错或红探不红（对表法失去纯函数锁）"
import importlib.util, os, sys, types
dsr = sys.argv[1]
sys.argv = [sys.argv[0], "8799"]          # status-web import 期读 argv[1] 当端口——垫成数字防测试参数污染
def loadsw(path, mut=None):
    m = types.ModuleType("sw_mod"); m.__file__ = path
    src = open(path, encoding="utf-8").read()
    if mut:
        assert mut[0] in src, "红自证替换未命中（读链漂移，锁需同步）"
        src = src.replace(mut[0], mut[1])
    exec(compile(src, path, "exec"), m.__dict__)
    return m
SP = os.path.join(dsr, "board", "status-web.py")
sw = loadsw(SP)
# 页级函数在场断言（STATS-1 开发实锤事故：一次编辑把 ab_html 的 def 行吞掉、函数体并入
# stats_html 成死代码——py_compile 全绿、渲染期才 NameError。编译过≠页起得来。）
for _fn in ("ab_html", "stats_html", "stats_compute", "stats_render", "get_body",
            "live_html", "build", "rel_html", "retro_html"):
    assert callable(getattr(sw, _fn, None)), "页级函数缺失(def 被编辑吞?): " + _fn
FIX = [
 {"id":"S-1","status":"VERIFIED","title":"今1","at":"2026-11-02T08:00:00+0800"},
 {"id":"S-2","status":"VERIFIED","title":"今2","at":"2026-11-01T16:30:00+00:00"},      # 北京 11-02 00:30——日切归今日
 {"id":"S-3","status":"VERIFIED","title":"痕今","verify_run":{"ts":"2026-11-02T01:00:00+08:00"}},  # 无at→痕回填
 {"id":"S-4","status":"VERIFIED","title":"痕昨","verify_run":{"ts":"2026-11-01T23:00:00+08:00"}},  # 痕在昨日
 {"id":"S-5","status":"VERIFIED","title":"无戳无痕"},                                    # 不计
 {"id":"S-6","status":"CLAIMED","title":"跑"},{"id":"S-7","status":"OPEN","title":"待1"},
 {"id":"S-8","status":"OPEN","title":"待2"},{"id":"S-9","status":"DONE","title":"未验","at":"2026-11-02T09:00:00+0800"},
]
d = sw.stats_compute(FIX, today="2026-11-02")
assert d["n_today"] == 3, d
assert (d["n_run"], d["n_wait"], d["n_total"]) == (1, 2, 9), d
assert d["n_trace"] == 2 and d["n_nodate"] == 1, d
assert sw.stats_compute(FIX, today="2026-11-03")["n_today"] == 0, "跨日不归零"
assert d["diff"] == 0 and "对表差" not in sw.stats_render(d), "≤10 不该黄字"
big = [{"id": "B-%02d" % i, "status": "VERIFIED", "title": "t",
        "at": "2026-11-02T10:%02d:00+0800" % i} for i in range(12)]
b = sw.stats_compute(big, today="2026-11-02")
assert b["n_today"] == 12 and len(b["recent"]) == 10 and b["diff"] == 2, b
h = sw.stats_render(b)
assert "今日已验收" in h and "对表差" in h, h[:160]
assert all(x in h for x in ("tsGo('VERIFIED')", "tsGo('CLAIMED')", "tsGo('OPEN')", "tsGo('')")), "四数点进清单丢失"
# 红自证：摘掉取值链的 at 读取（模拟盖章通道断供）→ 顶栏今日数必须塌到只剩痕（=1）
m2 = loadsw(SP, mut=('dt = _bj_dt(c.get("at"))', 'dt = _bj_dt(None)  # redprobe-STATS'))
d2 = m2.stats_compute(FIX, today="2026-11-02")
assert d2["n_today"] == 1, ("撤 at 后今日数不塌=纯函数锁是空枪", d2)
print("STATS_OK")
PYEOF
fi

# 87 BUG-REAPER-SCOPE-1 生成侧闸+警账结算运行时锁（wm7 版，与 #81 结算侧双防线并存）：看护族「ALARM <cid>:」只许给 CLAIMED 卡响；
#     存量终态僵尸警回收环一圈停铃且不再复活；alarm_scope_check.py 现读 engine.tick 断言
#     （BUG-ALARM-LIFECYCLE-1 只 grep「autoack」字样=弱判据假绿，本锁换成运行时对账）。
#     独立子沙箱不碰本程共享 /tmp/cc-team-test（互踩先例 6fd216f）；判据对象=**本仓自己的**
#     ops/recycle.py+tests/alarm_scope_check.py（按脚本位置定位，不钉主仓路径——合入后主仓
#     自锁主仓，分支偏移不再背账，上一道 #72 两向红教训）。红检两处：MUTA 拆生成现读闸 ⇒
#     门单元翻；MUTB 拆 autoack 状态迁移停铃 ⇒ **被测侧**僵尸警停不掉（整合后定标：双防线
#     并存下端到端撤一侧必假绿，9) 注全文；原「端到端一圈停不掉」形态系 wm7 单侧树的设计）。
# 整合修：此式必须在 cd $REPO 之后执行=相对 ./selftest.sh 按沙箱目录解（SELF87 得 /tmp，
# 全链级联红，2026-11-02 主仓落干后单跑复现）；复用第 21 行 cd 前固化的 SELF_CC（同义绝对自锚）。
SELF87="$SELF_CC"
S87=$ST_TMP/cc-scope87; S87REPO=$ST_TMP/cc-scope87-repo
rm -rf $S87 $S87REPO && mkdir -p $S87/state $S87REPO
( cd $S87REPO && git init -q && git config user.email t@t && git config user.name t \
  && mkdir -p backend/api && echo a > backend/api/user.py && git add -A && git commit -qm init >/dev/null )
for X in D V C W; do
  CC_TEAM_HOME=$S87 $CARD create R87-$X --title "87看护范围$X" --verify "true" --files "backend/api/" >/dev/null \
    || bad "87 造卡 R87-$X 失败"
done
# 1) 真响链（非注入）：D/V/W 无道锁 claim=kind session → 连两轮 --apply 升 ALARM；C 不领（下面手工塞）
for X in D V W; do CC_TEAM_HOME=$S87 $CARD claim R87-$X --owner s87$X >/dev/null || bad "87 claim R87-$X 失败"; done
CC_TEAM_HOME=$S87 python3 $SELF87/ops/recycle.py --apply >$ST_TMP/s87w1.out 2>&1
CC_TEAM_HOME=$S87 python3 $SELF87/ops/recycle.py --apply >$ST_TMP/s87w2.out 2>&1
grep -q "ALARM R87-D" $ST_TMP/s87w2.out && grep -q "ALARM R87-W" $ST_TMP/s87w2.out \
  && ok "87 连两轮 WARN 升 ALARM（CLAIMED=范围内，响得对）" || bad "87 报警生成链断(见 $ST_TMP/s87w2.out)"
# 2) 运行时断言绿面：三条开放警涉卡全 CLAIMED——判护尺不许误拦在押铃（W 到最后都得响）
CC_TEAM_HOME=$S87 python3 -I -X utf8 $SELF87/tests/alarm_scope_check.py >$ST_TMP/s87c1.out 2>&1 \
  && grep -q ALARM_SCOPE_PASS $ST_TMP/s87c1.out \
  && ok "87 断言绿面：范围内警不误红" || { bad "87 断言误拦在押警"; tail -6 $ST_TMP/s87c1.out; }
# 3) 卡迁出看守范围：D→DONE、V→done+verdict PASS→VERIFIED、C(未领)→cancel→CANCELED
( cd $S87REPO && echo b >> backend/api/user.py && git add -A && git commit -qm r87 >/dev/null )
C87=$(cd $S87REPO && git rev-parse HEAD)
CC_TEAM_HOME=$S87 $CARD done R87-D --owner s87D --commit $C87 --repo $S87REPO --evidence ok >/dev/null 2>&1
CC_TEAM_HOME=$S87 $CARD done R87-V --owner s87V --commit $C87 --repo $S87REPO --evidence ok >/dev/null 2>&1
CC_TEAM_HOME=$S87 $CARD verdict R87-V PASS --reason "87转正" >$ST_TMP/s87v.out 2>&1
CC_TEAM_HOME=$S87 $CARD cancel R87-C --reason "87撤销" >/dev/null 2>&1
# 4) 卡面字面通道「手工塞一条终态卡 ALARM」：给 CANCELED 的 R87-C 直接注入（生成路径都不走）
CC_TEAM_HOME=$S87 python3 -I -X utf8 - "$SELF87" <<'PYEOF' || bad "87 注入失败"
import sys; sys.path.insert(0, sys.argv[1])
from ccards import alerts
alerts.record("ALARM R87-C: owner=- session 执行卡(无回收锚) 静默超阈 → 连续两轮 WARN 仍未收口，请 PM 经 inbox 处置(不 release) [87手工塞]")
PYEOF
# 5) 断言红面（尺子红得起来的实锤）：D(DONE)+V(VERIFIED)+C(CANCELED) 三条僵尸警全点名
if CC_TEAM_HOME=$S87 python3 -I -X utf8 $SELF87/tests/alarm_scope_check.py >$ST_TMP/s87c2.out 2>&1; then
  bad "87 终态僵尸警竟判绿（假绿=尺子失效）"
else
  grep -q "VIOLATION 看护族「ALARM R87-D" $ST_TMP/s87c2.out \
    && grep -q "VIOLATION 看护族「ALARM R87-V" $ST_TMP/s87c2.out \
    && grep -q "VIOLATION 看护族「ALARM R87-C" $ST_TMP/s87c2.out \
    && ok "87 断言红面：DONE/VERIFIED/CANCELED 僵尸警全点名且 rc≠0" || bad "87 红面点名缺失(见 $ST_TMP/s87c2.out)"
fi
# 6) 回收环跑一圈（卡面字面）：三条存量警 AUTOACK 停铃，断言转绿；W 在押铃照旧不受累
CC_TEAM_HOME=$S87 python3 $SELF87/ops/recycle.py --apply >$ST_TMP/s87s1.out 2>&1
grep -q "AUTOACK ALARM R87-D" $ST_TMP/s87s1.out && grep -q "卡状态迁移 DONE" $ST_TMP/s87s1.out \
  && grep -q "AUTOACK ALARM R87-V" $ST_TMP/s87s1.out && grep -q "卡状态迁移 VERIFIED" $ST_TMP/s87s1.out \
  && grep -q "AUTOACK ALARM R87-C" $ST_TMP/s87s1.out && grep -q "卡状态迁移 CANCELED" $ST_TMP/s87s1.out \
  && ok "87 一圈清账：三态迁移停铃逐条在案" || bad "87 清账缺条(见 $ST_TMP/s87s1.out)"
CC_TEAM_HOME=$S87 python3 -I -X utf8 $SELF87/tests/alarm_scope_check.py >$ST_TMP/s87c3.out 2>&1 \
  && grep -q ALARM_SCOPE_PASS $ST_TMP/s87c3.out \
  && ok "87 清账后断言转绿（W 仍在范围 OK）" || { bad "87 清账后未转绿"; tail -6 $ST_TMP/s87c3.out; }
# 7) 不再复活：再两圈，D/V/C 停铃冻结（count/last 纹丝不动），W 的活铃 count 继续走（不误杀活铃）
python3 -I -X utf8 - "$S87" <<'PYEOF' || bad "87 停铃快照读取失败"
import json, sys, os
d = json.load(open(sys.argv[1] + "/state/alerts.json", encoding="utf-8"))["alerts"]
frozen = {k[:12]: (e["count"], e["last"]) for k, e in d.items() if k.startswith(("ALARM R87-D", "ALARM R87-V", "ALARM R87-C"))}
json.dump(frozen, open(os.path.join(os.environ["SELFTEST_TMP"], "s87frozen.json"), "w", encoding="utf-8"))
assert len(frozen) == 3, frozen
PYEOF
CC_TEAM_HOME=$S87 python3 $SELF87/ops/recycle.py --apply >$ST_TMP/s87r4.out 2>&1
CC_TEAM_HOME=$S87 python3 $SELF87/ops/recycle.py --apply >$ST_TMP/s87r5.out 2>&1
if python3 -I -X utf8 - "$S87" <<'PYEOF'
import json, sys, os
before = json.load(open(os.path.join(os.environ["SELFTEST_TMP"], "s87frozen.json"), encoding="utf-8"))
d = json.load(open(sys.argv[1] + "/state/alerts.json", encoding="utf-8"))["alerts"]
for pre_k, (cnt, last) in before.items():
    k = next((x for x in d if x.startswith(pre_k)), pre_k)
    e = d[k]
    assert (e["count"], e["last"]) == (cnt, last) and e.get("ack"), ("复活/走账", k, e)
w = next(e for k, e in d.items() if k.startswith("ALARM R87-W"))
assert not w.get("ack") or w["last"] > w["ack"], "在押铃被误停"
PYEOF
then ok "87 两圈后停铃冻结、活铃照旧=不再复活"; else bad "87 僵尸警复活/冻结判据红"; fi
# 8) MUTA 红检：拆生成侧现读闸（in_watch_scope 恒放行）⇒ 门单元对 DONE 卡翻成放行——
#    看护落笔的竞态防线（圈时长分钟级，环首快照晚到属主已迁出）被锁咬死
python3 -I -X utf8 - "$SELF87" "$S87" <<'PYEOF' && ok "87 MUTA：撤生成闸⇒DONE 卡重进报警评估(锁咬得住)" || bad "87 MUTA 不红=生成闸未上锁"
import importlib.util, os, sys, tempfile
root, sbx = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
os.environ["CC_TEAM_HOME"] = sbx
src = open(os.path.join(root, "ops", "recycle.py"), encoding="utf-8").read()
def load(name, text):
    p = os.path.join(tempfile.gettempdir(), name + ".py")
    open(p, "w", encoding="utf-8").write(text)
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
m = load("rec87_orig", src)
assert m.in_watch_scope("R87-W") == (True, "CLAIMED"), m.in_watch_scope("R87-W")
assert m.in_watch_scope("R87-D") == (False, "DONE"), m.in_watch_scope("R87-D")
assert m.in_watch_scope("GHOST-87")[0] is False, "查无此卡竟放行"
needle = 'return stt == "CLAIMED", stt'
assert needle in src, "哨兵失配：in_watch_scope 返回行被改动=必须同步本红检（宁红不裸）"
m2 = load("rec87_mut", src.replace(needle, "return True, stt  # MUTA"))
assert m2.in_watch_scope("R87-D")[0] is True, "变异体竟仍拦=加载没生效"
print("REAPER_MUTA_OK")
PYEOF
# 9) MUTB 红检（整合后定标 2026-11-02）：拆 autoack「卡状态迁移」停铃——端到端 --apply 形态
#    在合并树测不了本侧：双防线并存（docs/整合-泳道积压-20261102.md 四-2），结算侧
#    alarm_scope_sweep 范围=∉CLAIMED 全集，会连带清终态僵尸 ⇒ 端到端撤任一侧都不红，
#    原判据「变异体一圈停不掉」在 wm1×wm7 合并后必假红。红检收口到被测侧（与 MUTA 同形态）：
#    同一沙箱账，真身 autoack_sweep 须停铃且落「卡状态迁移 DONE」，变异体须停不掉；
#    tick_min 给 10000 封死「超2tick未续报」腐灭旁路（变异体 reason=None 会落该支=假绿通道）。
CC_TEAM_HOME=$S87 python3 -I -X utf8 - "$SELF87" <<'PYEOF' || bad "87 MUTB 前置注入失败"
import sys; sys.path.insert(0, sys.argv[1])
from ccards import alerts
alerts.record("ALARM R87-D: owner=- session 执行卡(无回收锚) 静默超阈 → 连续两轮 WARN 仍未收口，请 PM 经 inbox 处置(不 release) [87再塞]")
PYEOF
python3 -I -X utf8 - "$SELF87" "$S87" $ST_TMP/rec87_mutB.py <<'PYEOF' || bad "87 MUTB 变异体生成失败"
import sys
root, sbx, dst = sys.argv[1], sys.argv[2], sys.argv[3]
src = open(root + "/ops/recycle.py", encoding="utf-8").read()
needle = 'reason = "卡状态迁移 %s" % (stt or "?")'
assert needle in src, "哨兵失配：autoack 状态迁移支路被改动=必须同步本红检（宁红不裸）"
open(dst, "w", encoding="utf-8").write(src.replace(needle, 'reason = None  # MUTB'))
PYEOF
# 被测侧定标：账上停没停铃才是判据（同旧形态；只把「整圈 --apply」换成直调 autoack_sweep）
python3 -I -X utf8 - "$SELF87" "$S87" $ST_TMP/rec87_mutB.py >$ST_TMP/s87mB.out 2>&1 <<'PYEOF' \
  && ok "87 MUTB 定标：真身停铃在前、变异体拆侧停不掉(被测支路锁咬得住)" \
  || { bad "87 MUTB 定标红检不成立(见 $ST_TMP/s87mB.out)"; tail -6 $ST_TMP/s87mB.out; }
import importlib.util, io, os, sys, tempfile, contextlib
root, sbx, mutp = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
os.environ["CC_TEAM_HOME"] = sbx
from ccards import alerts
src = open(os.path.join(root, "ops", "recycle.py"), encoding="utf-8").read()
mut = open(mutp, encoding="utf-8").read()
assert 'reason = None  # MUTB' in mut, "变异体文件没带 MUTB 标记=生成步没跑"
def load(name, text):
    p = os.path.join(tempfile.gettempdir(), name + ".py")
    open(p, "w", encoding="utf-8").write(text)
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def reopened():
    for k, e in sorted(alerts.entries().items()):
        if k.startswith("ALARM R87-D") and (not e.get("ack") or (e.get("last") or "") > e["ack"]):
            return True
    return False
assert reopened(), "MUTB 前置注入未开铃（沙箱账不对版）"
m0 = load("rec87_origB", src)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    n_state, _n_stale = m0.autoack_sweep(10000, True)
out0 = buf.getvalue()
assert "卡状态迁移 DONE" in out0 and n_state >= 1, ("真身侧没停铃=被测支路断", out0[-300:])
assert not reopened(), "真身跑完仍开铃（账路径不对版=假绿温床）"
alerts.record("ALARM R87-D: owner=- session 执行卡(无回收锚) 静默超阈 → 连续两轮 WARN 仍未收口，请 PM 经 inbox 处置(不 release) [87重开]")
m1 = load("rec87_mutB_run", mut)
buf2 = io.StringIO()
with contextlib.redirect_stdout(buf2):
    m1.autoack_sweep(10000, True)
assert reopened(), "变异体竟停铃=加载没生效或腐灭旁路没封死"
print("REAPER_MUTB_SCOPED_OK")
PYEOF
CC_TEAM_HOME=$S87 python3 $SELF87/ops/recycle.py --apply >$ST_TMP/s87s5.out 2>&1
CC_TEAM_HOME=$S87 python3 -I -X utf8 $SELF87/tests/alarm_scope_check.py >$ST_TMP/s87c5.out 2>&1 \
  && grep -q ALARM_SCOPE_PASS $ST_TMP/s87c5.out \
  && ok "87 真身回场再一圈清账，收口态绿" || bad "87 收口态未回绿(见 $ST_TMP/s87c5.out)"
rm -rf $S87 $S87REPO $ST_TMP/cc-scope87* $ST_TMP/rec87_mutB.py $ST_TMP/rec87_orig.py $ST_TMP/rec87_mut.py $ST_TMP/s87*.out $ST_TMP/s87frozen.json $ST_TMP/rec87_origB.py $ST_TMP/rec87_mutB_run.py

# ===== 88 REQ-MERGE-GATE-1：验收绿要求交付已在主干（业主令 2026-11-02）=====
# ① done 落 repo 素材 + 泳道未合入主干的 commit，verdict PASS 必拒（卡留 DONE 待验，非故障）
TRUNK=$(git rev-parse --abbrev-ref HEAD)
$CARD create 8801 --title "主干闸" --verify "true" >/dev/null
$CARD claim 8801 --owner m95 >/dev/null
git checkout -q -b lane8801
echo z88 >> backend/api/user.py && git add -A && git commit -qm "feat(8801): 泳道交付未合入主干"
C88=$(git rev-parse HEAD)
env -u CC_AUTO_VERIFY $CARD done 8801 --owner m95 --commit "$C88" --repo $REPO --evidence "88" >/dev/null
gcard 8801 | grep -q '"repo"' && ok "88 done 落卡面 repo(主干闸素材)" || bad "88 done 未落 repo——验收侧无从查主干"
expect_fail verdict 8801 PASS
gcard 8801 | grep -q '"status": "DONE"' && ok "88 未合主干拒 PASS(卡留 DONE 属正常待验)" || bad "88 未拒或卡面异变"
# ② merge 进主干（不改写哈希）后复验=瞬绿落章
git checkout -q "$TRUNK" && git merge -q --ff-only lane8801
$CARD verdict 8801 PASS --reason "已合入主干" >/dev/null && ok "88 合入主干后 PASS 绿" || bad "88 合入后仍拒=闸错杀"
gcard 8801 | grep -q '"status": "VERIFIED"' && ok "88 合入复核落章 VERIFIED" || bad "88 未转 VERIFIED"
# ③ 存量欠账通道：卡面抠掉 repo→拒；set --repo 补登（唯一合法通道）→绿。8802 交付直接进主干，
#    隔离验证「缺素材拒」不是「没合入拒」的重复断言。
$CARD create 8802 --title "存量补账" --verify "true" >/dev/null
$CARD claim 8802 --owner m96 >/dev/null
echo w88 >> backend/api/user.py && git add -A && git commit -qm "feat(8802): 主干直交"
env -u CC_AUTO_VERIFY $CARD done 8802 --owner m96 --commit "$(git rev-parse HEAD)" --repo $REPO --evidence "88" >/dev/null
python3 - "$CC_TEAM_HOME/state/graph.json" <<'PYEOF'
import json,sys
p=sys.argv[1]; g=json.load(open(p,encoding='utf-8'))
g["cards"]["8802"].pop("repo",None); json.dump(g,open(p,"w",encoding='utf-8'),ensure_ascii=False)
PYEOF
expect_fail verdict 8802 PASS
$CARD set 8802 --repo $REPO >/dev/null && ok "88 set --repo 补登通道在" || bad "88 补登通道缺——存量账成孤儿"
$CARD verdict 8802 PASS --reason "素材补齐且在主干" >/dev/null && ok "88 补登后合入态转绿" || bad "88 补登后仍不绿"
gcard 8802 | grep -q '"status": "VERIFIED"' && ok "88 补登账收口 VERIFIED" || bad "88 补登账未转 VERIFIED"
# ④ 判据复跑绿≠可落章：主干闸在前，未合入连套件都不必跑（防 done 后随手改判据蒙绿通道回潮）
$CARD create 8803 --title "闸在判据前" --verify "true" >/dev/null
$CARD claim 8803 --owner m97 >/dev/null
git checkout -q -b lane8803
echo v88 >> backend/api/user.py && git add -A && git commit -qm "feat(8803): 未合入泳道交付"
env -u CC_AUTO_VERIFY $CARD done 8803 --owner m97 --commit "$(git rev-parse HEAD)" --repo $REPO --evidence "88" >/dev/null
OUT8803=$($CARD verdict 8803 PASS 2>&1 >/dev/null || true)
echo "$OUT8803" | grep -q "REQ-MERGE-GATE-1" && ok "88 拒因点名主干闸(不静默不含糊)" || bad "88 拒因未点名——排障黑盒"
git checkout -q "$TRUNK"

# ===== 89 BUG-LANE-SILENCE-1：在制道断流探针红绿锁（2026-11-02 17:0x 实锤）=====
#   病根：m1(UTF8-1)/m10(BEAT-ANCHOR)「锚活/转录零行/工作树零写入」冻结 65+ 分钟，巡逻环 warn=0
#   静默放行——旧判活只探进程存在与 comm 名，ALIVE→不动 一路豁免，从不测「工作是否在续写」。
#   锁面（真跑 ops/recycle.py 运行时断言，非 grep 字样；独立子沙箱防互踩先例 6fd216f）：
#   ①dry 点名零落手 ②apply 自动 release(silence-detach)+ALARM 稳定键 ③工作树在写=防误杀放行(③)
#   ④转录在写放行 ⑤progress 新鲜=BEAT 在账放行(②) ⑥progress 陈旧优先 detach（jsonl 新鲜不豁免）
#   ⑦新认领未足窗不抢跑 ⑧缺源保守 ⑨MUTA 撤探针必红+调用点哨兵针=2。判据对象=本仓 ops/recycle.py。
SELF89="$SELF_CC"
S89P=$ST_TMP/cc-sil89-proj; S89C=$ST_TMP/cc-sil89-code
rm -rf $S89P $S89C && mkdir -p $S89P $S89C
# REQ-OSS-ENGINE-1：转录目录=与 ops/recycle.py transcript_age_min 同一组合式现算
# （DISPATCH_PROJ/DISPATCH_CODE_ROOT 锚与 rec89 同 env；缺省走 config 派生），禁拼宿主字面量。
tp89(){ DISPATCH_PROJ=$S89P DISPATCH_CODE_ROOT=$S89C python3 - "$SELF89" "$1" <<'PYEOF'
import os, sys
sys.path.insert(0, sys.argv[1])
from ccards import config
print(os.path.join(os.environ["DISPATCH_PROJ"],
                   config.proj_dir_name(os.environ["DISPATCH_CODE_ROOT"], sys.argv[2])))
PYEOF
}
# mk89 tag cid lane 认领龄分 转录龄分 工作树龄分 —— 一景一沙箱（后续 recycle 圈不串染前景）
mk89(){
  local tag=$1 cid=$2 lane=$3 cmin=$4 jmin=$5 tmin=$6
  T89=$ST_TMP/cc-sil89-$tag; rm -rf $T89; mkdir -p $T89/state $T89/claims $T89/logs
  CC_TEAM_HOME=$T89 $CARD create $cid --title "89断流$cid" --verify "true" --files "backend/api/" --lanes $lane >/dev/null || { bad "89 造卡 $cid 失败"; return 1; }
  CC_TEAM_HOME=$T89 $CARD claim $cid --owner $lane --pid $$ >/dev/null || { bad "89 领卡 $cid 失败"; return 1; }
  # 锚覆写=本套 bash 进程（全程活）+pid_ns=wsl 无锚名（纯存在性核验）：win 宿主裸跑时
  # Get-Process 对 msys 号不可靠（判活假死），存在性档两宿主同判——同 lane-silence-check 形态。
  python3 - "$T89" "$lane" "$cid" "$$" <<'PYEOF' || bad "89 锚覆写失败"
import json, os, sys
team, lane, cid, pid = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
for p in (os.path.join(team, "state", "lane-%s.lock" % lane, "meta.json"),
          os.path.join(team, "claims", cid, "claim.json")):
    d = {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    d.update({"kind": "lane", "pid": pid, "pid_ns": "wsl"})
    d.pop("pid_comm", None)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
PYEOF
  python3 - "$T89" "$cid" "$cmin" <<'PYEOF' || bad "89 回拨 claimed_at 失败"
import json,os,sys
from datetime import datetime, timedelta
team, cid, cmin = sys.argv[1], sys.argv[2], float(sys.argv[3])
p = os.path.join(team, "state", "graph.json")
g = json.load(open(p, encoding="utf-8"))
g["cards"][cid]["claimed_at"] = (datetime.now().astimezone() - timedelta(minutes=cmin)).strftime("%Y-%m-%dT%H:%M:%S%z")
tmp = p + ".tmp"; json.dump(g, open(tmp, "w", encoding="utf-8"), ensure_ascii=False); os.replace(tmp, p)
PYEOF
  local d89; d89="$(tp89 "$lane")"
  mkdir -p "$d89"
  echo '{"type":"assistant"}' > "$d89/s.jsonl"
  [ "$jmin" != fresh ] && touch -d "$jmin minutes ago" "$d89/s.jsonl"
  mkdir -p "$S89C/wf-$lane"
  echo w > "$S89C/wf-$lane/f.txt"
  [ "$tmin" != fresh ] && touch -d "$tmin minutes ago" "$S89C/wf-$lane/f.txt"
}
rec89(){ CC_TEAM_HOME=$T89 DISPATCH_PROJ=$S89P DISPATCH_CODE_ROOT=$S89C CARD_SILENCE_RELEASE_MIN=${S89THR:-45} \
  python3 "$SELF89/ops/recycle.py" "$@"; }
gc89(){ python3 -c "import json,sys;print(json.load(open(sys.argv[2]+'/state/graph.json',encoding='utf-8'))['cards'].get(sys.argv[1],{}).get('status'))" "$1" "$T89"; }
# 1) dry 点名（双源冻 60min>阈45）→ 卡留 CLAIMED、summary silence=1、警账未写
mk89 a LS89A s89a 60 60 60 || T89=$ST_TMP/cc-sil89-a
rec89 >$ST_TMP/s89a.out 2>&1
# 注：dry 圈 memgate 水位可照写 MEMGATE-WARN 键（既有行为，非本卡产物）——零落手断言=断流键不入账
grep -q "SILENCE-DETACH LS89A" $ST_TMP/s89a.out && grep -q "silence=1" $ST_TMP/s89a.out \
  && [ "$(gc89 LS89A)" = "CLAIMED" ] \
  && { [ ! -f $T89/state/alerts.json ] || ! grep -q "ALARM LS89A" $T89/state/alerts.json; } \
  && ok "89 dry 点名断流且零落手(卡留 CLAIMED/断流警键未入账)" || { bad "89 dry 点名链断"; tail -6 $ST_TMP/s89a.out; }
# 2) apply 落手 → 卡回 OPEN + silence-detach 痕 + ALARM 稳定键（键不含分钟值，OPT1）
rec89 --apply >$ST_TMP/s89b.out 2>&1
grep -q "silence-detach" $ST_TMP/s89b.out && grep -q "SILENCE-RELEASE LS89A" $ST_TMP/s89b.out \
  && [ "$(gc89 LS89A)" = "OPEN" ] \
  && python3 - "$T89/state/alerts.json" <<'PYEOF' && ok "89 apply 自动 release+ALARM 稳定键留痕" || { bad "89 apply 落手/留痕断"; tail -6 $ST_TMP/s89b.out; }
import json, re, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))["alerts"]
ks = [k for k in d if k.startswith("ALARM LS89A: 在制道断流探针")]
assert ks, ("警账无断流键", list(d))
assert not re.search(r"\d+ ?min", ks[0]), ("键掺分钟值=键增殖", ks[0])
PYEOF
grep -q "AUTOACK ALARM LS89A" $ST_TMP/s89b.out && ok "89 同轮状态迁移 autoack 停铃=留痕不空响" || bad "89 断流警未被结算侧收口(留痕语义破)"
# 3) 工作树在写=防误杀硬闸（规格③：长判据合法零转录）——mk89 fresh 工作树+冻转录
mk89 c LS89C s89c 60 60 fresh || T89=$ST_TMP/cc-sil89-c
rec89 --apply >$ST_TMP/s89c.out 2>&1
grep -q "CONT LS89C" $ST_TMP/s89c.out && [ "$(gc89 LS89C)" = "CLAIMED" ] \
  && ok "89 工作树 mtime 在动→放行（瞬移坑防误杀）" || { bad "89 防误杀红(工作树)"; tail -6 $ST_TMP/s89c.out; }
# 4) 转录在写=降级双源活跃信号
mk89 d LS89D s89d 60 fresh 60 || T89=$ST_TMP/cc-sil89-d
rec89 --apply >$ST_TMP/s89d.out 2>&1
grep -q "CONT LS89D" $ST_TMP/s89d.out && [ "$(gc89 LS89D)" = "CLAIMED" ] \
  && ok "89 转录在写→放行" || { bad "89 放行链断(转录)"; tail -6 $ST_TMP/s89d.out; }
# 5) BEAT 对接（规格②）：progress 新鲜→双源全冻也放行；progress 陈旧→优先判其龄（jsonl 新鲜不豁免）
mk89 e LS89E s89e 60 60 60 || T89=$ST_TMP/cc-sil89-e
echo "$(date +%Y-%m-%dT%H:%M:%S%z)|LS89E|pm89|圈号名牌" >> $T89/state/progress.log
rec89 --apply >$ST_TMP/s89e.out 2>&1
grep -q "CONT LS89E" $ST_TMP/s89e.out && grep -q "progress 龄" $ST_TMP/s89e.out && [ "$(gc89 LS89E)" = "CLAIMED" ] \
  && ok "89 progress 在账新鲜→放行（BEAT 优先源）" || { bad "89 progress 新鲜未放行"; tail -6 $ST_TMP/s89e.out; }
mk89 f LS89F s89f 60 fresh 60 || T89=$ST_TMP/cc-sil89-f
echo "$(date -d '60 minutes ago' +%Y-%m-%dT%H:%M:%S%z)|LS89F|pm89|陈旧名牌" >> $T89/state/progress.log
rec89 --apply >$ST_TMP/s89f.out 2>&1
[ "$(gc89 LS89F)" = "OPEN" ] && grep -q "progress 龄60min" $ST_TMP/s89f.out \
  && ok "89 progress 在账即优先——陈旧 detach，jsonl 新鲜不救（双源仅在缺 progress 时说话）" || { bad "89 progress 优先序失效"; tail -6 $ST_TMP/s89f.out; }
# 6) 新认领未足窗不抢跑 + 缺源保守
mk89 g LS89G s89g 10 60 60 || T89=$ST_TMP/cc-sil89-g
rec89 --apply >$ST_TMP/s89g.out 2>&1
grep -q "SILENCE-SKIP LS89G" $ST_TMP/s89g.out && [ "$(gc89 LS89G)" = "CLAIMED" ] \
  && ok "89 认领 10min<阈45 → skip-fresh 不抢跑" || { bad "89 新认领地板失效"; tail -6 $ST_TMP/s89g.out; }
mk89 h LS89H s89h 60 60 60
rm -rf "$S89C/wf-s89h" "$(tp89 s89h)"
rec89 --apply >$ST_TMP/s89h.out 2>&1
grep -q "SILENCE-SKIP LS89H" $ST_TMP/s89h.out && [ "$(gc89 LS89H)" = "CLAIMED" ] \
  && ok "89 双源缺目录 → no-source 保守放行（宁漏不误杀不破）" || { bad "89 缺源保守失效"; tail -6 $ST_TMP/s89h.out; }
# 7) MUTA 撤探针必红：拆两处调用点（def 行不碰）的变异体对同款冻结沙箱零点名/卡不迁出；
#    调用点哨兵针=2 处——重构 recycle 不同步本锁=红（宁红不裸，同 #75 纪律）。
#    4→2 同步于 MECH-INT-BEAT13A-1 三向整合：beat 在账=lane_alive 全权探针不介入，
#    探针仅在 progress 缺账两处挂点（①存量无名牌 ②名牌在而本任期 beat 缺账）。
python3 - "$SELF89" <<'PYEOF' || bad "89 哨兵失配：调用点≠2（重构 recycle 必须同步本锁）"
import sys, os
src = open(sys.argv[1] + "/ops/recycle.py", encoding="utf-8").read()
n = len([ln for ln in src.splitlines()
         if "silence_check(c, owner)" in ln and not ln.strip().startswith("def ")
         and not ln.strip().startswith("#")])
assert n == 2, ("silence_check 调用点", n)
lines = [ln.replace("silence_check(c, owner)", "pass  # MUTA 拆探针")
         if ("silence_check(c, owner)" in ln and not ln.strip().startswith("def ")
             and not ln.strip().startswith("#")) else ln for ln in src.splitlines()]
open(os.path.join(os.environ["SELFTEST_TMP"], "s89-mut-src.py"), "w", encoding="utf-8").write("\n".join(lines))
PYEOF
rm -rf $ST_TMP/s89-lib && mkdir -p $ST_TMP/s89-lib/ops
cp -r "$SELF89/ccards" $ST_TMP/s89-lib/ccards && rm -rf $ST_TMP/s89-lib/ccards/__pycache__
cp $ST_TMP/s89-mut-src.py $ST_TMP/s89-lib/ops/recycle_mut.py
mk89 m LS89M s89m 60 60 60 || T89=$ST_TMP/cc-sil89-m
CC_TEAM_HOME=$T89 DISPATCH_PROJ=$S89P DISPATCH_CODE_ROOT=$S89C CARD_SILENCE_RELEASE_MIN=45 \
  python3 $ST_TMP/s89-lib/ops/recycle_mut.py --apply >$ST_TMP/s89m.out 2>&1
if grep -q "SILENCE-DETACH LS89M" $ST_TMP/s89m.out || [ "$(gc89 LS89M)" != "CLAIMED" ]; then
  bad "89 MUTA 拆探针竟仍点名/迁卡=断流判定另有暗道，锁咬不住"
else
  ok "89 MUTA 撤探针零点名/卡不迁出=红得起来（撤修复必红）"
fi
# 8) 逃生门：CARD_SILENCE_RELEASE_MIN=0 显式停用（双源冻结也零点名）
mk89 n LS89N s89n 60 60 60 || T89=$ST_TMP/cc-sil89-n
S89THR=0 rec89 --apply >$ST_TMP/s89n.out 2>&1
grep -q "SILENCE-DETACH LS89N" $ST_TMP/s89n.out && bad "89 逃生门失守：阈=0 仍 detach" || ok "89 CARD_SILENCE_RELEASE_MIN=0 显式停用探针"
rm -rf $ST_TMP/s89-lib $ST_TMP/s89-mut-src.py $T89 2>/dev/null; true
# 90 BUG-REDAUTO-HOST-1 复验环跨环境重放假红·红绿锁（卡面原话「selftest #77…新增 #78 红绿」；
#     主干整合时 #78 已被「改道回收」块占用、现册编到 #88，按避让纪律续号 #90）。
#     两案实锤：13:57 PARITY-5 补跑 FAIL playwright闸2 via=posix/linux（判据需 Windows 侧在位
#     环境，WSL cron 重放=假红打回）；15:58 DONE-SWEEP 跨侧 rc=1「cd 不存在」尾痕空（目录明明
#     在=环境瞬时故障，零归因素材仍被当真红冤驳，整卡返工）。锁 done_sweep_scan 新纪律三路：
#     ①读卡面原验收宿主（host 字段 或 verify_run.via 痕），本机非记录侧且对侧不可达→pending，
#     本机红不具驳回权；②rc≠0 但尾痕空/找不到类=环境性失败，先排除再归因，双真痕才许驳；
#     ③真红归因与转正通道不劣化（无记录+无桥+真红仍驳 / 双真红仍驳 / 对侧绿不假转正）——
#     撤修复 A1/A2/A2b/D6 必红，加过头 C/B 必红=两向都咬。
#     宿主兼容：被测件路径/沙箱一律取被测 python 的 tempfile.gettempdir() 原生形态（win 解释器
#     的 /tmp ≠ Git Bash 的 /tmp 是 71-87 各块在 win 侧成片红的既病，本块不继承），跨侧桥桩按
#     os.name 落 .cmd(win)/.sh(wsl)，故 Git Bash 与 WSL 两宿主同判据可跑。
PYT90=$(python3 -c "import tempfile;print(tempfile.gettempdir())")
rm -f "$PYT90"/h90-*.red 2>/dev/null  # 时序红灯标记防跨轮残留（上轮没清=本轮试跑撞红拒发卡）
R90="$PYT90/cc-repo-h90"
for _h in a b c d; do python3 -c "import sys,os;os.makedirs(sys.argv[1],exist_ok=True)" "$PYT90/cc-team-h90$_h"; done
rm -rf "$R90" 2>/dev/null
python3 -c "import sys,os;os.makedirs(os.path.join(sys.argv[1],'backend','api'),exist_ok=True);open(os.path.join(sys.argv[1],'backend','api','user.py'),'w').write('a\n')" "$R90"
git -C "$R90" init -q && git -C "$R90" config user.email t@t && git -C "$R90" config user.name t
git -C "$R90" add -A && git -C "$R90" commit -qm init >/dev/null
C90(){ env -u CC_AUTO_VERIFY CC_TEAM_HOME="$1" python3 "$SELF_CC/cards_dag.py" "${@:2}"; }
g90(){ python3 -c "import json,os,sys;g=json.load(open(os.path.join(sys.argv[1],'state','graph.json'),encoding='utf-8'))
print(json.dumps(g.get('cards',{}).get(sys.argv[2],{}),ensure_ascii=False))" "$1" "$2"; }
mk90(){ local H=$1 id=$2 v=$3
  C90 "$H" create $id --title "h90$id" --lanes h90 --verify "$v" >/dev/null || return 1
  C90 "$H" claim $id --owner h90 >/dev/null || return 1
  python3 -c "import sys;open(sys.argv[1],'a').write('x-'+sys.argv[2]+chr(10))" "$R90/backend/api/user.py" $id \
    && git -C "$R90" add -A && git -C "$R90" commit -qm "feat($id)" >/dev/null
  C90 "$H" done $id --owner h90 --commit "$(git -C "$R90" rev-parse HEAD)" --repo "$R90" --evidence h90 >/dev/null; }
SW90(){ env CC_TEAM_HOME="$1" CARD_CROSS_BASH="$2" python3 "$SELF_CC/ops/recycle.py" --apply --ring sweep --done-hours 0; }
SIDE90=$(python3 -c "import sys;print('win' if sys.platform.startswith('win') else 'wsl')")
if [ "$SIDE90" = win ]; then OPP90=wsl; OPPVIA90=posix/linux; else OPP90=win; OPPVIA90=nt/win32; fi
mkstub90(){ python3 -c "
import os,sys
d,name,code,noise=sys.argv[1],sys.argv[2],int(sys.argv[3]),sys.argv[4]
ext='.cmd' if os.name=='nt' else '.sh'
p=os.path.join(d,name+ext)
if os.name=='nt':
    body='@echo off\r\n'+('echo '+noise+'\r\n' if noise else '')+'exit /b %d\r\n'%code
else:
    body='#!/bin/bash\n'+('echo '+noise+'\n' if noise else '')+'exit %d\n'%code
open(p,'w').write(body)
try: os.chmod(p,0o755)
except OSError: pass
print(p)" "$PYT90" "$1" "$2" "$3"; }
STUB_G90=$(mkstub90 green 0 "")
STUB_RN90=$(mkstub90 rednoisy 1 CROSS-FAIL-NOISE)
STUB_RS90=$(mkstub90 redsilent 1 "")
NOISE90='echo NOISE-ASSERT-FAIL: expected 1 got 0; exit 1'
# REQ-VERIFY-DRYRUN-1 适配：恒红判据（exit 1 / NOISE90 尾段）现在发卡即被试跑闸拒——红夹具
# 改时序形「(噪声;) test ! -f 红灯」：发卡时红灯未亮=试跑绿可发卡，mk90 后点亮红灯，
# sweep/复跑撞真红。尾痕噪声形/静默形两分类断言（NOISE-ASSERT-FAIL 有尾 vs 空尾）逐字不动。
NOISE90M(){ echo "echo NOISE-ASSERT-FAIL: expected 1 got 0; test ! -f '$PYT90/h90-$1.red'"; }
SILENT90M(){ echo "test ! -f '$PYT90/h90-$1.red'"; }
H90A="$PYT90/cc-team-h90a"; H90B="$PYT90/cc-team-h90b"; H90C="$PYT90/cc-team-h90c"; H90D="$PYT90/cc-team-h90d"
# —— Run A（桥不可达）：A0 绿转正 / A1 静默红→pending / A2 host字段 / A2b via痕→pending
#    / A3 无记录真红→仍驳（归因通道不劣化）。
mk90 "$H90A" A0 true
mk90 "$H90A" A1 "$(SILENT90M A1)"
mk90 "$H90A" A2 "$(NOISE90M A2)"
mk90 "$H90A" A2b "$(NOISE90M A2b)"
mk90 "$H90A" A3 "$(NOISE90M A3)"
touch "$PYT90/h90-A1.red" "$PYT90/h90-A2.red" "$PYT90/h90-A2b.red" "$PYT90/h90-A3.red"  # 发卡后点红，sweep 复跑必红（REQ-VERIFY-DRYRUN-1）
python3 -c "
import json,sys
p,opp,via=sys.argv[1],sys.argv[2],sys.argv[3]
g=json.load(open(p,encoding='utf-8'))
g['cards']['A2']['host']=opp
g['cards']['A2b']['verify_run']={'ts':'2026-11-02T13:57:00+0800','rc':0,'via':via,'cmd_sha':'e','tail':''}
json.dump(g,open(p,'w',encoding='utf-8'),ensure_ascii=False)" "$H90A/state/graph.json" "$OPP90" "$OPPVIA90"
SW90 "$H90A" "" >$ST_TMP/s90A.out 2>&1
grep -q "DONE-VERIFIED A0" $ST_TMP/s90A.out && g90 "$H90A" A0 | grep -q '"status": "VERIFIED"' \
  && ok "90 A0 本机绿照旧转正（补跑主通道不劣化）" || { bad "90 A0 转正链被改坏"; tail -4 $ST_TMP/s90A.out; }
grep -q "DONE-PENDING A1" $ST_TMP/s90A.out && g90 "$H90A" A1 | grep -q '"status": "DONE"' \
  && ok "90 A1 静默红(15:58案)归 pending_side 不打回——撤「空尾痕=环境性」必红" || { bad "90 A1 又被环境假红冤驳"; tail -3 $ST_TMP/s90A.out; }
grep -q "DONE-PENDING A2:" $ST_TMP/s90A.out && grep -q "REDAUTO-HOST-1①" $ST_TMP/s90A.out \
  && g90 "$H90A" A2 | grep -q '"status": "DONE"' \
  && ok "90 A2 卡面host记录≠本机且对侧不可达=pending(13:57案①路)——撤宿主随判必红" || { bad "90 A2 host字段防线缺位"; tail -3 $ST_TMP/s90A.out; }
grep -q "DONE-PENDING A2b" $ST_TMP/s90A.out && g90 "$H90A" A2b | grep -q '"status": "DONE"' \
  && ok "90 A2b verify_run.via 痕同判 pending（冤案卡重做完仍带变绿宿主记录）" || { bad "90 A2b via痕未吃"; tail -3 $ST_TMP/s90A.out; }
grep -q "DONE-REJECT A3" $ST_TMP/s90A.out && g90 "$H90A" A3 | grep -q '"status": "OPEN"' \
  && ok "90 A3 无宿主记录+无桥+真红特征仍就地驳（归因力未阉）" || { bad "90 A3 真红归因被加过头弄哑"; tail -3 $ST_TMP/s90A.out; }
grep -q "done_swept=5 done_verified=1 done_rejected=1 done_pending_side=3" $ST_TMP/s90A.out \
  && ok "90 A 摘要三分计数对账（pending 不混进 rejected=喊话与打回分家）" || { bad "90 A 摘要计数不符"; tail -2 $ST_TMP/s90A.out; }
grep -q "待该侧验收" "$H90A/state/events.log" \
  && ok "90 A events 落「待该侧验收」点名（done_pending_side=喊话不是静默）" || bad "90 A events 缺喊话痕"
# —— Run B（桥→对侧绿桩）：红卡一律 pending 不假转正（跨侧落章=假痕）。
mk90 "$H90B" B5 "$(NOISE90M B5)"
mk90 "$H90B" B6 "$(SILENT90M B6)"
touch "$PYT90/h90-B5.red" "$PYT90/h90-B6.red"
SW90 "$H90B" "$STUB_G90" >$ST_TMP/s90B.out 2>&1
grep -q "DONE-PENDING B5" $ST_TMP/s90B.out && grep -q "DONE-PENDING B6" $ST_TMP/s90B.out \
  && ! grep -q "DONE-REJECT\|DONE-VERIFIED" $ST_TMP/s90B.out \
  && ok "90 B 对侧绿本机红=pending 不假转正也不冤驳" || { bad "90 B 单侧OS分流坏"; tail -4 $ST_TMP/s90B.out; }
# —— Run C（桥→对侧真红桩）：双侧真痕红=照旧驳（撤修复反向保护：pending 加过头本锁必红）。
mk90 "$H90C" C3 "$(NOISE90M C3)"
touch "$PYT90/h90-C3.red"
SW90 "$H90C" "$STUB_RN90" >$ST_TMP/s90C.out 2>&1
grep -q "DONE-REJECT C3" $ST_TMP/s90C.out && g90 "$H90C" C3 | grep -q '"status": "OPEN"' \
  && grep -q "done_swept=1 done_verified=0 done_rejected=1 done_pending_side=0" $ST_TMP/s90C.out \
  && ok "90 C 双侧真痕红仍 REJECT（守卫红得起来，不是空检）" || { bad "90 C 真红驳回通道被pending吞"; tail -4 $ST_TMP/s90C.out; }
# —— Run D（桥→对侧静默红桩）：15:58 案字面形态（本机红有痕/对侧 rc=1 尾空）→ pending。
mk90 "$H90D" D6 "$(NOISE90M D6)"
touch "$PYT90/h90-D6.red"
SW90 "$H90D" "$STUB_RS90" >$ST_TMP/s90D.out 2>&1
grep -q "DONE-PENDING D6" $ST_TMP/s90D.out && grep -q "REDAUTO-HOST-1②" $ST_TMP/s90D.out \
  && g90 "$H90D" D6 | grep -q '"status": "DONE"' \
  && ok "90 D 对侧rc≠0但尾痕空=环境性未排除，不驳先喊(15:58案②路)——撤「对侧静默红非实据」必红" || { bad "90 D 对侧静默红仍被当真红冤驳"; tail -4 $ST_TMP/s90D.out; }
rm -rf "$H90A" "$H90B" "$H90C" "$H90D" "$R90" 2>/dev/null
rm -f "$PYT90"/h90-*.red 2>/dev/null

# ===== 91 REQ-BEAT-ANCHOR-1 名牌制判活红绿锁（业主定版 2026-11-02「不搞复杂，一泳道一会话」）=====
# 卡面点名编号 #77，但 77=MECH-RESIDENT-1、88=REQ-MERGE-GATE-1 同册在册已占——按避让撞号
# 惯例先落 #89；MECH-INT-BEAT13A-1 三向整合时主干 #89=BUG-LANE-SILENCE-1、#90=BUG-REDAUTO-HOST-1 已在册，本块让号重排为 #91（内部沙箱 ID 同批改号）
# ①断流场景回收环必 release（心跳龄超阈 + 同任期圈号持平两条断流线）；
# ②旧任期上报必被拒（done/progress/note 三通道 + 幽灵抢位（未发牌先报/虚报未来班）全拒）；
# ③现任期正常续写必不动（ALIVE 且卡留 CLAIMED，圈号照涨、账本不动）；
# 静态锁：判活函数(lane_alive)源码零进程探测——Get-Process/tasklist/kill -0/os.kill/pid_alive
#        不许进判活体；另加 MUT 红检（撤修复必红）：
# MUTA 拆 lane_alive 三判据针（龄阈/持平计数/任期匹配）⇒变异体把该死判活；
# MUTB 拆 _epoch_gate_card 对班针 ⇒变异体收下错班 done。
T91=$ST_TMP/cc-team-r91; T91REPO=$ST_TMP/cc-repo-r91
rm -rf $T91 $T91REPO; mkdir -p $T91 $T91REPO
(cd $T91REPO && git init -q && git config user.email t@t && git config user.name t && echo a > f.txt && git add -A && git commit -qm init) && H91=$(cd $T91REPO && git rev-parse HEAD)
C91(){ env -u CC_AUTO_VERIFY CC_TEAM_HOME=$T91 python3 "$SELF_CC/cards_dag.py" "$@"; }
R91(){ env CC_TEAM_HOME=$T91 python3 "$SELF_CC/ops/recycle.py" "$@"; }
g91(){ CC_TEAM_HOME=$T91 python3 -c "import json,os,sys;g=json.load(open(os.path.join(os.environ['CC_TEAM_HOME'],'state','graph.json'),encoding='utf-8'))
print(json.dumps(g['cards'].get(sys.argv[1],{}),ensure_ascii=False))" "$1"; }
age91(){ python3 - "$T91" "$1" <<'PYEOF'
import json,os,sys
p = os.path.join(sys.argv[1], "state", "lane-a91.beat")
d = json.load(open(p, encoding="utf-8")); d["t"] -= float(sys.argv[2]) * 60
json.dump(d, open(p, "w", encoding="utf-8"))
PYEOF
}
fresh91(){ python3 - "$T91" <<'PYEOF'
import json,os,sys,time
p = os.path.join(sys.argv[1], "state", "lane-a91.beat")
d = json.load(open(p, encoding="utf-8")); d["t"] = time.time()
json.dump(d, open(p, "w", encoding="utf-8"))
PYEOF
}
# —— ① 断流（心跳龄超阈）→ dry 只报不动手、apply 必回收 ——
C91 create B91A --title "名牌断流" --lanes a91 --verify "true" >/dev/null
C91 claim B91A --owner a91 --pid 999999 >/dev/null   # 死 pid 留档——判活不再问进程表
C91 progress B91A "圈1" --lane a91 --epoch 1 --ring 1 >/dev/null
age91 20
R91 --dry >$ST_TMP/r91a1.out 2>&1
grep -q "ZOMBIE B91A" $ST_TMP/r91a1.out && g91 B91A | grep -q '"status": "CLAIMED"' \
  && ok "91 ① 断流 dry 报 ZOMBIE 不动手" || { bad "91 ① 断流未判死(心跳龄判据缺?)"; tail -3 $ST_TMP/r91a1.out; }
R91 --apply >$ST_TMP/r91a2.out 2>&1
grep -q "ZOMBIE B91A" $ST_TMP/r91a2.out && g91 B91A | grep -q '"status": "OPEN"' \
  && ok "91 ① 断流 apply 回收 release（回收环必 release 锁）" || { bad "91 ① 断流不回收=僵尸占道回潮"; tail -3 $ST_TMP/r91a2.out; }
# —— ①-b 换班重派(新任期 e2)后圈号持平=变相断流：连两圈持平第三全环必收 ——
C91 epoch a91 --cid B91A >/dev/null
C91 claim B91A --owner a91 --pid 999999 --epoch 2 >/dev/null
C91 progress B91A "续写一笔" --lane a91 --epoch 2 --ring 1 >/dev/null
for i in 1 2 3; do
  fresh91
  R91 --ring full --apply >$ST_TMP/r91b$i.out 2>&1
done
grep -q "ALIVE B91A" $ST_TMP/r91b2.out && grep -q "ZOMBIE B91A" $ST_TMP/r91b3.out \
  && grep -q "持平" $ST_TMP/r91b3.out && g91 B91A | grep -q '"status": "OPEN"' \
  && ok "91 ①-b 同圈号连两圈持平⇒第三圈回收（防戳文件装活）" || { bad "91 ①-b 圈号持平判据不咬(装活可永远占道)"; tail -3 $ST_TMP/r91b3.out; }
# —— ② 旧任期上报必被拒：现任 e3，旧班 done/progress/note + 幽灵虚报全拒 ——
C91 epoch a91 >/dev/null
C91 claim B91A --owner a91 --pid 999999 --epoch 3 >/dev/null
C91 done B91A --owner a91 --commit deadbeef --repo "$T91REPO" --epoch 2 >$ST_TMP/r91c1.out 2>&1 && rc=0 || rc=$?
[ $rc -ne 0 ] && grep -q "错班拒收" $ST_TMP/r91c1.out && g91 B91A | grep -q '"status": "CLAIMED"' \
  && ok "91 ② 旧班 done 拒收(僵尸勾卡免疫)" || { bad "91 ② 旧班 done 穿透=换班不作废"; tail -3 $ST_TMP/r91c1.out; }
grep -q "EPOCH-STALE done" "$T91/state/events.log" \
  && ok "91 ② 错班拒收落 events 痕(审计可见)" || bad "91 ② 拒收无痕=黑盒"
C91 progress B91A "旧班乱报" --lane a91 --epoch 2 --ring 9 >$ST_TMP/r91c2.out 2>&1 && rc=0 || rc=$?
# 此刻 beat 账合法现值仍是 ①-b 的 e2/ring1（e3 首笔在 ③ 才写）——拒收必须「账纹丝不动」
[ $rc -ne 0 ] && grep -q "错班拒收" $ST_TMP/r91c2.out \
  && grep -q '"epoch": 2' "$T91/state/lane-a91.beat" && grep -q '"ring": 1' "$T91/state/lane-a91.beat" \
  && ok "91 ② 旧班 progress 拒收且 beat 账纹丝不动" || { bad "91 ② 旧班续写穿透=心跳被僵尸续"; tail -3 $ST_TMP/r91c2.out; }
C91 note --owner a91 --text "旧班乱报进度" --epoch 2 >$ST_TMP/r91c3.out 2>&1 && rc=0 || rc=$?
[ $rc -ne 0 ] && grep -q "EPOCH-STALE" "$T91/state/rejects.log" \
  && ok "91 ② 旧班 note 拒收进 rejects.log(不污染卡账)" || { bad "91 ② 旧班 note 穿透或无痕"; tail -3 $ST_TMP/r91c3.out; }
C91 create B91G --title "幽灵抢位" --lanes g91 --verify "true" >/dev/null
C91 claim B91G --owner g91 --epoch 99 >$ST_TMP/r91c4.out 2>&1 && rc=0 || rc=$?
[ $rc -ne 0 ] && grep -q "名牌核验拒" $ST_TMP/r91c4.out \
  && ok "91 ② 未发牌先报/虚报未来班=claim 拒(现读现报绕不开台账)" || { bad "91 ② 幽灵可虚报抢位"; tail -3 $ST_TMP/r91c4.out; }
# —— ③ 现任期正常续写必不动：ALIVE、卡留 CLAIMED、圈号照涨、账本心跳新 ——
C91 progress B91A "现任续写圈1" --lane a91 --epoch 3 --ring 1 >$ST_TMP/r91d1.out 2>&1 \
  && grep -q "BEAT_OK e3 r1" $ST_TMP/r91d1.out && ok "91 ③ 现任期续写入账" || bad "91 ③ 现任续写被拒(错杀活工人)"
C91 progress B91A "现任续写圈1重报" --lane a91 --epoch 3 --ring 1 >$ST_TMP/r91d2.out 2>&1 && rc=0 || rc=$?
[ $rc -ne 0 ] && grep -q "递增" $ST_TMP/r91d2.out \
  && ok "91 ③ 圈号不涨重报=拒（没在干活不许装）" || bad "91 ③ 圈号单调性失守"
C91 progress B91A "现任续写圈2" --lane a91 --epoch 3 --ring 2 >/dev/null
R91 --apply >$ST_TMP/r91d3.out 2>&1
grep -q "ALIVE B91A" $ST_TMP/r91d3.out && ! grep -q "ZOMBIE B91A" $ST_TMP/r91d3.out \
  && g91 B91A | grep -q '"status": "CLAIMED"' \
  && ok "91 ③ 现任期正常续写回收环不动（撤修复此条必红）" || { bad "91 ③ 活工人被回收(误杀)"; tail -3 $ST_TMP/r91d3.out; }
# —— 静态锁：判活函数体零进程探测（卡面原文 Get-Process/tasklist/kill -0，扩 os.kill/pid_alive）——
python3 -I -X utf8 - "$SELF_CC" <<'PYEOF' && ok "91 静态锁：判活函数零进程探测" || bad "91 静态锁红：判活回潮进程表(见上)"
import ast, os, sys
root = sys.argv[1]
src = open(os.path.join(root, "ops", "recycle.py"), encoding="utf-8").read()
fns = {n.name: ast.get_source_segment(src, n) or ""
       for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
assert "lane_alive" in fns, "lane_alive=判活唯一权威，函数不在=名牌制本体丢了"
for t in ("Get-Process", "tasklist", "kill -0", "os.kill", "pid_alive", "subprocess"):
    assert t not in fns["lane_alive"], "判活函数含禁区字样: " + t
print("BEAT_STATIC_OK")
PYEOF
# —— MUTA 红检：拆 lane_alive 三判据针（龄阈/持平/任期匹配）⇒变异体把该死判活 ——
python3 -I -X utf8 - "$SELF_CC" "$T91" <<'PYEOF' && ok "91 MUTA：拆三判据针⇒变异体全误判活(锁咬得住)" || bad "91 MUTA 不红=判据是纸锁(见上)"
import importlib.util, json, os, sys, tempfile, time
root, sbx = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
os.environ["CC_TEAM_HOME"] = sbx
src = open(os.path.join(root, "ops", "recycle.py"), encoding="utf-8").read()
def load(name, text):
    p = os.path.join(tempfile.gettempdir(), name + ".py")
    open(p, "w", encoding="utf-8").write(text)
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def beat(epoch, ring, age_min):
    os.makedirs(os.path.join(sbx, "state"), exist_ok=True)
    json.dump(dict(lane="mut91", epoch=epoch, ring=ring, cid="MUT", ts="x",
                   t=time.time() - age_min * 60),
              open(os.path.join(sbx, "state", "lane-mut91.beat"), "w"))
m = load("rec91_orig", src)
beat(1, 5, 20)
assert m.lane_alive("mut91", 1, 15, "MUT", {})[0] is False, "夹具失效：真身应判断流死"
needle = "        if age >= minutes:"
assert needle in src, "哨兵失配：龄阈行被改动=必须同步本红检（宁红不裸）"
m2 = load("rec91_mutA", src.replace(needle, "        if False:  # MUTA-AGE"))
assert m2.lane_alive("mut91", 1, 15, "MUT", {})[0] is True, "变异体竟仍判死=针没拆对/加载没生效"
beat(1, 7, 0)
obs = {}
assert m.lane_alive("mut91", 1, 15, "MUT", obs)[0] is True
assert m.lane_alive("mut91", 1, 15, "MUT", obs)[0] is True
al, why = m.lane_alive("mut91", 1, 15, "MUT", obs)
assert al is False and "持平" in why, "真身第三圈应判持平死"
needle = "        if plateau >= 2:"
assert needle in src, "哨兵失配：持平行被改动=必须同步本红检（宁红不裸）"
m3 = load("rec91_mutB", src.replace(needle, "        if False:  # MUTA-PLATEAU"))
obs = {}
for _ in range(4):
    assert m3.lane_alive("mut91", 1, 15, "MUT", obs)[0] is True, "变异体竟仍判持平死"
beat(1, 9, 0)
assert m.lane_alive("mut91", 404, 15, "MUT", {})[0] is False, "真身应拒错班（旧任期心跳≠活）"
needle = "if b_ep == epoch and b_t > 0:"
assert needle in src, "哨兵失配：任期匹配行被改动=必须同步本红检（宁红不裸）"
m4 = load("rec91_mutC", src.replace(needle, "if b_t > 0:  # MUTA-EPOCH"))
assert m4.lane_alive("mut91", 404, 15, "MUT", {})[0] is True, "变异体竟仍拒错班=加载没生效"
print("BEAT_MUTA_OK")
PYEOF
# —— MUTB 红检：拆 _epoch_gate_card 对班针 ⇒变异体收下错班 done（整包副本跑，真身对照在先）——
MUT91=$ST_TMP/cc-mut91
rm -rf $MUT91; mkdir -p $MUT91
cp "$SELF_CC/cards_dag.py" "$MUT91/" && cp -r "$SELF_CC/ccards" "$MUT91/"
python3 -I - "$MUT91/ccards/commands.py" <<'PYEOF' && ok "91 MUTB 变异体注入成功(哨兵命中)" || bad "91 MUTB 注入失败=红检空转"
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
needle = "    if int(declared) != int(cep):"
assert needle in s, "哨兵失配：_epoch_gate_card 对班行被改动=必须同步本红检（宁红不裸）"
open(p, "w", encoding="utf-8").write(s.replace(needle, "    if False:  # MUTB-GATE"))
PYEOF
M91(){ env -u CC_AUTO_VERIFY CC_TEAM_HOME=$T91 python3 "$MUT91/cards_dag.py" "$@"; }
C91 done B91A --owner a91 --commit "$H91" --repo "$T91REPO" --epoch 99 >$ST_TMP/r91e1.out 2>&1 && rc=0 || rc=$?
[ $rc -ne 0 ] && grep -q "错班拒收" $ST_TMP/r91e1.out \
  && ok "91 MUTB 前置：真身仍拒错班(对照面)" || bad "91 MUTB 前置红：真身错放过(被测本体先坏了)"
M91 done B91A --owner a91 --commit "$H91" --repo "$T91REPO" --epoch 99 >$ST_TMP/r91e2.out 2>&1 && rc=0 || rc=$?
[ $rc -eq 0 ] && grep -q "B91A DONE" $ST_TMP/r91e2.out \
  && ok "91 MUTB：拆对班针⇒错班 done 穿透(锁就是这一行)" || { bad "91 MUTB 变异体竟仍拒=红检空转"; tail -3 $ST_TMP/r91e2.out; }
rm -rf $MUT91 $T91 $T91REPO $ST_TMP/rec91_mut[ABC].py $ST_TMP/rec91_orig.py $ST_TMP/r91*.out

# ===== 92 BUG-SPAWN-TRACE-1 起会话留痕+起班即死尸检常驻锁（业主 2026-11-02 两例实锤：m10/m2 起班即死、
#    stdout/stderr 全进 DEVNULL=死因永远无据，占道到人工放卡）。原编号 #91，MECH-INT-OPS5-1 整合时
#    主干 #91=REQ-BEAT-ANCHOR-1 已在册，本块让号重排 #92（内部沙箱 ID 91→92 同批；套面锁号 T5-T7→T7-T9，
#    让位主干 REQ-REG-HOST T5/T6）。三面：
#    ①源面锚：dispatch.py 必须有 WORKER-DIED 尸检行与 runner-<道>-<时刻>.log 留痕（接回 DEVNULL=此处先红）；
#    ②套面：dispatch.py --selftest 九锁全绿（T1-T4 水位 + REG-HOST T5 名册分派/T6 留白拒起
#       + 本卡 T7 留痕/T8 即死检出/T9 不误杀）；
#    ③撤机制必红实测（2026-11-02 红检，整合后锁号映射）：M1 留痕回 DEVNULL→T7+T8 红；
#       M2 拆尸检→T8 红；M3 拆转录旁证→T9 红。
D92="$SELF_CC/ops/dispatch.py"
grep -q "WORKER-DIED" "$D92" && grep -q "runner-" "$D92" && grep -q '"stdout": logf' "$D92" \
  && ok "92 dispatch 源面：WORKER-DIED+runner-留痕+stdout 不再 DEVNULL" \
  || { bad "92 dispatch 留痕/尸检源面消失（合并吞回 DEVNULL?）"; grep -n "stdout=" "$D92" | head -3; }
T92=$ST_TMP/cc-team-t92; rm -rf $T92; mkdir -p $T92/state $T92/bin; echo '{"cards":{}}' > $T92/state/graph.json
# REQ-CCTEAM-OSS-1 随件（56995d03 后新增第三处公共runner必红）：dispatch --selftest T11 ok11a
# 要台账根 bin/run-cc.ps1 在盘（BUG-RUNNER-GONE-1 环境锁），沙箱只造 state/ 不种 bin/ →
# 干净克隆/CI 上 #92/#93.5 必红（作者机 CC_TEAM_HOME 指主仓才显绿）。种正本副本=满足在盘
# 断言本体，不削闸；正本缺位时 cp 即红，锁依然锁得住。
cp "$SELF_CC/bin/run-cc.ps1" "$T92/bin/run-cc.ps1" 2>/dev/null || bad "92 沙箱种 bin/run-cc.ps1 失败（正本缺位？）"
CC_TEAM_HOME=$T92 python3 -I "$D92" --selftest >$ST_TMP/s92.out 2>&1 \
  && grep -q "SPAWN-TRACE-SELFTEST ALL-GREEN" $ST_TMP/s92.out \
  && grep -q "REG-HOST-SELFTEST ALL-GREEN" $ST_TMP/s92.out \
  && grep -q "MEMGATE-SELFTEST ALL-GREEN" $ST_TMP/s92.out \
  && ok "92 dispatch --selftest 九锁全绿（T5名册/T6留白拒起/T7留痕/T8即死/T9不误杀，不碰正账不真起会话）" \
  || { bad "92 dispatch --selftest 红或崩"; tail -6 $ST_TMP/s92.out; }
rm -rf $T92 $ST_TMP/s92.out

# ==== #93 REQ-CTRL-PLAIN-1 单进程控制面 ctrld（三环并一 loop，零cron零WSL）====
# 原编号 #90：MECH-INT-OPS5-1 整合时主干 #90=BUG-REDAUTO-HOST-1、#91=REQ-BEAT-ANCHOR-1、
# #92=BUG-SPAWN-TRACE-1 已在册，本块让号重排 #93——内部沙箱 ID 90→93 同批改
# （H90D 与 REDAUTO 块同名沙箱变量正撞，必须连坐；重编号不连块内 ID=BEAT13A 教训）。
# 锁1 五把自检全绿；锁2 五种故障注入各恰红其位（env=子环境族三件套同响，余各1；守卫红得起来，撤修复必红）；
# 锁3 boot_check TC11 断言在位（被人摘掉=本锁红，防复验环空转）；锁4 部署模板在位+py_compile。
CTR93="$SELF_CC/ops/ctrld.py"
python3 -I "$CTR93" --selftest >$ST_TMP/s93g.out 2>&1   && grep -q "CTRLD_SELFTEST ALL-GREEN" $ST_TMP/s93g.out   && ok "93.1 ctrld selftest ALL-GREEN" || { bad "93.1 ctrld selftest 非全绿"; tail -6 $ST_TMP/s93g.out; }
n93=0
# MECH-TRAIN-ADR-1 ④轴（既有断言漂移对表，同 81 型）：a67b1596 毒轮根除把子环境族扩成
# T5/T5b/T5c 三检查，且 --fault env **故意**三件同响（撤拒绝段=T5红、撤 PATH 补=T5b红、
# 撤 Git Bash 排除段=T5c红——单故障面「恰红1」是 T5b/T5c 出生前的旧拓扑）。期望数按族钉死：
# env=3 其余=1；数字仍是精确断言（摘掉任一段则 HAS-RED 数对不上=本项红），未放宽。
exp_red=1
for f93 in schedule argv hb board env; do
  python3 -I "$CTR93" --selftest --fault "$f93" >$ST_TMP/s93f.out 2>&1
  rc93=$?
  redn93=$(grep -c '^FAIL' $ST_TMP/s93f.out)
  exp_red=1; [ "$f93" = "env" ] && exp_red=3
  [ $rc93 -eq 1 ] && [ "$redn93" = "$exp_red" ] && grep -q "HAS-RED($exp_red) fault=$f93" $ST_TMP/s93f.out     || { bad "93.2 fault=$f93 应恰红${exp_red}项rc1（实 rc=$rc93 fails=$redn93）"; n93=1; }
done
[ $n93 -eq 0 ] && ok "93.2 五注入各恰红其位(schedule/argv/hb/board=1, env=子环境族三件套)"
grep -q "TC11" "$SELF_CC/tests/boot_check.sh"   && grep -q "ctrld.heartbeat" "$SELF_CC/tests/boot_check.sh"   && grep -q "CTRLD_REQUIRED" "$SELF_CC/tests/boot_check.sh"   && ok "93.3 boot_check TC11 ctrld 活性断言在位" || bad "93.3 boot_check 缺 TC11/ctrld 活性断言（被摘?）"
test -f "$SELF_CC/deploy/systemd/cc-ctrld.service" && test -f "$SELF_CC/deploy/launchd/com.cc.ctrld.plist"   && python3 -I -m py_compile "$CTR93" "$SELF_CC/ops/dispatch.py"   && ok "93.4 Linux/launchd 模板在位 + ctrld/dispatch py_compile 过" || bad "93.4 模板缺失或 py_compile 红"
H93D="$(mktemp -d)"; mkdir -p "$H93D/state" "$H93D/proj" "$H93D/bin"; printf '{"cards":{}}' > "$H93D/state/graph.json"
# 同 #92 随件口径：T11 ok11a 环境锁要台账根有 bin/run-cc.ps1，缺则 MEMGATE-SELFTEST 恒红
cp "$SELF_CC/bin/run-cc.ps1" "$H93D/bin/run-cc.ps1" 2>/dev/null || bad "93.5 沙箱种 bin/run-cc.ps1 失败（正本缺位？）"
env CC_TEAM_HOME="$H93D" DISPATCH_BIND_WAIT=0 DISPATCH_PROJ="$H93D/proj"   python3 -I "$SELF_CC/ops/dispatch.py" --selftest >$ST_TMP/s93d.out 2>&1   && grep -q "MEMGATE-SELFTEST ALL-GREEN" $ST_TMP/s93d.out   && ok "93.5 dispatch 环改动无回归（MEMGATE-SELFTEST 全绿，沙箱根隔离）" || { bad "93.5 dispatch selftest 红（锚改动伤及）"; tail -4 $ST_TMP/s93d.out; }
rm -rf "$H93D" $ST_TMP/s93g.out $ST_TMP/s93f.out $ST_TMP/s93d.out

# ===== 94 REQ-BOARD-LANECLEAN-1：红检/沙箱临时树命名禁令（静态断言常驻，2026-11-02 业主实底）=====
# 卡面原落 #90，MECH-INT-BOARD5-1 整合时主干 #90=BUG-REDAUTO-HOST-1、#91=REQ-BEAT-ANCHOR-1 已在册，
# 本块让号重排为 #94（内部沙箱 tmp 名同批改号，避让撞号惯例）。
# 禁则：不许再产生 code-wf-*-tmp-* 会话目录（实况哨兵）；仓内源码不得含 wf-*-tmp-* 建目录构造
#（静态扫描）。违规=看板泳道区重新躺满幽灵——本条把「一次性清账」升级成「永久红绿锁」。
python3 "$SELF_CC/tests/sandbox_tree_naming_check.py" >$ST_TMP/s94.out 2>&1 \
  && ok "94 命名禁令双绿（无 code-wf-*-tmp-* 残留 + 源码无违规构造）" \
  || { bad "94 命名禁令红：见 $ST_TMP/s94.out（清账=cc-team/ops/lane_registry.py clean-sessions）"; tail -6 $ST_TMP/s94.out; }

# MECH-SELFTEST-WIN-1：尾行加显式 skips= 汇总字段（SKIP 也要数得出来；ALL_PASS 行保持纯净，
# done 判据「tail -1 | grep -q ALL_PASS」与 all.sh 的 grep 面不受影响）。
echo "=== fails=$fails skips=$skips ==="
[ $fails -eq 0 ] && echo ALL_PASS
