' kiseki JV-Link RACE 出馬表取得（毎週木曜 19:00 自動実行）
'
' Invoked by: kiseki-JVLink-Race-Thu task
'
' Behavior:
'   - 既に jvlink_agent.py --mode daily が走っていればスキップ
'   - 木曜に確定した週末の出馬表（RA/SE）を取得してバックエンドへ送信
'   - fire-and-forget: pythonw を Wait=False で起動

On Error Resume Next

Dim wmi, procs, alreadyRunning
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
alreadyRunning = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 And InStr(p.CommandLine, "--mode daily") > 0 Then
            alreadyRunning = True
            Exit For
        End If
    End If
Next

If alreadyRunning Then WScript.Quit

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\kiseki\windows-agent"
WshShell.Run "C:\Python312-32\pythonw.exe jvlink_agent.py --mode daily", 0, False
