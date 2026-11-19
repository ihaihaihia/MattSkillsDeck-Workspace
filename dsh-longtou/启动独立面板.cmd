@echo off
rem Legacy entry. Double-clicking a .cmd always flashes a console window.
rem The zero-flash launcher is the .vbs file sitting next to this script;
rem this file now just forwards to it so old shortcuts keep working.
for %%F in ("%~dp0*.vbs") do wscript.exe //nologo "%%F"
exit /b 0
