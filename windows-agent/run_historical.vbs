' kiseki-Historical VBS起動スクリプト
' jvlink_historical.py を pythonw.exe (コンソールウィンドウなし) で起動する。
' 既に実行中なら起動しない（WMI 経由でプロセスチェック）。
'
' InteractiveToken で実行することで JVDTLab.dll がデスクトップセッションを取得できる。
' kiseki-Historical タスクから呼び出される。
'
' ハング回収 (2026-08-05 追加):
'   jvlink_historical は --time-limit 7200 (2時間) で自ら止まる設計だが、これは
'   「ファイル完了単位の graceful stop」なので **JVOpen 内でブロックしていると効かない**。
'   2026-08-04 16:23 起動のものは JVOpen から 11.7時間 返らなかった。
'
'   さらにこの状態を回収する仕組みが無かった:
'     - run_realtime_watchdog.vbs の [1]〜[4] は realtime 専用
'     - run_eod_cleanup.vbs の [B] は jvlink_agent / umaconn_agent しかマッチしない
'       (同日の正規バックフィルを巻き添えにしないため jvlink_historical は意図的に除外)
'
'   → 本スクリプト自身が回収する。タスクは4時間おきに発火するので、
'     HUNG_MINUTES を超えたものを落としてから起動しなおせば最大でも4時間で復帰する。
'     健全な実行は time_limit 2時間 + 後処理で終わるので 3時間を閾値にする。
'
'   taskkill (TerminateProcess) を使うのは意図的。DLL_PROCESS_DETACH を走らせないので
'   JV-Link / UmaConn のリークダイアログを出さずに落とせる。
'
'   なお JVOpen ブロックの根本対処は event ディレクトリのクリアと JVLinkAgent 再起動
'   (CLAUDE.md「jvlink_agent トラブルシューティング」)。ここでは滞留を止めるだけ。

Option Explicit

Const HUNG_MINUTES = 180   ' time_limit 7200s(2h) + 後処理。3時間超は JVOpen ハングとみなす

Dim objWMI, colProcesses, objProcess
Dim blRunning, lngAge
Dim strPython, strScript, strArgs, strFromDate
Dim objShell, objFSO, strLog

Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")
strLog = "C:\kiseki\windows-agent\watchdog.log"

Sub WriteLog(msg)
    Dim ts
    Set ts = objFSO.OpenTextFile(strLog, 8, True)
    ts.WriteLine Now & " " & msg
    ts.Close
End Sub

' WMI の CreationDate (yyyymmddHHMMSS.mmmmmm+ZZZ) を分単位の経過時間に変換する
Function AgeMinutes(wmiDate)
    AgeMinutes = -1
    If IsNull(wmiDate) Then Exit Function
    If Len(wmiDate) < 14 Then Exit Function
    Dim d
    On Error Resume Next
    d = DateSerial(CInt(Mid(wmiDate, 1, 4)), CInt(Mid(wmiDate, 5, 2)), CInt(Mid(wmiDate, 7, 2))) _
      + TimeSerial(CInt(Mid(wmiDate, 9, 2)), CInt(Mid(wmiDate, 11, 2)), CInt(Mid(wmiDate, 13, 2)))
    If Err.Number <> 0 Then
        Err.Clear
        On Error Goto 0
        Exit Function
    End If
    On Error Goto 0
    AgeMinutes = DateDiff("n", d, Now)
End Function

' ----- 多重起動チェック（ハングしていれば回収する）-----
blRunning = False
Set objWMI = GetObject("winmgmts:\\.\root\cimv2")
Set colProcesses = objWMI.ExecQuery( _
    "SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")

For Each objProcess In colProcesses
    If Not IsNull(objProcess.CommandLine) Then
        If InStr(LCase(objProcess.CommandLine), "jvlink_historical") > 0 Then
            lngAge = AgeMinutes(objProcess.CreationDate)
            If lngAge >= HUNG_MINUTES Then
                WriteLog "jvlink_historical HUNG (" & lngAge & "min, JVOpen block suspected) -> terminating PID=" & objProcess.ProcessId
                objShell.Run "taskkill /PID " & objProcess.ProcessId & " /F", 0, True
            Else
                blRunning = True
            End If
        End If
    End If
Next

Set colProcesses = Nothing
Set objWMI = Nothing

If blRunning Then
    ' 正常に実行中 → 何もせず終了
    WScript.Quit 0
End If

' ----- 起動 -----
strPython = "C:\Python312-32\pythonw.exe"
strScript = "C:\kiseki\windows-agent\jvlink_historical.py"
' 🔴 --from-date を必ず渡すこと。省略すると jvlink_historical の既定 20000101 が使われ、
'    RACE も UM も 26 年分を舐めて JVOpen が 3600 秒の上限を超えてタイムアウトする。
'    2026-08-06〜08-08 の全 14 回がこれで失敗し、8/8 に本タスクは Disabled にされた。
'    差分取得なので直近 90 日もあれば取りこぼさない（未処理ファイルは completed で管理）。
Dim dtFrom
dtFrom = DateAdd("d", -90, Date())
strFromDate = Right("0000" & Year(dtFrom), 4) & Right("00" & Month(dtFrom), 2) & Right("00" & Day(dtFrom), 2)
strArgs   = "--mode all --time-limit 7200 --from-date " & strFromDate

objShell.Run strPython & " " & strScript & " " & strArgs, 0, False

Set objShell = Nothing
WScript.Quit 0
