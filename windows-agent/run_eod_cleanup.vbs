' kiseki end-of-day cleanup (daily 23:45)
'
' Forcibly terminate leftover kiseki agent processes so the next morning's 9:00
' schedule starts from a clean state.
'
' Targets:
'   [A] jvlink_agent.py / umaconn_agent.py  --mode realtime   (any age)
'   [B] jvlink_agent.py / umaconn_agent.py  ANY mode, started BEFORE today
'   [C] jvlink_historical.py                started BEFORE today
'
' Why [C] exists (2026-08-05 incident):
'   jvlink_historical was deliberately excluded from [B] so a same-day backfill would
'   not be killed mid-run. That left it with NO reclamation path at all: the realtime
'   watchdog only covers realtime, and its own --time-limit is a graceful stop at file
'   boundaries which never fires while JVOpen itself blocks. A run started
'   2026-08-04 16:23 was still inside JVOpen 11.7 hours later.
'   Same-day hangs are now reclaimed by run_historical.vbs (HUNG_MINUTES, every 4h);
'   [C] is the day-boundary net, matching what [B] does for the other agents.
'
' Why [B] exists (2026-08-02 incident):
'   `jvlink_agent.py --mode daily` had been hung since 2026-07-16 (17 days, 13h CPU)
'   and `umaconn_agent.py --mode fetch-results` since 2026-07-31, both holding the
'   JV-Link / UmaConn COM. The morning realtime agent started but could not fetch a
'   single odds record. The old cleanup only matched "--mode realtime", so these
'   zombies survived every nightly sweep.
'   Restricting [B] to processes started before today keeps同日の正規バックフィル
'   (jvlink_historical / fetch-results 等) を巻き添えにしない。
'
' This is the safety net for hung COM/JV-Link/NV calls that the in-process
' watchdogs (jvlink 1800s, umaconn 600s) sometimes fail to interrupt.
'
' NOTE: Task trigger is set to 23:45 (not 23:00) so UmaConn can process
'   the SENV race results files published at ~23:10 JST before being killed.

' Diagnosability (2026-08-04):
'   On 2026-08-02 and 2026-08-03 this task reported exit code 1 and wrote NOTHING to
'   watchdog.log, leaving a 3-day-old realtime process behind. With a blanket
'   `On Error Resume Next` and no output before the WMI query, there was no way to tell
'   "never ran" from "ran and crashed early". We now write a start marker first and
'   surface suppressed errors instead of swallowing them.
'   run_realtime_watchdog.vbs [4] STALE is the backstop for when this fails anyway.

On Error Resume Next

Dim sh, fso, logFile
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
logFile = "C:\kiseki\windows-agent\watchdog.log"

' 呼び出し元の Err を必ず先にクリアすること。
' `On Error Resume Next` 下では Err は明示的に消すまで残るため、これが無いと
' 「エラーを報告するための WriteLog」が入口の Err.Number チェックで弾かれ、
' 記録したい失敗のときだけ何も書けない（このスクリプトが直そうとしている症状そのもの）。
' 呼び出し側は msg の中で Err.Number / Err.Description を先に文字列化しているので、
' ここでクリアしても情報は失われない。
Sub WriteLog(msg)
    Dim t
    Err.Clear
    Set t = fso.OpenTextFile(logFile, 8, True)
    If Err.Number = 0 Then
        t.WriteLine Now & " " & msg
        t.Close
    End If
    Err.Clear
End Sub

WriteLog "EOD cleanup start."

' WMI の CreationDate (yyyymmddHHMMSS.mmmmmm+ZZZ) から yyyymmdd を取り出す
Function ProcDateStr(wmiDate)
    ProcDateStr = ""
    If IsNull(wmiDate) Then Exit Function
    If Len(wmiDate) < 8 Then Exit Function
    ProcDateStr = Left(wmiDate, 8)
End Function

Dim todayStr
todayStr = Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2)

Dim wmi, procs, killed
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
If Err.Number <> 0 Then
    WriteLog "EOD cleanup ABORT: WMI connect failed (" & Err.Number & " " & Err.Description & ")"
    WScript.Quit 1
End If
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
If Err.Number <> 0 Then
    WriteLog "EOD cleanup ABORT: WMI query failed (" & Err.Number & " " & Err.Description & ")"
    WScript.Quit 1
End If
killed = 0
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        Dim isAgent, isBatch, isRealtime, isStale, isOldBatch, reason
        isAgent = (InStr(p.CommandLine, "jvlink_agent.py") > 0) Or _
                  (InStr(p.CommandLine, "umaconn_agent.py") > 0)
        isBatch = (InStr(LCase(p.CommandLine), "jvlink_historical") > 0)
        isRealtime = isAgent And (InStr(p.CommandLine, "realtime") > 0)
        isStale = isAgent And (ProcDateStr(p.CreationDate) <> "") And _
                  (ProcDateStr(p.CreationDate) < todayStr)
        ' [C] 日跨ぎの historical。同日分は run_historical.vbs (HUNG_MINUTES) が回収する
        isOldBatch = isBatch And (ProcDateStr(p.CreationDate) <> "") And _
                     (ProcDateStr(p.CreationDate) < todayStr)

        reason = ""
        If isRealtime Then reason = "realtime"
        If isStale Then reason = "stale(started " & ProcDateStr(p.CreationDate) & ")"
        If isOldBatch Then reason = "historical stale(started " & ProcDateStr(p.CreationDate) & ")"

        If reason <> "" Then
            WriteLog "EOD cleanup [" & reason & "]: terminating PID=" & _
                     p.ProcessId & " (" & p.CommandLine & ")"
            p.Terminate
            If Err.Number <> 0 Then
                WriteLog "EOD cleanup: terminate FAILED for PID=" & p.ProcessId & _
                         " (" & Err.Number & " " & Err.Description & ")"
                Err.Clear
            Else
                killed = killed + 1
            End If
        End If
    End If
Next

WriteLog "EOD cleanup done. terminated=" & killed
