' Yunshu hidden launcher (2026-11-13) - no black console window on double-click.
'
' Why this layer exists: .bat is a CONSOLE program, so double-clicking a .lnk that
' points at it always opens a cmd window; the one-click script then opened a SECOND
' window for the backend, and the watchdog is a 30s probe loop that keeps its window
' alive. Pointing the .lnk here makes wscript.exe (GUI subsystem, no console of its
' own) start the .bat with WScript.Shell.Run(..., 0, False): window hidden, returns
' immediately, and the .bat exits by itself.
'
' Usage: wscript.exe scripts\yunshu_launch.vbs start_yunshu
'        wscript.exe scripts\yunshu_launch.vbs watchdog
' Output: logs\launcher_<mode>.log (stdout/stderr of the .bat are redirected there,
'         so "hidden window" != "swallowed logs"; the watchdog keeps writing its
'         structured trace to logs\watchdog_yunshu.jsonl).
'
' NOTE: keep this file ASCII-only. WScript reads .vbs as ANSI; non-ASCII bytes in a
' comment can swallow the following byte (GBK pairs) and break parsing silently.

Option Explicit

Dim shell, fso, here, repo, mode, batName, bat, logDir, logFile, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)   ' ...\scripts
repo = fso.GetParentFolderName(here)                     ' repository root

' Only two legal modes; anything else falls back to the one-click start
' (a typo must not end up doing nothing at all).
mode = "start_yunshu"
If WScript.Arguments.Count > 0 Then
    mode = LCase(Trim(WScript.Arguments(0)))
End If
If mode <> "watchdog" Then mode = "start_yunshu"

' mode is the SHORTCUT name; the batch files are named start_*.bat
batName = "start_yunshu.bat"
If mode = "watchdog" Then batName = "start_watchdog.bat"
bat = repo & "\" & batName

logDir = repo & "\logs"
If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)
logFile = logDir & "\launcher_" & mode & ".log"

' cmd /c ""...bat" >> "log" 2>&1" : the outer quote pair wraps the whole command,
' the inner pair keeps the path intact.
cmd = "cmd /c """"" & bat & """ >> """ & logFile & """ 2>&1"""

shell.CurrentDirectory = repo
shell.Run cmd, 0, False   ' 0 = hidden window; False = do not wait (wscript exits now)

Set shell = Nothing
Set fso = Nothing
