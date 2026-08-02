' kiseki end-of-day cleanup (daily 23:45)
'
' Forcibly terminate leftover kiseki agent processes so the next morning's 9:00
' schedule starts from a clean state.
'
' Targets:
'   [A] jvlink_agent.py / umaconn_agent.py  --mode realtime   (any age)
'   [B] jvlink_agent.py / umaconn_agent.py  ANY mode, started BEFORE today
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

On Error Resume Next

Dim sh, fso, logFile, ts
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
logFile = "C:\kiseki\windows-agent\watchdog.log"

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
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
killed = 0
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        Dim isAgent, isRealtime, isStale, reason
        isAgent = (InStr(p.CommandLine, "jvlink_agent.py") > 0) Or _
                  (InStr(p.CommandLine, "umaconn_agent.py") > 0)
        isRealtime = isAgent And (InStr(p.CommandLine, "realtime") > 0)
        isStale = isAgent And (ProcDateStr(p.CreationDate) <> "") And _
                  (ProcDateStr(p.CreationDate) < todayStr)

        reason = ""
        If isRealtime Then reason = "realtime"
        If isStale Then reason = "stale(started " & ProcDateStr(p.CreationDate) & ")"

        If reason <> "" Then
            Set ts = fso.OpenTextFile(logFile, 8, True)
            ts.WriteLine Now & " EOD cleanup [" & reason & "]: terminating PID=" & _
                         p.ProcessId & " (" & p.CommandLine & ")"
            ts.Close
            p.Terminate
            killed = killed + 1
        End If
    End If
Next

Set ts = fso.OpenTextFile(logFile, 8, True)
ts.WriteLine Now & " EOD cleanup done. terminated=" & killed
ts.Close
