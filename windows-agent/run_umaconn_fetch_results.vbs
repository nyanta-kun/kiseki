' kiseki UmaConn 本日成績の定期取得 (5分おき)
' realtime の 0B12 worker が止まっている場合のバックアップ
' 動作時間帯: 10:00 - 22:30 (それ以外はスキップ)
'
' 多重起動を禁止する (2026-08-04 追加):
'   本スクリプトは5分おきに起動するが、fetch-results 1回の所要時間はレース数に比例する。
'   46レースあった 2026-08-04 は **21分** かかった (17:28 開始 -> 17:49 完了)。
'   ガードが無いと4本以上が積み上がり (16:35 / 16:46 / 16:52 / 16:56 の実例)、
'   UmaConn COM を奪い合って NVSetServiceKey が60秒タイムアウトする。
'   realtime の初期化まで巻き添えになり、heartbeat を書けないまま watchdog に
'   STALLED 判定されて kill される、という連鎖を起こしていた。
'
'   STUCK_MINUTES を超えたものは本当に固まっているとみなして taskkill する。
'   taskkill (TerminateProcess) は DLL_PROCESS_DETACH を走らせないので、
'   UmaConn の FastMM リークダイアログを出さずに回収できる。

Option Explicit

Const STUCK_MINUTES = 30   ' 通常は数分〜21分。30分を超えたら異常とみなす

Dim h
h = Hour(Now)
If h < 10 Or h >= 23 Then WScript.Quit
If h = 22 Then
    If Minute(Now) >= 30 Then WScript.Quit
End If

Dim fso, logFile
Set fso = CreateObject("Scripting.FileSystemObject")
logFile = "C:\kiseki\windows-agent\watchdog.log"

Sub WriteLog(msg)
    Dim ts
    Set ts = fso.OpenTextFile(logFile, 8, True)
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

Dim sh
Set sh = CreateObject("WScript.Shell")

' ---- 既に fetch-results が走っていれば起動しない ----
Dim wmi, procs, p, running, age
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
running = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And _
           InStr(p.CommandLine, "fetch-results") > 0 Then
            age = AgeMinutes(p.CreationDate)
            If age >= STUCK_MINUTES Then
                WriteLog "umaconn fetch-results STUCK (" & age & "min) -> terminating PID=" & p.ProcessId
                sh.Run "taskkill /PID " & p.ProcessId & " /F", 0, True
            Else
                running = True
            End If
        End If
    End If
Next

' 実行中のものがあれば今回はスキップ (積み上げない)
If running Then WScript.Quit 0

Dim today
today = Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2)

sh.CurrentDirectory = "C:\kiseki\windows-agent"
sh.Run "C:\Python312-32\pythonw.exe umaconn_agent.py --mode fetch-results --fetch-date " & today, 0, False
