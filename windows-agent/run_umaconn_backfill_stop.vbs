' kiseki UmaConn backfill stopper
'
' Invoked by: kiseki-UmaConn-Backfill-Stop task (daily 08:30)
'
' 夜間バックフィル(--mode recent)を 9:00 の realtime 起動前に確実に落とす。
' 止めても completed 済みファイルの進捗は残るので、翌夜に続きから再開できる。
'
' Terminate は TerminateProcess 相当で DLL_PROCESS_DETACH を走らせない。
' NVDTLab.dll(Delphi/FastMM) のメモリリークダイアログを出さずに落とすため、
' 意図的にこちらを使う（正常終了を待つと pythonw がダイアログで居座る）。

Option Explicit

On Error Resume Next

Dim fso, logPath
Set fso = CreateObject("Scripting.FileSystemObject")
logPath = "C:\kiseki\windows-agent\backfill.log"

Sub WriteLog(msg)
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
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And InStr(p.CommandLine, "--mode recent") > 0 Then
            p.Terminate
            killed = killed + 1
        End If
    End If
Next

If killed > 0 Then
    WriteLog "stopped: " & killed & " backfill process(es) before realtime window"
Else
    WriteLog "stop: no backfill process running"
End If
