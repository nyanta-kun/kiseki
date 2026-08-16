' kiseki JV-Link BLDN full launcher (night window only)
'
' Invoked by: kiseki-JVLink-BldnFull task (daily 22:45)
' Stopped by: kiseki-JVLink-BldnFull-Stop task (daily 08:00)
'
' BLDN の累積マスタ(571 files)を取り直し、HN の親コード
' (sire_breeding_code / dam_breeding_code) を keiba.breeding_horses へ入れる。
' これが入ると繁殖登録番号で何代でも系図を遡れるようになり、
' インブリード判定に netkeiba のスクレイピングが要らなくなる。
'
' 2026-08-16 実測: 開催日の日中に JVOpen(BLDN, option=4) を呼んだところ
' 22分戻らず JV-Link 枠を占有した。realtime が止まる 22:30 以降にだけ走らせる。
'
' ファイルは到着ごとに完了マークされるので途中で止めても進捗は残る(冪等)。
' 一晩で終わらなければ複数夜に分けてよい。

On Error Resume Next

Dim fso, logPath
Set fso = CreateObject("Scripting.FileSystemObject")
logPath = "C:\kiseki\windows-agent\bldn_full.log"

Sub WriteLog(msg)
    Err.Clear
    Dim ts, f
    ts = Now
    Set f = fso.OpenTextFile(logPath, 8, True)
    f.WriteLine ts & " " & msg
    f.Close
End Sub

Dim wmi, procs, p, bldnRunning, realtimeRunning
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
bldnRunning = False
realtimeRunning = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 Then
            If InStr(p.CommandLine, "--mode bldn-full") > 0 Then bldnRunning = True
            If InStr(p.CommandLine, "--mode realtime") > 0 Then realtimeRunning = True
        End If
    End If
Next

' 多重起動の防止。前夜から走り続けている場合はそのまま継続させる。
If bldnRunning Then
    WriteLog "skip: bldn-full already running"
    WScript.Quit
End If

' realtime と同時に走らせない。JVOpen が長時間ブロックして当日の取得を壊す。
If realtimeRunning Then
    WriteLog "skip: jvlink realtime is still running"
    WScript.Quit
End If

Dim sh
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\kiseki\windows-agent"
sh.Run "C:\Python312-32\pythonw.exe jvlink_agent.py --mode bldn-full", 0, False
WriteLog "started: jvlink_agent.py --mode bldn-full"
