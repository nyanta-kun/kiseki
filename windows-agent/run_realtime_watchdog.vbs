' kiseki realtime watchdog (5min interval)
'
' Restart kiseki-UmaConn-Realtime / kiseki-JVLink-Realtime task if realtime process is
' missing OR stalled (process alive but the loop stopped making progress).
' Active hours: 9:00 - 22:30
'
' This watchdog monitors BOTH:
'   - umaconn_agent.py --mode realtime  (chihou)
'   - jvlink_agent.py  --mode realtime  (JRA)
'
' [1] MISSING  : process not found            -> run the restart task
' [2] STALLED  : process alive but the heartbeat file has not been updated for
'                STALL_MINUTES               -> terminate the process, then restart
'
' Why [2] exists (2026-08-02 incident):
'   jvlink_agent realtime stayed alive but JVRTOpen hung at the COM level and froze
'   the in-process watchdog thread too, so os._exit(1) never fired. Odds collection
'   was dead for ~95 minutes while the process looked healthy to a presence check.
'   The agents' watchdog threads now touch data\realtime_heartbeat_*.txt every 30s;
'   if that stops, everything is frozen and only an external kill can recover it.
'
' The launcher VBS is itself idempotent (skips if already running) so a stray
' double-fire is harmless.

Const STALL_MINUTES = 15   ' 通常ループは40秒前後で1周する。15分止まっていれば異常

Dim h
h = Hour(Now)
If h < 9 Or h >= 23 Then WScript.Quit
If h = 22 Then
    If Minute(Now) >= 30 Then WScript.Quit
End If

Dim sh, fso
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Dim logFile
logFile = "C:\kiseki\windows-agent\watchdog.log"

Sub WriteLog(msg)
    Dim ts
    Set ts = fso.OpenTextFile(logFile, 8, True)
    ts.WriteLine Now & " " & msg
    ts.Close
End Sub

' ---- heartbeat が STALL_MINUTES 以上更新されていなければ True ----
Function IsStalled(hbPath)
    IsStalled = False
    If Not fso.FileExists(hbPath) Then Exit Function   ' 旧版エージェント等: 判定不能→触らない
    Dim f
    Set f = fso.GetFile(hbPath)
    If DateDiff("n", f.DateLastModified, Now) >= STALL_MINUTES Then IsStalled = True
End Function

Dim wmi, procs
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")

Dim umaFound, jvFound, umaPid, jvPid
umaFound = False
jvFound = False
umaPid = 0
jvPid = 0
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            umaFound = True
            umaPid = p.ProcessId
        End If
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            jvFound = True
            jvPid = p.ProcessId
        End If
    End If
Next

' ---- [2] STALLED: 生きているがループが止まっている場合は kill して落とす ----
Dim hbUma, hbJv
hbUma = "C:\kiseki\windows-agent\data\realtime_heartbeat_umaconn.txt"
hbJv  = "C:\kiseki\windows-agent\data\realtime_heartbeat_jvlink.txt"

If umaFound Then
    If IsStalled(hbUma) Then
        WriteLog "umaconn realtime STALLED (heartbeat > " & STALL_MINUTES & "min) -> terminating PID=" & umaPid
        sh.Run "taskkill /PID " & umaPid & " /F", 0, True
        umaFound = False
    End If
End If

If jvFound Then
    If IsStalled(hbJv) Then
        WriteLog "jvlink realtime STALLED (heartbeat > " & STALL_MINUTES & "min) -> terminating PID=" & jvPid
        sh.Run "taskkill /PID " & jvPid & " /F", 0, True
        jvFound = False
    End If
End If

' ---- [1] MISSING: 不在（または上で落とした）なら起動タスクを実行 ----
If Not umaFound Then
    WriteLog "umaconn realtime process not found -> starting kiseki-UmaConn-Realtime"
    sh.Run "schtasks /run /tn kiseki-UmaConn-Realtime", 0, False
End If

If Not jvFound Then
    WriteLog "jvlink realtime process not found -> starting kiseki-JVLink-Realtime"
    sh.Run "schtasks /run /tn kiseki-JVLink-Realtime", 0, False
End If
