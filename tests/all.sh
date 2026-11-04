#!/bin/bash
# tests/all.sh — 闸标准跑法（MECH-SPD1）：套件**并行**，各用各的独立沙箱
# （MECH-PIDALIVE-WIN-1 增 pidalive 第四套=pid_alive 判活红绿锁；汇总行尾追加 pidalive=Ns，
#   既有 token 顺序不动，防连累历史卡「bash tests/all.sh」判据的 grep。
#   REQ-OSS-PREP-1 增 ccenv 第五套=CC 环境检测锁（cc_env_detect_test.py，离线打桩）；同样只尾追加。）
# （/tmp/cc-team-test、/tmp/cc-adr、/tmp/cc-recguard，均开跑即 rm -rf——并行的前提条件，改动别破坏）。
# 耗时从「三者和」降到「三者最大」。输出分套落 /tmp/ccsuite/<名>.log，任一 FAIL 自动打该套尾部 20 行；
# 全绿汇总行：`ALL_SUITES_PASS` + `selftest=Ns adversary=Ns guard=Ns wall=Ns`（留证用，卡面 verify 可 grep）。
# 单遍纪律（定版）：默认跑一遍即可——沙箱每跑必全新，旧「连跑×2 防串染」的理由已消除；
# 仅存储层换底这类关键改动（先例 ENG2）加跑第二遍。
set -u
N="$(cd "$(dirname "$0")/.." && pwd)"
# MECH-TRAIN-ADR-1：汇总日志面可移（CCSUITE_DIR）——火车沙箱逐圈传唯一路径，两路 all.sh
# 并跑不再互踩 .res/.log；缺省行为逐字不变。
D="${CCSUITE_DIR:-/tmp/ccsuite}"; rm -rf $D; mkdir -p $D
export PYTHONDONTWRITEBYTECODE=1   # REQ-OSSPATH-1 配套：防 pyc 嵌宿主绝对路径→判据 grep 假红

run(){ local name=$1; shift; local s e rc; s=$(date +%s); "$@" >"$D/$name.log" 2>&1; rc=$?; e=$(date +%s)
       echo "$rc $((e-s))" > "$D/$name.res"; }

T0=$(date +%s)
run selftest  bash "$N/tests/selftest.sh" &
run adversary bash "$N/tests/adversary.sh" &
run guard     python3 "$N/tests/recycle_guard_test.py" &
run pidalive  python3 "$N/tests/pid_alive_test.py" &
run ccenv     python3 "$N/tests/cc_env_detect_test.py" &
wait

bad=0
res(){ cut -d' ' -f"$1" < "$D/$2.res"; }
for spec in "selftest ALL_PASS" "adversary ADR_ALL_PASS" "guard GUARD_ALL_PASS" "pidalive PID_ALIVE_PASS" "ccenv CC_ENV_DETECT_PASS"; do
  set -- $spec; name=$1; marker=$2   # 词切分取法：${t% *} 族要两个空格才匹配，单空格串会假红(2026-10-31 实锤)
  if [ "$(res 1 $name)" != 0 ] || ! grep -q "$marker" "$D/$name.log"; then
    bad=$((bad+1)); echo "FAIL: $name (rc=$(res 1 $name)) —— $D/$name.log 全部红条+尾部:"
    # 只 tail -20 会把 FAIL 行挤出可见窗（macOS 首跑 adversary fails=1 查不到因=本改直接诱因）；
    # 红条全打 + 崩溃行（unbound/not found/syntax）全打，再留 20 行上下文尾巴。
    grep -aE '^FAIL|^=== .*(fails=[1-9]|FAIL)' "$D/$name.log" || true
    grep -aE 'unbound variable|command not found|syntax error|No such file|Segmentation' "$D/$name.log" | head -8 || true
    tail -20 "$D/$name.log"
  fi
done
TW=$(( $(date +%s) - T0 ))
echo "selftest=$(res 2 selftest)s adversary=$(res 2 adversary)s guard=$(res 2 guard)s pidalive=$(res 2 pidalive)s ccenv=$(res 2 ccenv)s wall=${TW}s"
[ $bad -eq 0 ] && echo ALL_SUITES_PASS || exit 1
