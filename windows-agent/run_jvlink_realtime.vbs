On Error Resume Next
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\kiseki\windows-agent"

Dim STOP_HOUR : STOP_HOUR = 22   ' 22:00以降は再起動しない

Do While True
    Dim h : h = Hour(Now)
    If h >= STOP_HOUR Then Exit Do

    ' jvlink_agent realtime を起動し、終了を待つ
    WshShell.Run "C:\Python312-32\pythonw.exe jvlink_agent.py --mode realtime", 0, True

    ' 停止時刻を再確認してから10秒待ち再起動
    h = Hour(Now)
    If h >= STOP_HOUR Then Exit Do
    WScript.Sleep 10000
Loop
