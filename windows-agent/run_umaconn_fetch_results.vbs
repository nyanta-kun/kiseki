' kiseki UmaConn 本日成績の定期取得 (5分おき)
' realtime の 0B12 worker が止まっている場合のバックアップ
' 動作時間帯: 10:00 - 22:30 (それ以外はスキップ)

Dim h
h = Hour(Now)
If h < 10 Or h >= 23 Then
    WScript.Quit
End If

' 22時台かつ 30分以降は停止
If h = 22 Then
    If Minute(Now) >= 30 Then WScript.Quit
End If

Dim today
today = Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2)

Dim sh
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\kiseki\windows-agent"
sh.Run "C:\Python312-32\pythonw.exe umaconn_agent.py --mode fetch-results --fetch-date " & today, 0, False
