' kiseki JV-Link bldn-full stopper
'
' Invoked by: kiseki-JVLink-BldnFull-Stop task (daily 08:00)
'
' 夜間の BLDN 累積取得を realtime 起動前に確実に落とす。
' 止めても completed 済みファイルの進捗は残るので翌夜に続きから再開できる。
'
' Terminate は TerminateProcess 相当で DLL_PROCESS_DETACH を走らせない。
' JVDTLab.dll がダイアログを出したまま居座るのを避けるため意図的にこちらを使う。

Option Explicit

On Error Resume Next

Dim fso, logPath
Set fso = CreateObject("Scripting.FileSystemObject")
logPath = "C:\kiseki\windows-agent\bldn_full.log"

Sub WriteLog(msg)
    Err.Clear
    Dim f
    Set f = fso.OpenTextFile(logPath, 8, True)
    f.WriteLine Now & " " & msg
    f.Close
End Sub

Dim wmi, procs, p, killed
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
killed = 0
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 And InStr(p.CommandLine, "--mode bldn-full") > 0 Then
            p.Terminate
            killed = killed + 1
        End If
    End If
Next

If killed > 0 Then
    WriteLog "stopped: " & killed & " bldn-full process(es) before realtime window"
Else
    WriteLog "stop: no bldn-full process running"
End If
