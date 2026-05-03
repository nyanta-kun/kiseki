' kiseki JV-Link TOKU 取得（毎日 18:00 自動実行・idempotent）
'
' Invoked by: kiseki-JVLink-TOKU task
'
' Behavior:
'   - 既に jvlink_agent.py --mode toku が走っていればスキップ
'   - JV-Link は同時 1 接続だが TOKU は数分で完了するので realtime 終了後の 18:00 に走らせる
'   - fire-and-forget: pythonw を Wait=False で起動

On Error Resume Next

Dim wmi, procs, alreadyRunning
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
alreadyRunning = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 And InStr(p.CommandLine, "--mode toku") > 0 Then
            alreadyRunning = True
            Exit For
        End If
    End If
Next

If alreadyRunning Then WScript.Quit

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\kiseki\windows-agent"
WshShell.Run "C:\Python312-32\pythonw.exe jvlink_agent.py --mode toku", 0, False
