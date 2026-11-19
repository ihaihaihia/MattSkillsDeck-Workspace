@echo off
REM Double-click to migrate DSH data directory to E:\dsh-home
REM Quit DSH completely before running.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0migrate-dsh-home.ps1"
