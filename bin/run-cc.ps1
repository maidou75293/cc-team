param(
  [string]$Brief = '',
  [string]$Workdir = '',
  [string]$Lane = ''
)
$ErrorActionPreference = 'Continue'
# REQ-CCTEAM-OSS-1（2026-11-04 单仓开源化）：原五处宿主绝对路径字面量（Workdir 默认/
# CC_TEAM_HOME/cc-msg 日志目录/道锁目录/registry 册）整体退役，改「env 覆写 > 脚本
# 自位置派生」——RepoRoot=本脚本所在 <仓根>\cc-team\bin 上两级；发布面宿主锚 grep=0
# （卡面判据）。覆写名：CC_REPO_ROOT/CC_MSG_BASE/CC_STATE_DIR/CC_CLAUDE_EXE（沙箱与换宿主用）。
# 现网等价实锤：起会话路径唯一=config.CC_RUNNER=<CC_TEAM_HOME>/bin/run-cc.ps1=账本仓正本
# （BUG-RUNNER-GONE-1 正本入仓纪律），其 $PSScriptRoot 派生=旧字面量逐字符同值；
# dispatch/inbox/fuse 三方拉起全部显式传 -Workdir，$Workdir 缺省仅影响手敲场景。
# 2026-11-01 实锤（补49/记忆 cards-dag-needs-cc-team-home）：泳道工作树也检出 cc-team，
# 但正账 graph.json 被 gitignore 只在主仓——worker 里跑 cards_dag 报「找不到卡」或静默
# 写本地空账=收不了口→回收重派空转。钉死所有子进程账本=正本所在仓 cc-team（派生形）。
$RepoRoot = if ($env:CC_REPO_ROOT) { $env:CC_REPO_ROOT } else {
  Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
}
if (-not $Workdir) { $Workdir = $RepoRoot }
$env:CC_TEAM_HOME = Join-Path $RepoRoot 'cc-team'
# 补50 业主设计：done 拉起校验（锁外跑、绿=瞬锁落账 VERIFIED）——worker 会话全启
$env:CC_AUTO_VERIFY = '1'
$exe  = if ($env:CC_CLAUDE_EXE) { $env:CC_CLAUDE_EXE } else { "$env:APPDATA\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe" }
$stamp = Get-Date -Format 'HHmmss'
$base  = if ($env:CC_MSG_BASE) { $env:CC_MSG_BASE } else { Join-Path $env:USERPROFILE '.openclaw\tmp\cc-msg' }
$lockDir = if ($env:CC_STATE_DIR) { $env:CC_STATE_DIR } else {
  Join-Path (Split-Path $RepoRoot -Parent) '.cc\state'
}
New-Item -ItemType Directory -Force -Path $lockDir | Out-Null
# 2026-11-04 BUG-RUNNER-GONE-1：OpenClaw 原生切流把 ~/.openclaw/tmp 整树归档，$base 目录随之失踪——
# Set-Content/-RedirectStandardOutput 全炸、起会话静默空转。日志目录随起随修，不再依赖外部存活。
New-Item -ItemType Directory -Force -Path $base | Out-Null

# ===== 身份=进程名字（业主 2026-11-03 定版：不允许用进程号；进程有名字=岗位编号+名称）=====
# 岗位名现读名册（改册即时生效，无需重启任何东西——本脚本每拍新起）。读不到册=只带道号。
$role = ''
$reg = Join-Path (Join-Path $env:CC_TEAM_HOME 'state') 'registry.json'
if ($Lane -and (Test-Path $reg)) {
  try {
    $j = Get-Content -Raw -Encoding UTF8 $reg | ConvertFrom-Json
    $one = $j.lanes | Where-Object { $_.lane -eq $Lane }
    if ($one -and $one.role) { $role = [string]$one.role }
  } catch { }
}
$tag = if ($Lane) { "CCLANE=$Lane-$role" } else { '' }

# 单例锁按名字判：锁内存的是名字标记（第一行）+工作树（第二行），**不是 pid**。
# 「这道有没有人」= 进程表里有无命令行带 CCLANE=<道号>- 的 claude.exe。
# （旧版存 pid + Get-Process -Id 校验=进程号依赖；pid 复用假活/包装进程打空两案随本版根除）
if ($Lane) {
  $lock = Join-Path $lockDir "lane-$Lane.lock"
  if (Test-Path $lock) {
    $running = Get-CimInstance Win32_Process -Filter "Name='claude.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -and $_.CommandLine.Contains("CCLANE=$Lane-") }
    if ($running) {
      "SKIP lane=$Lane 岗位在编（带名字进程在场 $($running.Count) 个，标记前缀 CCLANE=$Lane-）" |
        Out-File "$base\skip-$stamp-$PID.log" -Encoding UTF8
      exit 0
    }
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
  }
}

$log = "$base\cc-run-$stamp-$PID.log"
$err = "$base\cc-run-$stamp-$PID.err"
"START $stamp lane=$Lane role=$role wd=$Workdir brief=$Brief" | Set-Content -Path $log -Encoding UTF8
try {
  $prompt = Get-Content -Raw -Encoding UTF8 $Brief
  Set-Location $Workdir
  # Start-Process 的 ArgumentList 不会自动加引号：多行/含空格的 prompt 会被拆成多个参数，
  # claude 只拿到第一个词 → 打印版本号秒退（19:47 验收会话就是这么死的）。必须自己包引号。
  # 压缩窗口钉 200K（15:31 实测：不设时上下文一路涨到 556K/轮，总 token 与轮数近似平方关系）
  # --append-system-prompt 双重用途：①把岗位名字烧进 worker 命令行=全系统按名认人的载体；
  # ②worker 自己也知道自己的岗位（名册改岗位名，下一拍起的会话自动跟上）。
  $q = '-p "' + ($prompt.Trim() -replace '"', '\"') + '" --autocompact 200000 --dangerously-skip-permissions --append-system-prompt "' + $tag + '（本进程名字=岗位编号+岗位名；调度按名认人，禁用进程号）"'
  $p = Start-Process -FilePath $exe -ArgumentList $q `
        -WorkingDirectory $Workdir -NoNewWindow -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError $err
  if ($Lane) {
    @($tag, $Workdir) | Set-Content -Path (Join-Path $lockDir "lane-$Lane.lock") -Encoding UTF8
  }
  $p.WaitForExit()
  "RC=$($p.ExitCode)" | Add-Content -Path $log -Encoding UTF8
  if (Test-Path $err) { Get-Content $err -Tail 20 | Add-Content -Path $log -Encoding UTF8 }
} finally {
  "DONE $(Get-Date -Format HH:mm:ss)" | Add-Content -Path $log -Encoding UTF8
  if ($Lane) {
    $lock = Join-Path $lockDir "lane-$Lane.lock"
    $cur = (Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1)
    # 锁的归属也按名字认：第一行还是我的标记才删（别人家的锁不碰）
    if ($cur -and $cur.Contains($tag)) { Remove-Item $lock -Force -ErrorAction SilentlyContinue }
  }
}
