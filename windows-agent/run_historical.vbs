' kiseki-Historical VBS起動スクリプト
' jvlink_historical.py を pythonw.exe (コンソールウィンドウなし) で起動する。
' 既に実行中なら起動しない（WMI 経由でプロセスチェック）。
'
' InteractiveToken で実行することで JVDTLab.dll がデスクトップセッションを取得できる。
' kiseki-Historical タスクから呼び出される。

Option Explicit

Dim objWMI, colProcesses, objProcess
Dim blRunning
Dim strPython, strScript, strArgs, strCmd
Dim objShell

' ----- 多重起動チェック -----
blRunning = False
Set objWMI = GetObject("winmgmts:\\.\root\cimv2")
Set colProcesses = objWMI.ExecQuery( _
    "SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")

For Each objProcess In colProcesses
    If InStr(LCase(objProcess.CommandLine), "jvlink_historical") > 0 Then
        blRunning = True
        Exit For
    End If
Next

Set colProcesses = Nothing
Set objWMI = Nothing

If blRunning Then
    ' 既に実行中 → 何もせず終了
    WScript.Quit 0
End If

' ----- 起動 -----
strPython = "C:\Python312-32\pythonw.exe"
strScript = "C:\kiseki\windows-agent\jvlink_historical.py"
strArgs   = "--mode all --time-limit 7200"

Set objShell = CreateObject("WScript.Shell")
objShell.Run strPython & " " & strScript & " " & strArgs, 0, False

Set objShell = Nothing
WScript.Quit 0
