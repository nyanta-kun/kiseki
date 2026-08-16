' kiseki chokyo (training) daily fetch launcher
'
' Invoked by: kiseki-Chokyo-Daily task (daily 06:00)
'
' 調教データ（坂路 SLOP / ウッド WOOD）を差分(option=1)で取り込む。
'
' Why 必要か（2026-08-17 に判明）:
'   調教取得は自動実行されていなかった。kiseki-Chokyo-Setup は最終実行 2026-06-07 で
'   Next 未設定、CHOKYO の実行履歴は手動のみ。そのため 8/15〜8/16 開催週の調教が
'   丸ごと欠けていた（DB 最終日が 2026-08-11 のまま）。
'
' Why 貯めることが唯一の道か:
'   JRA-VAN は差分を 2025-05-27 より前まで保持していない（probe_chokyo_retention.py
'   の実測。option=1 に古い from_time を渡しても件数が増えない）。option=4 の
'   セットアップは JVOpen が返らない。つまり過去へのバックフィルは不可能で、
'   評価窓は「これから貯める」しかない。ここが止まると戦略そのものが成立しない。
'
' Why 06:00 か:
'   realtime は 9:00-22:30。07:00 の kiseki-JRA-Entries-RT より前で、
'   22:00/23:00/23:45/23:50 の各ジョブとも重ならない空き枠。
'
' Why 遡り日数を持たせるか:
'   追い切りは水木に集中し、ファイルの到着も遅れうる。取得済みファイルは
'   {SLOP,WOOD}_CHOKYO で記録され再取得はスキップされるので、窓を広めに取っても
'   コストはほぼ増えない（実測 60 秒）。

Option Explicit
On Error Resume Next

Const LOOKBACK_DAYS = 14

Dim fso, logPath
Set fso = CreateObject("Scripting.FileSystemObject")
logPath = "C:\kiseki\windows-agent\chokyo.log"

Sub WriteLog(msg)
    ' ⚠️ 入口で Err.Clear すること。呼び出し元の Err が残っていると
    ' 「エラーを報告するための関数」が自分の Err チェックで弾かれる。
    Err.Clear
    Dim f
    Set f = fso.OpenTextFile(logPath, 8, True)
    f.WriteLine Now & " " & msg
    f.Close
End Sub

Dim wmi, procs, p, chokyoRunning, realtimeRunning
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
chokyoRunning = False
realtimeRunning = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 Then
            If InStr(p.CommandLine, "--mode chokyo") > 0 Then chokyoRunning = True
            If InStr(p.CommandLine, "--mode realtime") > 0 Then realtimeRunning = True
        End If
    End If
Next

' 多重起動の防止。前回分が走っていればそのまま継続させる。
If chokyoRunning Then
    WriteLog "skip: chokyo already running"
    WScript.Quit
End If

' JV-Link を realtime と奪い合わない。当日のオッズ取得を壊す方が損害が大きい。
If realtimeRunning Then
    WriteLog "skip: jvlink realtime is running"
    WScript.Quit
End If

Dim d, fromDate, cmd, sh
d = DateAdd("d", -LOOKBACK_DAYS, Date)
fromDate = Year(d) & Right("0" & Month(d), 2) & Right("0" & Day(d), 2)
cmd = "C:\Python312-32\pythonw.exe jvlink_agent.py --mode chokyo --from-date " & fromDate

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\kiseki\windows-agent"
sh.Run cmd, 0, False
WriteLog "started: jvlink_agent.py --mode chokyo --from-date " & fromDate
