' Silent launcher for the dsh-longtou standalone panel.
' Executed by wscript.exe (no console window at all), it runs tools\open_panel.ps1
' in a hidden PowerShell window, so nothing flashes on screen.
' Keep this file ASCII-only: wscript reads it as ANSI.
Option Explicit

Dim sh, fso, here, ps1, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = here & "\tools\open_panel.ps1"

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """"
' 0 = hidden window, False = do not wait
sh.Run cmd, 0, False
