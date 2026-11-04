#!/usr/bin/env bash
# cc-team/tests/boot_check.sh — REQ-BOOT-BRINGUP-1 开机链路自检（在 WSL 内跑）
#
# 卡面 verify 即跑它：
#   bash -c 'test -f cc-team/tests/boot_check.sh && grep -qE "schtasks|Task" cc-team/tests/boot_check.sh && wsl -e bash cc-team/tests/boot_check.sh'
#
# 逐项断言（业主问「重启后这些节点如何重新启动的」，点检=开机五环+任务在册+账外附加）：
#   TC1  systemd ready           /run/systemd/system 在 = wsl.conf[boot]systemd 生效
#   TC2  linger                  loginctl Linger=yes（退出登录后 user 单元仍活，常驻前提）
#   TC3  cc-dispatch             user 单元 enabled+active（enabled=重启后自起，active=此刻在跑）
#   TC4  cc-status-dashboard      user 单元 enabled+active（:8790 看板同左）
#   TC5  cron                    在跑（常驻环的节拍器；systemd 管不到就 pgrep 兜底，仍无=红）
#   TC6  看板 HTTP                127.0.0.1:8790 返回 200
#   TC7  心跳龄                   dispatcher.heartbeat <180s（find -mmin -3，龄>=3min 即红）
#   TC8  计划任务在册             schtasks.exe /query /tn cc-boot-bringup rc=0——开机自举第一环，
#                                 没它=重启后没人开一次 WSL，整摊常驻链静默不起（注册器 boot_bringup_register.sh）
#   TC9  任务描述防误删           Get-ScheduledTask 的 Description 含 cc-boot-bringup（勿随手删标记在账）
#   TC10 settings 默认模式        用户 settings.json permissions.defaultMode 仍 bypassPermissions
#                                 （防交互窗口收「过滤费」回潮；只判这一个值，不读不打印文件其余内容）
#   TC11 ctrld 活性（REQ-CTRL-PLAIN-1 单进程控制面去WSL依赖）：心跳文件 <team>/logs/ctrld.heartbeat
#                                 存在→龄<3min 必新鲜；三态=计划任务 cc-ctrld 在册→必在必新鲜（上岗后
#                                 自动收紧）；未在册（影子/过渡期）→豁免不红——不拖 BOOT-BRINGUP 的
#                                 复验成假红；CTRLD_REQUIRED=1 可强制（红检/上岗常压用）。
#
# 纪律：任一红退出非零；只判事实不自愈（常驻岗互盯原则：报警不代修，自愈=掩盖锚件之死）。
# 宿主：需 WSL 内跑（systemctl --user/loginctl 是 Linux 侧）；TC8/TC9 走 interop 调 Windows 侧
#       schtasks.exe / powershell.exe（Get-ScheduledTask）——与岗卡 RESIDENT-MOS-1 心跳判据同道。
# 可覆盖：BOOT_TASK_NAME / DASH_URL / DISPATCH_HB / CLAUDE_SETTINGS（默认=本机现网路径）；
#         CTRLD_HB / CTRLD_TASK_NAME / CTRLD_REQUIRED（TC11，REQ-CTRL-PLAIN-1）。
set -u
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

FAILS=0
ok()  { printf '[PASS] %s\n' "$1"; }
bad() { printf '[FAIL] %s\n' "$1"; FAILS=$((FAILS+1)); }
U="$(whoami)"
TASK="${BOOT_TASK_NAME:-cc-boot-bringup}"
DASH_URL="${DASH_URL:-http://127.0.0.1:8790/}"
# REQ-OSS-ENGINE-1：默认路径零宿主字面量——仓根/码根由本脚本自位置现算；settings 取挂载视图
# 首个含 .claude 的用户目录（本机取到的串与旧默认逐字相同，扫不到退回 ${HOME}）。
BC_REPO="$(cd "$(dirname "$0")/../.." && pwd)"
BC_CODE="$(dirname "$BC_REPO")"
BC_SETTINGS_DEFAULT(){ for u in /mnt/[A-Za-z]*/Users/*; do [ -d "$u/.claude" ] && { echo "$u/.claude/settings.json"; return; }; done; echo "$HOME/.claude/settings.json"; }
HB="${DISPATCH_HB:-$BC_CODE/.cc/state/dispatcher.heartbeat}"
SETTINGS="${CLAUDE_SETTINGS:-$(BC_SETTINGS_DEFAULT)}"

# TC1 systemd ready
if [ -d /run/systemd/system ]; then ok "TC1 systemd ready (/run/systemd/system)"; else bad "TC1 systemd 未就绪——wsl.conf[boot]systemd 没生效？"; fi

# TC2 linger
if loginctl show-user "$U" 2>/dev/null | grep -qx Linger=yes; then ok "TC2 linger Linger=yes ($U)"; else bad "TC2 linger 不为 yes——退出后 user 单元会被杀"; fi

# TC3/TC4 两单元 enabled+active
for svc in cc-dispatch cc-status-dashboard; do
  en="$(systemctl --user is-enabled "$svc" 2>&1)"; ac="$(systemctl --user is-active "$svc" 2>&1)"
  n="TC3"; [ "$svc" = cc-status-dashboard ] && n="TC4"
  if [ "$en" = enabled ] && [ "$ac" = active ]; then ok "$n $svc enabled+active"
  else bad "$n $svc enabled=$en active=${ac}（重启后自起看 enabled，此刻在跑看 active）"; fi
done

# TC5 cron 在跑
if systemctl is-active cron >/dev/null 2>&1 || pgrep -x cron >/dev/null 2>&1; then ok "TC5 cron 在跑"; else bad "TC5 cron 没在跑——常驻环节拍器停了"; fi

# TC6 看板 HTTP 200
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$DASH_URL" 2>/dev/null)"
if [ "$code" = 200 ]; then ok "TC6 看板 HTTP 200 ($DASH_URL)"; else bad "TC6 看板 HTTP=$code ≠200 ($DASH_URL)"; fi

# TC7 心跳龄 <3min（find -mmin -3；文件读不到=判不出=红，盲不算过）
if [ ! -e "$HB" ]; then bad "TC7 心跳文件不存在 $HB"
elif find "$HB" -mmin -3 2>/dev/null | grep -q .; then ok "TC7 心跳龄<3min ($HB)"
else bad "TC7 心跳龄>=3min 或时间源异常 ($HB mtime=$(stat -c %y "$HB" 2>/dev/null || echo '?'))"; fi

# TC8 计划任务在册（Windows 侧 schtasks.exe，经 WSL interop；rc=0 即在册）
if command -v schtasks.exe >/dev/null 2>&1 || [ -x /mnt/c/Windows/System32/schtasks.exe ]; then
  if schtasks.exe /query /tn "$TASK" >/dev/null 2>&1 || /mnt/c/Windows/System32/schtasks.exe /query /tn "$TASK" >/dev/null 2>&1; then
    ok "TC8 Windows 计划任务 $TASK 在册（开机自举第一环）"
  else
    bad "TC8 计划任务 $TASK 不在册——重启后没人碰 wsl.exe，整摊不起；跑 bash cc-team/tests/boot_bringup_register.sh 重注册"
  fi
else
  bad "TC8 schtasks.exe 不可达（interop 关了？）——判不出即红，盲不算过"
fi

# TC9 任务描述含 cc-boot-bringup（防误删标记）；PS 输出编码不定，只 grep ASCII 标记
PSBIN="$(command -v powershell.exe || echo /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe)"
desc="$("$PSBIN" -NoProfile -Command "(Get-ScheduledTask -TaskName '$TASK').Description" 2>/dev/null | tr -d '\r' | iconv -f GBK -t UTF-8 2>/dev/null || true)"
if grep -q cc-boot-bringup <<<"$desc"; then ok "TC9 任务描述含 cc-boot-bringup 防误删标记"; else bad "TC9 任务描述缺 cc-boot-bringup（被改写或 PS 不可达）"; fi

# TC10 settings.json 默认模式（python3 -I 解析，只打值不打文件，密钥在 env 段不外泄）
if command -v python3 >/dev/null 2>&1; then
  mode="$(python3 -I -c 'import json,sys
try:
    d=json.load(open(sys.argv[1],encoding="utf-8"))
    print((d.get("permissions") or {}).get("defaultMode") or "ABSENT")
except Exception as e:
    print("ERR:"+e.__class__.__name__)' "$SETTINGS" 2>/dev/null)"
  if [ "$mode" = bypassPermissions ]; then ok "TC10 settings defaultMode=bypassPermissions（交互窗口不收「过滤费」）"
  else bad "TC10 settings defaultMode=$mode ≠bypassPermissions——交互窗口收「过滤费」回潮"; fi
else
  bad "TC10 python3 不可用，判不出 settings 模式"
fi

# TC11 ctrld 活性（REQ-CTRL-PLAIN-1 单进程控制面）：三态见头注——在册必新鲜/过渡豁免/CTRLD_REQUIRED 强制。
CTR_HB="${CTRLD_HB:-$BC_REPO/cc-team/logs/ctrld.heartbeat}"
CTR_TASK="${CTRLD_TASK_NAME:-cc-ctrld}"
if [ -n "${CTRLD_REQUIRED:-}" ]; then
  ctr_req="$CTRLD_REQUIRED"
elif command -v schtasks.exe >/dev/null 2>&1 || [ -x /mnt/c/Windows/System32/schtasks.exe ]; then
  if schtasks.exe /query /tn "$CTR_TASK" >/dev/null 2>&1 || /mnt/c/Windows/System32/schtasks.exe /query /tn "$CTR_TASK" >/dev/null 2>&1; then
    ctr_req=1   # 计划任务在册=已上岗，心跳必在且新鲜
  else
    ctr_req=0   # 未上岗（影子/过渡）=豁免，不拖 BOOT-BRINGUP 复验成假红
  fi
else
  ctr_req=0     # Windows 侧不可达（纯 Linux 宿主跑本脚本时）：按过渡豁免，上岗制压请用 CTRLD_REQUIRED=1
fi
if [ "$ctr_req" = 1 ]; then
  if [ ! -e "$CTR_HB" ]; then bad "TC11 ctrld 心跳不存在 ${CTR_HB}（任务在册却没跑？install 或查计划任务）"
  elif find "$CTR_HB" -mmin -3 2>/dev/null | grep -q .; then
    ok "TC11 ctrld 活性 心跳龄<3min [$(tr -d '
' < "$CTR_HB" | cut -c1-60)]"
  else bad "TC11 ctrld 心跳龄>=3min 或时间源异常 ($CTR_HB mtime=$(stat -c %y "$CTR_HB" 2>/dev/null || echo '?'))——单进程控制面停了"; fi
else
  # 未上岗=豁免：心跳不存在/新鲜/陈旧都判 ok——陈旧只可能是影子实验残留（一次性 run-once），
  # 不是事故；把它判红会误伤 BOOT-BRINGUP 卡的复验（影子心跳 3 分钟后必陈旧=假红）。
  # 「起过又停」的静默死监控属于上岗后（任务在册→上方强制路 R1/R2 恰红已证），此处喊话不判红。
  if [ -e "$CTR_HB" ] && ! find "$CTR_HB" -mmin -3 2>/dev/null | grep -q .; then
    ok "TC11 ctrld 未上岗豁免（存在影子残留心跳但已停——上岗制压请注册 cc-ctrld 或 CTRLD_REQUIRED=1）"
  else ok "TC11 ctrld 活性（未上岗豁免/或心跳新鲜；上岗=注册 cc-ctrld 计划任务或 CTRLD_REQUIRED=1）"; fi
fi

echo "----------------------------------------"
if [ "$FAILS" -eq 0 ]; then
  echo "BOOT_CHECK=PASS 11/11（开机五环+任务在册+描述防误删+看板HTTP+心跳龄+默认模式+ctrld活性 全绿）"
  exit 0
else
  echo "BOOT_CHECK=FAIL 红 $FAILS 项（逐项见上；只判不自愈）"
  exit 1
fi
