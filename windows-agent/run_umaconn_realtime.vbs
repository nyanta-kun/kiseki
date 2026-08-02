' kiseki UmaConn realtime launcher (fire-and-forget, idempotent)
'
' Invoked by:
'   - kiseki-UmaConn-Realtime task (daily 9:00)
'   - kiseki-UmaConn-Watchdog task (every 5min when realtime is missing)
'
' Skips when umaconn_agent.py --mode realtime is already running, so concurrent
' watchdog and scheduled-task triggers cannot stack duplicate processes.

On Error Resume Next

Dim wmi, procs, alreadyRunning
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
alreadyRunning = False
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            alreadyRunning = True
            Exit For
        End If
    End If
Next

If alreadyRunning Then WScript.Quit

' ---- 起動レースの防止 (2026-08-02) ----
' 「実行中か調べる→起動する」は check-then-act の競合になっており、
' タスク実行とウォッチドッグがほぼ同時に走ると全員が「未起動」と判定して
' 多重起動する（実測: 1〜2秒差で3プロセス）。多重起動は JV-Link/NV の COM を
' 奪い合い、オッズ取得が止まる原因になる。
' ロックファイルの更新時刻で「他のランチャが起動処理中」を検出して降りる。
Const LOCK_STALE_SEC = 60
Dim lockPath, fsoL
lockPath = "C:\kiseki\windows-agent\data\launcher_umaconn.lock"
Set fsoL = CreateObject("Scripting.FileSystemObject")
If fsoL.FileExists(lockPath) Then
    If DateDiff("s", fsoL.GetFile(lockPath).DateLastModified, Now) < LOCK_STALE_SEC Then
        WScript.Quit   ' 直前に別のランチャが起動処理を始めている
    End If
End If
If Not fsoL.FolderExists("C:\kiseki\windows-agent\data") Then
    fsoL.CreateFolder("C:\kiseki\windows-agent\data")
End If
Dim lockTs
Set lockTs = fsoL.CreateTextFile(lockPath, True)
lockTs.WriteLine Now
lockTs.Close

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\kiseki\windows-agent"
WshShell.Run "C:\Python312-32\pythonw.exe umaconn_agent.py --mode realtime", 0, False

' ---- 念のための後始末: 競合をすり抜けて多重起動した場合は最古の1本だけ残す ----
WScript.Sleep 4000
Dim procs2, arr, i, j, tmp
Set procs2 = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
Set arr = CreateObject("Scripting.Dictionary")
For Each p2 In procs2
    If Not IsNull(p2.CommandLine) Then
        If InStr(p2.CommandLine, "umaconn_agent.py") > 0 And InStr(p2.CommandLine, "realtime") > 0 Then
            arr.Add p2.ProcessId, p2.CreationDate
        End If
    End If
Next
If arr.Count > 1 Then
    Dim keepPid, keepDate
    keepPid = -1
    keepDate = ""
    For Each k In arr.Keys
        If keepDate = "" Or arr(k) < keepDate Then
            keepDate = arr(k)
            keepPid = k
        End If
    Next
    For Each k In arr.Keys
        If k <> keepPid Then
            sh2Log "duplicate umaconn_agent.py realtime PID=" & k & " -> terminate (keep " & keepPid & ")"
            On Error Resume Next
            GetObject("winmgmts:\\.\root\cimv2:Win32_Process.Handle='" & k & "'").Terminate
        End If
    Next
End If

Sub sh2Log(msg)
    Dim fso3, ts3
    Set fso3 = CreateObject("Scripting.FileSystemObject")
    Set ts3 = fso3.OpenTextFile("C:\kiseki\windows-agent\watchdog.log", 8, True)
    ts3.WriteLine Now & " launcher: " & msg
    ts3.Close
End Sub
