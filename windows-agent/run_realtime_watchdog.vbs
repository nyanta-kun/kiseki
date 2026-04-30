' kiseki realtime watchdog (5min interval)
'
' Restart kiseki-UmaConn-Realtime / kiseki-JVLink-Realtime task if realtime process is missing.
' Active hours: 9:00 - 22:30
'
' This watchdog now monitors BOTH:
'   - umaconn_agent.py --mode realtime  (chihou)
'   - jvlink_agent.py  --mode realtime  (JRA)
'
' If a process is missing, schedule its restart task. The launcher VBS is itself
' idempotent (skips if already running) so a stray double-fire is harmless.

Dim h
h = Hour(Now)
If h < 9 Or h >= 23 Then WScript.Quit
If h = 22 Then
    If Minute(Now) >= 30 Then WScript.Quit
End If

Dim sh, fso
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Dim wmi, procs
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")

Dim umaFound, jvFound
umaFound = False
jvFound = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then umaFound = True
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then jvFound = True
    End If
Next

Dim logFile, ts
logFile = "C:\kiseki\windows-agent\watchdog.log"

If Not umaFound Then
    Set ts = fso.OpenTextFile(logFile, 8, True)
    ts.WriteLine Now & " umaconn realtime process not found -> starting kiseki-UmaConn-Realtime"
    ts.Close
    sh.Run "schtasks /run /tn kiseki-UmaConn-Realtime", 0, False
End If

If Not jvFound Then
    Set ts = fso.OpenTextFile(logFile, 8, True)
    ts.WriteLine Now & " jvlink realtime process not found -> starting kiseki-JVLink-Realtime"
    ts.Close
    sh.Run "schtasks /run /tn kiseki-JVLink-Realtime", 0, False
End If
