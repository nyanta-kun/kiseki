' kiseki UmaConn backfill launcher (night window only)
'
' Invoked by: kiseki-UmaConn-Backfill task (daily 23:50)
' Stopped by: kiseki-UmaConn-Backfill-Stop task (daily 08:30)
'
' 蓄積系(NVOpen / RACE dataspec)で 2024-01 以降を回収する。
' 2026-08-13 の実測では NVOpen が rc=0 / 読込12283ファイル / DL数12283 を返し、
' 37分間 NVRead=-3 のまま1件も返らなかった。全期間の再ダウンロードを伴うため、
' レース日の日中に走らせると UmaConn COM を占有して当日のオッズ・結果収集を壊す。
' → realtime が終了した夜間だけ走らせ、翌朝 9:00 の realtime 起動前に必ず止める。
'
' ファイルは到着ごとに mark_file_completed されるので、途中で止めても進捗は残る。
' 一晩で終わらなければ複数夜に分けてよい（冪等）。

On Error Resume Next

Dim fso, logPath
Set fso = CreateObject("Scripting.FileSystemObject")
logPath = "C:\kiseki\windows-agent\backfill.log"

Sub WriteLog(msg)
    Dim ts, f
    ts = Now
    Set f = fso.OpenTextFile(logPath, 8, True)
    f.WriteLine ts & " " & msg
    f.Close
End Sub

Dim wmi, procs, p, backfillRunning, realtimeRunning
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
backfillRunning = False
realtimeRunning = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 Then
            If InStr(p.CommandLine, "--mode recent") > 0 Then backfillRunning = True
            If InStr(p.CommandLine, "--mode realtime") > 0 Then realtimeRunning = True
        End If
    End If
Next

' 多重起動の防止。前夜から走り続けている場合はそのまま継続させる。
If backfillRunning Then
    WriteLog "skip: backfill already running"
    WScript.Quit
End If

' realtime と同時に走らせない。COM を奪い合うと当日のオッズ取得が止まる。
If realtimeRunning Then
    WriteLog "skip: realtime is still running"
    WScript.Quit
End If

Dim sh
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\kiseki\windows-agent"
sh.Run "C:\Python312-32\pythonw.exe umaconn_agent.py --mode recent --from-year 2024", 0, False
WriteLog "started: umaconn_agent.py --mode recent --from-year 2024"
