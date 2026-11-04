#!/usr/bin/env bash
# cc-team/tests/boot_bringup_register.sh — REQ-BOOT-BRINGUP-1 开机自举注册器（唯一通道，幂等）
#
# 这是什么（类型=需求，业主 15:2x「重启后这些节点如何重新启动的」）：
#   注册 Windows 计划任务 cc-boot-bringup：**Interactive 登录触发（AtLogon）**，
#   任务体 = wsl.exe 唤醒虚拟机（等价 wsl.exe -e true）并往 logs/boot.log 落一行带时间戳的记录。
#   补的是开机链路第一环的被动软肋：wsl.conf[boot]systemd / linger / cc-dispatch+cc-status-dashboard
#   enabled / cron enabled / Restart=on-failure 五环都在，但重启后若没人碰一次 wsl.exe，
#   虚拟机根本不启动，整摊常驻链静默不起——本任务把「人碰」换成「登录自动碰」。
#
# 纪律与边界：
#   - 不装第三方服务管理器；不改现有 systemd user 单元一个字。
#   - 任务描述钉死 cc-boot-bringup + 卡号 + 「勿随手删」后果——防误删标记，boot_check.sh TC9 复查。
#   - 幂等：已存在先 Unregister 再 Register（同 scripts/ops/mos.sh register 口径）。
#   - boot.log 落主仓 dataworks-ai/logs/（绝对路径，不依赖泳道工作树，重启后一定在）。
#
# 用法：bash cc-team/tests/boot_bringup_register.sh     （Git Bash 或 WSL interop 均可）
# 自检：bash cc-team/tests/boot_check.sh（经 wsl 跑，任一红退出非零）
set -euo pipefail

TASK="${BOOT_TASK_NAME:-cc-boot-bringup}"
# REQ-OSS-ENGINE-1：仓根零宿主字面量。优先问 git「主工作树」——从泳道工作树里重注册也钉回主仓
# （旧硬编码语义=boot.log 必落主仓，不随执行处漂移）；git 不可达时退回本脚本自位置。
# 归一为 WSL /mnt 视图：Git Bash /c/... → /mnt/c/...、cygdrive 同理、WSL 原生路径逐字不变；
# 主仓原位注册取到的串与旧默认相同（boot.log 落同一文件）。
_r="$(git -C "$(cd "$(dirname "$0")/../.." && pwd)" rev-parse --mainworktree 2>/dev/null || true)"
[ -n "$_r" ] || _r="$(cd "$(dirname "$0")/../.." && pwd)"
case "$_r" in
  /mnt/*) REPO_WSL="$_r";;
  /cygdrive/*) REPO_WSL="/mnt/${_r#/cygdrive/}";;
  /[a-zA-Z]/*) REPO_WSL="/mnt${_r}";;
  [A-Za-z]:/*) REPO_WSL="/mnt/$(printf '%s' "${_r:0:1}" | tr 'A-Z' 'a-z')/${_r#*:/}";;
  *) REPO_WSL="$_r";;
esac
LOG="$REPO_WSL/logs/boot.log"
DESC="cc-boot-bringup — REQ-BOOT-BRINGUP-1 开机自举：Interactive 登录触发 wsl.exe 唤醒虚拟机并往 logs/boot.log 落时间戳行。勿随手删：删了它=重启后没人开一次 WSL，常驻链 cc-dispatch/cc-status-dashboard/cron 整摊静默不起。重注册：cc-team/tests/boot_bringup_register.sh；自检：cc-team/tests/boot_check.sh"

# 任务体：一次 bash -c 同时完成「唤醒」与「留痕」。内层 \" 经 Windows 命令行解析还原成 bash 的双引号，
# $(date -Iseconds) 由 bash 执行展开——这就是卡面「wsl.exe -e true 唤醒虚拟机并落 logs/boot.log(带时间戳)」。
ARG='-e bash -c "mkdir -p '"$REPO_WSL"'/logs; echo \"$(date -Iseconds) cc-boot-bringup: interactive logon woke WSL (wsl.exe -e true equivalent)\" >> '"$LOG"'; true"'

# powershell.exe 定位：Git Bash 走 PATH；WSL 走 interop（/mnt/c 在 PATH 时 command -v 也能命中）。
PS_BIN="$(command -v powershell.exe || true)"
[ -n "$PS_BIN" ] || PS_BIN=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
[ -x "$PS_BIN" ] || { echo "REGISTER=FAIL 找不到 powershell.exe（需 Windows 宿主或 WSL interop）"; exit 1; }

# PS 走 -EncodedCommand（UTF-16LE base64），中文描述不经任何层转义丢字——同 mos.sh register 的实测做法。
ps="$(cat <<'PS'
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$name = '@TASK@'
$action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument '@ARG@'
$tLogon = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -Hidden
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $tLogon `
  -Settings $settings -Principal $principal -Description '@DESC@' | Out-Null
Write-Output "registered=$name"
(Get-ScheduledTask -TaskName $name).Triggers | ForEach-Object { "trigger=" + $_.CimClass.CimClassName }
(Get-ScheduledTask -TaskName $name).Actions | ForEach-Object { "action=" + $_.Execute + ' ' + $_.Arguments }
"state=" + (Get-ScheduledTask -TaskName $name).State
PS
)"
ps="${ps//@TASK@/$TASK}"
ps="${ps//@ARG@/$ARG}"
ps="${ps//@DESC@/$DESC}"
b64="$(printf '%s' "$ps" | iconv -f UTF-8 -t UTF-16LE | base64 -w0)"
out="$("$PS_BIN" -NoProfile -ExecutionPolicy Bypass -EncodedCommand "$b64" 2>&1)" && rc=0 || rc=$?
printf '%s\n' "$out" | tr -d '\r'
if [ $rc -ne 0 ]; then echo "REGISTER=FAIL rc=${rc}（任务 $TASK 未注册成）"; exit 1; fi
grep -q "registered=$TASK" <<<"$out" || { echo "REGISTER=FAIL 输出无 registered= 标记"; exit 1; }
echo "REGISTER=PASS task=${TASK}（Interactive 登录触发；首启验证=schtasks.exe /run /tn $TASK 后看 ${LOG}）"
