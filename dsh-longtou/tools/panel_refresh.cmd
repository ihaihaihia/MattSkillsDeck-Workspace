@echo off
rem dsh-longtou 面板四个时点刷新的计划任务入口（09:25 / 11:30 / 13:00 / 15:00）。
rem 只跑一次刷新并追加日志，不重启 8765 面板服务。
setlocal
cd /d "D:\deepseek-harness\default-workspace\dsh-longtou"
echo ==== %DATE% %TIME% start ==== >> output\panel_refresh.log
".venv\Scripts\python.exe" tools\panel_refresh.py --auto >> output\panel_refresh.log 2>&1
set RC=%ERRORLEVEL%
echo ==== %DATE% %TIME% end rc=%RC% ==== >> output\panel_refresh.log
endlocal & exit /b %RC%
