' kiseki UmaConn realtime launcher (fire-and-forget, idempotent)
'
' Invoked by:
'   - kiseki-UmaConn-Realtime task (daily 9:00)
'   - kiseki-UmaConn-Watchdog task (every 5min when realtime is missing)
'
' Skips when umaconn_agent.py --mode realtime is already running, so concurrent
' watchdog and scheduled-task triggers cannot stack duplicate processes.

On Error Resume Next

Dim wmi, procs, alreadyRunning
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
alreadyRunning = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            alreadyRunning = True
            Exit For
        End If
    End If
Next

If alreadyRunning Then WScript.Quit

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\kiseki\windows-agent"
WshShell.Run "C:\Python312-32\pythonw.exe umaconn_agent.py --mode realtime", 0, False
