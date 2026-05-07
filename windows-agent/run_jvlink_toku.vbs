' kiseki JV-Link 週次プレビュー取得（毎週水曜 19:00 自動実行）
'
' Invoked by: kiseki-JVLink-TOKU-Wed task
'
' Behavior:
'   - 全レース（非特別含む）の RA レース情報（名称・クラス等）を取得
'   - 特別競走の特別登録馬（TK レコード）を取得
'   - 既に jvlink_agent.py --mode weekly-preview が走っていればスキップ
'   - fire-and-forget: pythonw を Wait=False で起動

On Error Resume Next

Dim wmi, procs, alreadyRunning
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
alreadyRunning = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 And InStr(p.CommandLine, "--mode weekly-preview") > 0 Then
            alreadyRunning = True
            Exit For
        End If
    End If
Next

If alreadyRunning Then WScript.Quit

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\kiseki\windows-agent"
WshShell.Run "C:\Python312-32\pythonw.exe jvlink_agent.py --mode weekly-preview", 0, False
