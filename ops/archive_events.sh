#!/bin/bash
# archive_events.sh — events.log 归档轮转（BACKLOG D7：原协议只有 >1MB 报警行、无归档动作）。
# 规则：state/events.log 超过阈值(默认 1MB) → 同目录 mv 成 events.log.<YYYY-MM-DD>（原子 rename，
#       不复制不截断，零丢行）→ touch 新空 events.log 续写。低于阈值什么都不做。
# 不动 ops/recycle.py/cards_dag.py 协议；selftest 不涵盖本脚本，单测见 tests 记录（1.2MB 人造副本）。
# 用法: bash ops/archive_events.sh [阈值字节数]   （CC_TEAM_HOME 可覆盖台账根）
# REQ-OSSPATH-1：缺省根=本脚本所在 cc-team 相对定位（禁宿主字面量；拷仓/换机/换用户皆可移植）。
set -u
_self="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"   # ops/.. = cc-team 根
N="${CC_TEAM_HOME:-$_self}"
LOG="$N/state/events.log"
THRESHOLD="${1:-1048576}"

[ -f "$LOG" ] || { echo "archive-events: 无 $LOG，无事可做"; exit 0; }
SZ=$(stat -c %s "$LOG")
if [ "$SZ" -le "$THRESHOLD" ]; then
  echo "archive-events: events.log ${SZ}B ≤ ${THRESHOLD}B，无需轮转"
  exit 0
fi

STAMP=$(date +%Y%m%d)
ARCH="$N/state/events.log.$STAMP"
# 同日二次轮转防撞名：加时间后缀
if [ -e "$ARCH" ]; then ARCH="$N/state/events.log.$STAMP-$(date +%H%M%S)"; fi
if mv "$LOG" "$ARCH"; then
  touch "$LOG"
  echo "archive-events: 已归档 $ARCH ($(stat -c %s "$ARCH")B)，新 events.log 已重置"
  exit 0
else
  echo "archive-events: ERROR mv 失败，原文件未动" >&2
  exit 1
fi
