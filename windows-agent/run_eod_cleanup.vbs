' kiseki end-of-day cleanup (daily 23:45)
'
' Forcibly terminate any kiseki agent realtime pythonw processes still alive
' so the next morning's 9:00 schedule starts from a clean state.
'
' Targets:
'   - jvlink_agent.py  --mode realtime
'   - umaconn_agent.py --mode realtime
'
' This is the safety net for hung COM/JV-Link/NV calls that the in-process
' watchdogs (jvlink 1800s, umaconn 600s) sometimes fail to interrupt.
'
' NOTE: Task trigger is set to 23:45 (not 23:00) so UmaConn can process
'   the SENV race results files published at ~23:10 JST before being killed.

On Error Resume Next

Dim sh, fso, logFile, ts
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
logFile = "C:\kiseki\windows-agent\watchdog.log"

Dim wmi, procs, killed
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
killed = 0
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        Dim isRealtime
        isRealtime = False
        If InStr(p.CommandLine, "jvlink_agent.py")  > 0 And InStr(p.CommandLine, "realtime") > 0 Then isRealtime = True
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then isRealtime = True
        If isRealtime Then
            Set ts = fso.OpenTextFile(logFile, 8, True)
            ts.WriteLine Now & " EOD cleanup: terminating PID=" & p.ProcessId & " (" & p.CommandLine & ")"
            ts.Close
            p.Terminate
            killed = killed + 1
        End If
    End If
Next

Set ts = fso.OpenTextFile(logFile, 8, True)
ts.WriteLine Now & " EOD cleanup done. terminated=" & killed
ts.Close
