@echo off
REM Double-click to roll the DSH data directory back to C.
REM Quit DSH completely before running.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0rollback-dsh-home.ps1"
