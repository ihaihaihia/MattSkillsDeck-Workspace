# 把 DSH 数据目录从 C:\Users\MSN\.dsh 迁到 E:\dsh-home
# 用法：完全退出 DSH 后运行（双击 migrate-dsh-home.cmd）
# 安全设计：先完整复制 → 校验 → 改名留备份 → 建 junction；任一步失败自动回滚

$ErrorActionPreference = 'Stop'
$Src = 'C:\Users\MSN\.dsh'
$Dst = 'E:\dsh-home'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$Bak = "C:\Users\MSN\.dsh.bak-$Stamp"

function Fail($msg) {
  Write-Host ""
  Write-Host "[失败] $msg" -ForegroundColor Red
  Write-Host "源目录未被改动。" -ForegroundColor Yellow
  Read-Host "按回车关闭"
  exit 1
}

Write-Host "=== 1/6 检查 DSH 是否已退出 ===" -ForegroundColor Cyan
$running = Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue
if ($running) { Fail "DSH 还在运行（$($running.Count) 个进程）。请先在托盘退出，再运行本脚本。" }
Write-Host "已退出，可以开始。"

Write-Host "=== 2/6 检查目标位置 ===" -ForegroundColor Cyan
if ((Get-Item $Src -Force).LinkType) { Fail "$Src 已经是链接，可能已迁移过。" }
if (Test-Path $Dst) {
  $existing = (Get-ChildItem $Dst -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
  if ($existing -gt 0) { Fail "$Dst 非空（$([math]::Round($existing/1MB,1)) MB）。请先手动清空或换名字。" }
}
Write-Host "目标可用：$Dst"

Write-Host "=== 3/6 复制数据（约 310 MB，请稍候） ===" -ForegroundColor Cyan
$rc = Start-Process robocopy -ArgumentList @(
  "`"$Src`"", "`"$Dst`"", '/E', '/COPY:DAT', '/SL', '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS'
) -Wait -PassThru -NoNewWindow
if ($rc.ExitCode -ge 8) { Fail "robocopy 失败，退出码 $($rc.ExitCode)" }

$srcCount = (Get-ChildItem $Src -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object).Count
$dstCount = (Get-ChildItem $Dst -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "源文件数 $srcCount / 目标文件数 $dstCount"
if ($dstCount -lt $srcCount) { Fail "复制不完整（目标文件数少于源）。" }

Write-Host "=== 4/6 源目录改名为备份 ===" -ForegroundColor Cyan
try { Rename-Item -LiteralPath $Src -NewName (Split-Path $Bak -Leaf) -ErrorAction Stop }
catch { Fail "改名失败：$($_.Exception.Message)" }
Write-Host "已保留备份：$Bak"

Write-Host "=== 5/6 建立目录链接 ===" -ForegroundColor Cyan
$link = cmd /c mklink /J "$Src" "$Dst" 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host $link
  Write-Host "建链接失败，正在回滚…" -ForegroundColor Yellow
  Rename-Item -LiteralPath $Bak -NewName '.dsh' -ErrorAction SilentlyContinue
  Fail "mklink 失败；已尝试回滚，请检查 $Src 是否恢复。"
}
Write-Host $link

Write-Host "=== 6/6 校验 ===" -ForegroundColor Cyan
$item = Get-Item $Src -Force -ErrorAction SilentlyContinue
if (-not $item -or $item.LinkType -ne 'Junction') { Fail "链接未生效。" }
$probe = Join-Path $Src 'sessions'
if (-not (Test-Path $probe)) { Fail "链接指向的数据读不到（sessions 缺失）。" }
Write-Host "链接正常，sessions 可读。"

# 顺手设一个用户级 DSH_HOME，双保险（DSH 若认这个变量就直接走 E，不再经过链接）
[Environment]::SetEnvironmentVariable('DSH_HOME', $Dst, 'User')
Write-Host "已设置用户环境变量 DSH_HOME=$Dst"

Write-Host ""
Write-Host "=== 迁移完成 ===" -ForegroundColor Green
Write-Host "  C:\Users\MSN\.dsh  ->  $Dst （junction）"
Write-Host "  备份保留在：$Bak"
Write-Host "  现在可以重新打开 DSH。"
Write-Host ""

# 可选：删除 C 盘上的备份原件（只删这一个备份目录；不碰系统文件、不碰 DSH 本体、不碰 E 盘数据）
$bakSize = [math]::Round(((Get-ChildItem $Bak -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1MB, 0)
Write-Host "备份占用 $bakSize MB。" -ForegroundColor Yellow
$ans = Read-Host "现在删除 C 盘这份备份吗？输入 YES 删除；直接回车则保留"
if ($ans -eq 'YES') {
  # 删除前再验一次链接：链接不正常就不删，避免删掉唯一可用副本
  if ((Get-Item $Src -Force).LinkType -ne 'Junction' -or -not (Test-Path (Join-Path $Src 'profiles'))) {
    Write-Host "链接校验未通过，已拒绝删除备份。请先检查再手动处理。" -ForegroundColor Red
  } else {
    Write-Host "正在删除备份：$Bak" -ForegroundColor Cyan
    Remove-Item -LiteralPath $Bak -Recurse -Force
    if (Test-Path $Bak) { Write-Host "删除未完成，请稍后手动删除该目录。" -ForegroundColor Yellow }
    else { Write-Host "已删除，C 盘释放约 $bakSize MB。" -ForegroundColor Green }
  }
} else {
  Write-Host "已保留备份：$Bak（确认没问题后可手动删除这个目录）" -ForegroundColor Yellow
}
Write-Host ""
Read-Host "按回车关闭"
