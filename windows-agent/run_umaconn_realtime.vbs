' kiseki UmaConn realtime launcher (fire-and-forget, idempotent)
'
' Invoked by:
'   - kiseki-UmaConn-Realtime task (daily 9:00)
'   - kiseki-UmaConn-Watchdog task (every 5min when realtime is missing)
'
' Skips when umaconn_agent.py --mode realtime is already running AND making
' progress (heartbeat fresh), so concurrent watchdog and scheduled-task triggers
' cannot stack duplicate processes. A process that is present but frozen -- or a
' corpse that taskkill cannot reclaim -- is NOT counted as running: see the
' 2026-08-20 note below.

On Error Resume Next

' ---- 「動いている」ではなく「進んでいる」で判定する (2026-08-20) ----
' プロセスの存在だけを見ていたため、終了処理の途中で固まって taskkill も
' 受け付けなくなったプロセス（＝死体）を「もう動いている」と数えて降りていた。
' watchdog は5分ごとに kill を試みては失敗し、ここで毎回降りるので再起動が
' 永久に始まらない。実測で地方のオッズが 4時間51分 止まった。
' → heartbeat が STALL_MINUTES 以上古ければ、プロセスが残っていても
'   代わりを起動する。死体と併存しても新しいプロセスは正常に動く。
Const STALL_MINUTES = 15
Const DEDUP_WINDOW_SEC = 120

Dim HB_PATH
HB_PATH = "C:\kiseki\windows-agent\data\realtime_heartbeat_umaconn.txt"

Dim wmi, procs, aliveCount, newestBorn
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
aliveCount = 0
newestBorn = ""
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            aliveCount = aliveCount + 1
            If Not IsNull(p.CreationDate) Then
                If p.CreationDate > newestBorn Then newestBorn = p.CreationDate
            End If
        End If
    End If
Next

If aliveCount > 0 Then
    If Not IsFrozen(HB_PATH, newestBorn) Then WScript.Quit   ' 正常稼働中 -> 何もしない
    sh2Log "umaconn realtime alive but heartbeat is stale -> launching a replacement (existing PIDs are frozen or unkillable)"
End If

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
' 対象は「たった今の競合」だけに絞る (2026-08-20)。
' 以前は最古の1本を残していたため、殺せない死体が居るときに死体が「最古」として
' 生き残り、たった今起動した健全なプロセスの方が殺されていた。
Dim recent
Set recent = CreateObject("Scripting.Dictionary")
For Each k In arr.Keys
    Dim born
    born = WmiDateToDate(arr(k))
    If Not IsEmpty(born) Then
        If DateDiff("s", born, Now) <= DEDUP_WINDOW_SEC Then recent.Add k, arr(k)
    End If
Next

If recent.Count > 1 Then
    Dim keepPid, keepDate
    keepPid = -1
    keepDate = ""
    For Each k In recent.Keys
        If keepDate = "" Or recent(k) < keepDate Then
            keepDate = recent(k)
            keepPid = k
        End If
    Next
    For Each k In recent.Keys
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

' ---- WMI の CreationDate (yyyymmddHHMMSS.mmmmmm+ZZZ) を日付に変換する ----
Function WmiDateToDate(wmiDate)
    WmiDateToDate = Empty
    If IsNull(wmiDate) Then Exit Function
    If Len(wmiDate) < 14 Then Exit Function
    On Error Resume Next
    WmiDateToDate = DateSerial(CInt(Mid(wmiDate, 1, 4)), CInt(Mid(wmiDate, 5, 2)), CInt(Mid(wmiDate, 7, 2))) _
                  + TimeSerial(CInt(Mid(wmiDate, 9, 2)), CInt(Mid(wmiDate, 11, 2)), CInt(Mid(wmiDate, 13, 2)))
    If Err.Number <> 0 Then
        WmiDateToDate = Empty
        Err.Clear
    End If
End Function

' ---- heartbeat が STALL_MINUTES 以上古ければ True（起動直後は猶予する） ----
' 猶予の理由は watchdog の IsStalled と同じ: heartbeat ファイルはプロセスを
' またいで使い回されるので、起動直後のプロセスは前のプロセスが残した古い
' ファイルで判定されてしまう。初期化 (NVInit / NVSetServiceKey) が詰まると
' 最初の heartbeat はさらに遅れる。
' 判定不能（ファイルが無い）ときは False を返す。旧版エージェントを
' 多重起動させないため、迷ったら起動しない側へ倒す。
Function IsFrozen(hbPath, newestWmiDate)
    IsFrozen = False
    Dim fsoH
    Set fsoH = CreateObject("Scripting.FileSystemObject")
    If Not fsoH.FileExists(hbPath) Then Exit Function
    If DateDiff("n", fsoH.GetFile(hbPath).DateLastModified, Now) < STALL_MINUTES Then Exit Function

    Dim startedAt
    startedAt = WmiDateToDate(newestWmiDate)
    If IsEmpty(startedAt) Then
        IsFrozen = True
        Exit Function
    End If
    If DateDiff("n", startedAt, Now) < STALL_MINUTES Then Exit Function   ' 起動直後は猶予
    IsFrozen = True
End Function
