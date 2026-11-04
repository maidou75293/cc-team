#!/bin/bash
# tests/select.sh — 开发档最小套件选择器（MECH-SPD1）：按改动文件打印**该跑哪些**，用来在迭代中少跑无关面。
# ⚠️ 只服务开发循环；终验（卡面 verify / verdict 机器闸）不降级，照旧全量 all.sh。
# 用法: bash tests/select.sh [路径...]   缺省=工作区+HEAD 的全部改动。
# 规则表（拿不准一律 FULL——少跑的前提是选得对，不是赌）：
#   cc-team/*.md / cc-team/docs/*  → 不跑（文档不触机制）
#   cc-team/tests/<单套件>         → 只跑那一套
#   cc-team/ops/recycle.py         → guard + selftest（看护侧）
#   cc-team/** 其余（ccards/入口/facade/测试公共设施…）→ FULL（兜底，宁可多跑）
#   全在 cc-team 之外              → NONE（业务仓自有测试分层，见 docs/测试分层.md）
set -u
files="$*"
# git 默认按 cwd 相对输出——在 cc-team/ 里跑会吐出 ccards/... 匹配不上规则：钉到仓库根取根相对路径
[ -z "$files" ] && ROOT=$(git rev-parse --show-toplevel 2>/dev/null) && \
  files=$(git -C "$ROOT" diff --name-only HEAD 2>/dev/null; git -C "$ROOT" ls-files --others --exclude-standard 2>/dev/null)
full=0 s=0 a=0 g=0 any=0
for f in $files; do
  case "$f" in
    cc-team/*.md|cc-team/docs/*) any=1; continue;;
    cc-team/*) any=1;;
    *) continue;;
  esac
  case "$f" in
    cc-team/tests/selftest.sh) s=1;;
    cc-team/tests/adversary.sh) a=1;;
    cc-team/tests/recycle_guard_test.py) g=1;;
    cc-team/tests/all.sh|cc-team/tests/select.sh) s=1;;
    cc-team/ops/recycle.py) s=1; g=1;;
    *) full=1;;
  esac
done
if [ $full = 1 ]; then
  echo "FULL: bash tests/all.sh"
elif [ $any = 1 ] && [ $((s+a+g)) = 0 ]; then
  echo "NONE: 只动文档，不触机制套件"
elif [ $s = 1 ] || [ $a = 1 ] || [ $g = 1 ]; then
  o=""
  [ $s = 1 ] && o="$o bash tests/selftest.sh;"
  [ $a = 1 ] && o="$o bash tests/adversary.sh;"
  [ $g = 1 ] && o="$o python3 tests/recycle_guard_test.py;"
  echo "PARTIAL:$o"
else
  echo "NONE: 未触 cc-team"
fi
