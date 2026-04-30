' kiseki JV-Link realtime launcher (fire-and-forget)
'
' This script is invoked by:
'   - kiseki-JVLink-Realtime task (daily 9:00)
'   - kiseki-UmaConn-Watchdog task (every 5min, when realtime is missing)
'
' Behavior:
'   1. Bail out if outside 9:00-22:00.
'   2. Skip if jvlink_agent --mode realtime is already running (idempotent restart).
'   3. Otherwise, launch pythonw detached (Wait=False) so this VBS exits immediately.
'      The previous Do-While+Wait=True design caused the wscript to hang forever
'      whenever pythonw blocked on JVRTOpen, stacking zombie processes daily.
'   The internal jvlink_agent watchdog (1800s) and the EOD cleanup task handle hung pythonw.

On Error Resume Next

Dim h
h = Hour(Now)
If h < 9 Or h >= 22 Then WScript.Quit

Dim wmi, procs, alreadyRunning
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
alreadyRunning = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            alreadyRunning = True
            Exit For
        End If
    End If
Next

If alreadyRunning Then WScript.Quit

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\kiseki\windows-agent"
WshShell.Run "C:\Python312-32\pythonw.exe jvlink_agent.py --mode realtime", 0, False
