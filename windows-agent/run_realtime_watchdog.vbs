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
' [3] SERVICE  : the JVLinkAgent Windows service is not running
'                                             -> start the service
' [4] STALE    : realtime process started on an earlier day
'                                             -> terminate, then restart
'
' Why [4] exists (2026-08-04 incident):
'   kiseki-EOD-Cleanup returned exit code 1 on both 2026-08-02 and 2026-08-03 and wrote
'   nothing to this log, so its nightly sweep silently did nothing. A jvlink_agent
'   realtime process started 2026-08-02 11:57 was still running on 08-04 — three days
'   old. It was NOT detectable by [1] (present), [2] (heartbeat fresh: the watchdog
'   thread was alive) or [3] (service Running). EOD cleanup is the only other net and
'   it had failed, so nothing would ever have reclaimed it.
'   Realtime is only ever launched at 09:00 or by this watchdog (09:00-22:30), so a
'   process whose creation date is before today is by definition a leftover.
'
' Why [2] exists (2026-08-02 incident):
'   jvlink_agent realtime stayed alive but JVRTOpen hung at the COM level and froze
'   the in-process watchdog thread too, so os._exit(1) never fired. Odds collection
'   was dead for ~95 minutes while the process looked healthy to a presence check.
'   The agents' watchdog threads now touch data\realtime_heartbeat_*.txt every 30s;
'   if that stops, everything is frozen and only an external kill can recover it.
'
' Why [3] exists:
'   A stopped JVLinkAgent service silences JVRTOpen/JVOpen without producing any
'   error log. The agent process stays alive and its watchdog thread keeps touching
'   the heartbeat file, so BOTH [1] and [2] see a perfectly healthy agent while no
'   data arrives at all. This is a third, independent failure mode and needs its own
'   probe. (Ported from fix/migration-down-revision, 2026-05-23 — a branch whose name
'   had nothing to do with its contents, which is why it sat unmerged for months.)
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

' ---- WMI の CreationDate (yyyymmddHHMMSS.mmmmmm+ZZZ) が今日より前なら True ----
Function IsStaleProc(wmiDate)
    IsStaleProc = False
    If IsNull(wmiDate) Then Exit Function
    If Len(wmiDate) < 8 Then Exit Function
    Dim todayStr
    todayStr = Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2)
    If Left(wmiDate, 8) < todayStr Then IsStaleProc = True
End Function

' ---- heartbeat が STALL_MINUTES 以上更新されていなければ True ----
Function IsStalled(hbPath)
    IsStalled = False
    If Not fso.FileExists(hbPath) Then Exit Function   ' 旧版エージェント等: 判定不能→触らない
    Dim f
    Set f = fso.GetFile(hbPath)
    If DateDiff("n", f.DateLastModified, Now) >= STALL_MINUTES Then IsStalled = True
End Function

Dim wmi
Set wmi = GetObject("winmgmts:\\.\root\cimv2")

' ---- [3] SERVICE: JVLinkAgent サービスが止まっていれば起動する ----
' プロセス確認より先に行う。サービスが落ちている状態でエージェントだけ再起動しても
' JVRTOpen は黙って失敗し続けるため、土台を先に戻す。
' エージェント自体の再起動は [1]/[2] に委ねる（サービス復帰後も JV-Link を掴み直せない
' 場合は、次の tick で heartbeat が止まり STALLED として回収される）。
'
' 起動を schtasks 経由にしている理由（ここを net start に戻してはいけない）:
'   本 watchdog タスク kiseki-UmaConn-Watchdog は RunLevel=Limited で動く。
'   JVLinkAgent の ACL は SERVICE_START(RP) を BA(Administrators)/SY にしか与えて
'   おらず、IU(Interactive Users) には無い。したがって非昇格の net start は
'   アクセス拒否で失敗する。さらに sh.Run(..., 0, False) は結果を待たないため、
'   その失敗はログにもコンソールにも一切現れない（「starting」と記録されるのに
'   実際には何も起きない）。RunLevel=Highest の専用タスクに委譲して回避する。
'   タスク登録: windows-agent/register_start_jvlinkagent_task.ps1
Dim svcs, svc
Set svcs = wmi.ExecQuery("SELECT * FROM Win32_Service WHERE Name='JVLinkAgent'")
For Each svc In svcs
    If LCase(svc.State) <> "running" Then
        WriteLog "JVLinkAgent service is '" & svc.State & "' -> requesting elevated start (JVRTOpen fails silently while stopped)"
        sh.Run "schtasks /run /tn kiseki-Start-JVLinkAgent", 0, False
    End If
Next

Dim procs
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")

Dim umaFound, jvFound, umaPid, jvPid, umaStale, jvStale
umaFound = False
jvFound = False
umaPid = 0
jvPid = 0
umaStale = False
jvStale = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            umaFound = True
            umaPid = p.ProcessId
            umaStale = IsStaleProc(p.CreationDate)
        End If
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            jvFound = True
            jvPid = p.ProcessId
            jvStale = IsStaleProc(p.CreationDate)
        End If
    End If
Next

' ---- [4] STALE: 前日以前に起動した realtime は EOD cleanup の取りこぼし。落とす ----
' heartbeat が新しくても落とす。3日居座った実例があり、日付跨ぎのプロセスを
' 生かしておく正当な理由が無いため。
If umaFound And umaStale Then
    WriteLog "umaconn realtime STALE (started before today) -> terminating PID=" & umaPid
    sh.Run "taskkill /PID " & umaPid & " /F", 0, True
    umaFound = False
End If

If jvFound And jvStale Then
    WriteLog "jvlink realtime STALE (started before today) -> terminating PID=" & jvPid
    sh.Run "taskkill /PID " & jvPid & " /F", 0, True
    jvFound = False
End If

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
