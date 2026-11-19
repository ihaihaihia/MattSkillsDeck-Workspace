$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$port = 8765
$url = "http://127.0.0.1:$port/"

# 口令：存在 .dsh-switchman-public-token.txt 时启用口令闸（公网窗口期用）。
# 文件不存在 = 旧行为（无口令，本机/局域网直接打开）。
$tokenFile = Join-Path $root '.dsh-switchman-public-token.txt'
$token = ''
if (Test-Path $tokenFile) {
    $token = (Get-Content $tokenFile -Raw -ErrorAction SilentlyContinue)
    if ($token) { $token = $token.Trim() }
}
$query = ''
if ($token) { $query = "?k=$token" }

$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    $pythonw = Join-Path $root '.venv\Scripts\python.exe'
}

# Starting a second server is harmless: the new process exits if the port is taken.
if (Test-Path $pythonw) {
    # --host 0.0.0.0：同时监听局域网，手机/平板可用 http://<本机局域网IP>:8765/ 打开。
    # 只想要本机访问时把它改回 127.0.0.1（默认值）。
    $serverArgs = @('tools\panel_server.py', '--host', '0.0.0.0', '--port', "$port")
    if ($token) { $serverArgs += @('--token', $token) }
    Start-Process -FilePath $pythonw -ArgumentList $serverArgs `
        -WorkingDirectory $root -WindowStyle Hidden | Out-Null
    Start-Sleep -Milliseconds 900
}

# 浏览器优先级：Chrome 放在最前面。
# 理由：用户日常就在跑 Chrome，面板窗口并入同一个浏览器实例，只多一个渲染进程；
# 若走 Edge，则要额外拉起一整套 Edge 内核（实测约 660MB），纯属重复占用。
$chromeCandidates = @(
    (Join-Path $env:LocalAppData 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe')
)
$edgeCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
)
$chrome = $chromeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
$edge = $edgeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
$profileDir = Join-Path $env:LOCALAPPDATA 'DshLongtouPanelProfile'

if ($chrome) {
    # 关键：不传 --user-data-dir。Chrome 已在跑时，这条命令只是往现有实例里加一个 app 窗口，
    # 不会另起一个独立的浏览器进程树；Chrome 没跑时它才自己起一份。
    Start-Process -FilePath $chrome -ArgumentList @(
        "--app=$url$query",
        '--window-size=1320,900',
        '--no-first-run',
        '--no-default-browser-check',
        '--force-dark-mode'
    ) | Out-Null
} elseif ($edge) {
    Start-Process -FilePath $edge -ArgumentList @(
        "--app=$url$query",
        '--window-size=1320,900',
        "--user-data-dir=$profileDir",
        '--no-first-run',
        '--no-default-browser-check',
        '--force-dark-mode'
    ) | Out-Null
} else {
    Start-Process "$url$query" | Out-Null
}
