# 回滚：把 DSH 数据目录从 E 盘链接改回 C 盘实体目录
# 用法：完全退出 DSH 后运行（双击 rollback-dsh-home.cmd）

$ErrorActionPreference = 'Stop'
$Src = 'C:\Users\MSN\.dsh'

$running = Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue
if ($running) {
  Write-Host "[失败] DSH 还在运行，请先退出。" -ForegroundColor Red
  Read-Host "按回车关闭"; exit 1
}

$item = Get-Item $Src -Force -ErrorAction SilentlyContinue
if (-not $item) { Write-Host "找不到 $Src" -ForegroundColor Red; Read-Host; exit 1 }

if ($item.LinkType -ne 'Junction') {
  Write-Host "$Src 不是链接，无需回滚。" -ForegroundColor Yellow
  Read-Host "按回车关闭"; exit 1
}

$bak = Get-ChildItem 'C:\Users\MSN' -Directory -Filter '.dsh.bak-*' -Force -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $bak) {
  Write-Host "找不到 .dsh.bak-* 备份。E 盘数据仍在 $((Get-Item $Src).Target)，可手动取回。" -ForegroundColor Red
  Read-Host "按回车关闭"; exit 1
}

Write-Host "移除链接（只删链接，不动 E 盘数据）…" -ForegroundColor Cyan
cmd /c rmdir "$Src"
if (Test-Path $Src) { Write-Host "[失败] 链接没删掉。" -ForegroundColor Red; Read-Host; exit 1 }

Write-Host "恢复备份 $($bak.Name) …" -ForegroundColor Cyan
Rename-Item -LiteralPath $bak.FullName -NewName '.dsh'

[Environment]::SetEnvironmentVariable('DSH_HOME', $null, 'User')

Write-Host "=== 已回滚 ===" -ForegroundColor Green
Write-Host "数据目录恢复为：$Src"
Write-Host "E:\dsh-home 里的数据仍然保留（可自行删除）。"
Read-Host "按回车关闭"
