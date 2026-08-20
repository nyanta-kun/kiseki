' kiseki realtime watchdog (5min interval)
'
' Restart kiseki-UmaConn-Realtime / kiseki-JVLink-Realtime task if realtime process is
' missing OR stalled (process alive but the loop stopped making progress).
' Active hours: 9:00 - 22:30
'
' This watchdog monitors BOTH:
'   - umaconn_agent.py --mode realtime  (chihou)
'   - jvlink_agent.py  --mode realtime  (JRA)
'
' [1] MISSING  : process not found            -> run the restart task
' [2] STALLED  : process alive but the heartbeat file has not been updated for
'                STALL_MINUTES               -> terminate the process, then restart
' [3] SERVICE  : the JVLinkAgent Windows service is not running
'                                             -> start the service
' [4] STALE    : realtime process started on an earlier day
'                                             -> terminate, then restart
'
' Why [4] exists (2026-08-04 incident):
'   kiseki-EOD-Cleanup returned exit code 1 on both 2026-08-02 and 2026-08-03 and wrote
'   nothing to this log, so its nightly sweep silently did nothing. A jvlink_agent
'   realtime process started 2026-08-02 11:57 was still running on 08-04 — three days
'   old. It was NOT detectable by [1] (present), [2] (heartbeat fresh: the watchdog
'   thread was alive) or [3] (service Running). EOD cleanup is the only other net and
'   it had failed, so nothing would ever have reclaimed it.
'   Realtime is only ever launched at 09:00 or by this watchdog (09:00-22:30), so a
'   process whose creation date is before today is by definition a leftover.
'
' Why [2] exists (2026-08-02 incident):
'   jvlink_agent realtime stayed alive but JVRTOpen hung at the COM level and froze
'   the in-process watchdog thread too, so os._exit(1) never fired. Odds collection
'   was dead for ~95 minutes while the process looked healthy to a presence check.
'   The agents' watchdog threads now touch data\realtime_heartbeat_*.txt every 30s;
'   if that stops, everything is frozen and only an external kill can recover it.
'
' Why [3] exists:
'   A stopped JVLinkAgent service silences JVRTOpen/JVOpen without producing any
'   error log. The agent process stays alive and its watchdog thread keeps touching
'   the heartbeat file, so BOTH [1] and [2] see a perfectly healthy agent while no
'   data arrives at all. This is a third, independent failure mode and needs its own
'   probe. (Ported from fix/migration-down-revision, 2026-05-23 — a branch whose name
'   had nothing to do with its contents, which is why it sat unmerged for months.)
'
' The launcher VBS is itself idempotent (skips if already running) so a stray
' double-fire is harmless.
'
' Why the kill result is now verified (2026-08-20 incident):
'   umaconn realtime のプロセス内ウォッチドッグが os._exit(1) を撃った直後、
'   最後のスレッドがカーネル待ちのまま固まり、プロセスが「終了処理の途中」で
'   居座った（スレッド1本・ハンドル485・CPU 加算なし）。この状態のプロセスは
'   TerminateProcess を受け付けず、taskkill は
'   「実行中のタスクのインスタンスがありません」で失敗する。
'   本 watchdog は kill の成否を見ずに [1] の再起動へ進んでいたが、ランチャ側の
'   「もう動いている」判定がこの死体を生きたプロセスと数えて降りるため、
'   5分ごとに「STALLED -> terminating / not found -> starting」を繰り返すだけで
'   再起動は一度も起きなかった。地方のオッズが 14:55 から 19:48 まで
'   **4時間51分** 止まった（発走直前のオッズが最後まで朝の値のままだった）。
'   → kill の成否を確認してログに残し、ランチャ側は heartbeat で
'     「生きている」ではなく「進んでいる」を判定するようにした。

Const STALL_MINUTES = 15   ' 通常ループは40秒前後で1周する。15分止まっていれば異常

Dim h
h = Hour(Now)
If h < 9 Or h >= 23 Then WScript.Quit
If h = 22 Then
    If Minute(Now) >= 30 Then WScript.Quit
End If

Dim sh, fso
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Dim logFile
logFile = "C:\kiseki\windows-agent\watchdog.log"

Sub WriteLog(msg)
    Dim ts
    Set ts = fso.OpenTextFile(logFile, 8, True)
    ts.WriteLine Now & " " & msg
    ts.Close
End Sub

' ---- WMI の CreationDate (yyyymmddHHMMSS.mmmmmm+ZZZ) が今日より前なら True ----
Function IsStaleProc(wmiDate)
    IsStaleProc = False
    If IsNull(wmiDate) Then Exit Function
    If Len(wmiDate) < 8 Then Exit Function
    Dim todayStr
    todayStr = Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2)
    If Left(wmiDate, 8) < todayStr Then IsStaleProc = True
End Function

' ---- WMI の CreationDate を VBScript の日付に変換する（失敗時は Empty） ----
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
    On Error Goto 0
End Function

' ---- heartbeat が STALL_MINUTES 以上更新されていなければ True ----
'
' 起動直後のプロセスに猶予を与える（2026-08-04 追加）:
'   heartbeat ファイルはプロセスをまたいで使い回される。エージェントが最初の
'   heartbeat を書くのは起動から30秒後で、初期化 (NVInit / NVSetServiceKey) が
'   長引くとさらに遅れる。そのため起動直後のプロセスは **前のプロセスが残した
'   古いファイル** で判定されてしまい、まだ何も悪いことをしていないのに
'   「STALLED」として殺される。
'   実測 2026-08-04: UmaConn の COM 競合で初期化が詰まり、起動→15分でkill→再起動
'   を繰り返した (16:55 / 17:48)。
'
'   → heartbeat が古くても、プロセス起動から STALL_MINUTES 経っていなければ猶予する。
'     「本当に固まっている」なら猶予明けに改めて捕まえられるので取りこぼしはない。
Function IsStalled(hbPath, wmiCreationDate)
    IsStalled = False
    If Not fso.FileExists(hbPath) Then Exit Function   ' 旧版エージェント等: 判定不能→触らない
    Dim f
    Set f = fso.GetFile(hbPath)
    If DateDiff("n", f.DateLastModified, Now) < STALL_MINUTES Then Exit Function  ' 新鮮 → 正常

    ' heartbeat は古い。それが今のプロセスの責任かどうかを起動時刻で切り分ける。
    Dim startedAt
    startedAt = WmiDateToDate(wmiCreationDate)
    If IsEmpty(startedAt) Then
        IsStalled = True   ' 起動時刻が読めない場合は従来どおり停止扱い
        Exit Function
    End If
    If DateDiff("n", startedAt, Now) < STALL_MINUTES Then Exit Function  ' 猶予中

    IsStalled = True
End Function

Dim wmi
Set wmi = GetObject("winmgmts:\\.\root\cimv2")

' ---- taskkill で殺し、本当に消えたかを確認する（消えなくても True を返さない） ----
' 戻り値は「消えたか」。消えなかった場合も呼び出し側は再起動へ進むこと。
' 死体が居座っていても新しいプロセスは正常に起動できる（COM は解放済み。
' 2026-08-20 実測: 死体と併存させた realtime が問題なくオッズ取得を再開した）。
Function KillRealtime(pid, label)
    sh.Run "taskkill /PID " & pid & " /F", 0, True
    WScript.Sleep 1000
    Dim leftovers
    Set leftovers = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE ProcessId=" & pid)
    If leftovers.Count > 0 Then
        WriteLog label & " PID=" & pid & " survived taskkill (stuck exiting / unkillable) -> restarting alongside it"
        KillRealtime = False
    Else
        KillRealtime = True
    End If
End Function

' ---- [3] SERVICE: JVLinkAgent サービスが止まっていれば起動する ----
' プロセス確認より先に行う。サービスが落ちている状態でエージェントだけ再起動しても
' JVRTOpen は黙って失敗し続けるため、土台を先に戻す。
' エージェント自体の再起動は [1]/[2] に委ねる（サービス復帰後も JV-Link を掴み直せない
' 場合は、次の tick で heartbeat が止まり STALLED として回収される）。
'
' 起動を schtasks 経由にしている理由（ここを net start に戻してはいけない）:
'   本 watchdog タスク kiseki-UmaConn-Watchdog は RunLevel=Limited で動く。
'   JVLinkAgent の ACL は SERVICE_START(RP) を BA(Administrators)/SY にしか与えて
'   おらず、IU(Interactive Users) には無い。したがって非昇格の net start は
'   アクセス拒否で失敗する。さらに sh.Run(..., 0, False) は結果を待たないため、
'   その失敗はログにもコンソールにも一切現れない（「starting」と記録されるのに
'   実際には何も起きない）。RunLevel=Highest の専用タスクに委譲して回避する。
'   タスク登録: windows-agent/register_start_jvlinkagent_task.ps1
Dim svcs, svc
Set svcs = wmi.ExecQuery("SELECT * FROM Win32_Service WHERE Name='JVLinkAgent'")
For Each svc In svcs
    If LCase(svc.State) <> "running" Then
        WriteLog "JVLinkAgent service is '" & svc.State & "' -> requesting elevated start (JVRTOpen fails silently while stopped)"
        sh.Run "schtasks /run /tn kiseki-Start-JVLinkAgent", 0, False
    End If
Next

Dim procs
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")

' ---- 該当プロセスは「1本だけ」とは限らない（2026-08-20） ----
' taskkill が効かない死体が残ると、健全なプロセスと併存する。以前は For Each で
' 最後に見つかった1本の PID だけを覚えていたため、どちらを掴むかが WMI の
' 列挙順まかせになり、死体を無視して健全な方を殺しうる。全部を辞書に集めて
' 1本ずつ判定する。
Dim umaPids, jvPids
Set umaPids = CreateObject("Scripting.Dictionary")
Set jvPids = CreateObject("Scripting.Dictionary")
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(p.CommandLine, "umaconn_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            umaPids.Add p.ProcessId, p.CreationDate
        End If
        If InStr(p.CommandLine, "jvlink_agent.py") > 0 And InStr(p.CommandLine, "realtime") > 0 Then
            jvPids.Add p.ProcessId, p.CreationDate
        End If
    End If
Next

' ---- [4] STALE: 前日以前に起動した realtime は EOD cleanup の取りこぼし。落とす ----
' heartbeat が新しくても落とす。3日居座った実例があり、日付跨ぎのプロセスを
' 生かしておく正当な理由が無いため。
SweepStale umaPids, "umaconn realtime"
SweepStale jvPids, "jvlink realtime"

' ---- [2] STALLED: 生きているがループが止まっている場合は kill して落とす ----
' 判定は「最も新しいプロセスの起動時刻」で行う。heartbeat ファイルはプロセスを
' またいで使い回されるので、死体の起動時刻で猶予を計算すると、起動直後の
' 健全なプロセスまで巻き添えで殺してしまう。
Dim hbUma, hbJv
hbUma = "C:\kiseki\windows-agent\data\realtime_heartbeat_umaconn.txt"
hbJv  = "C:\kiseki\windows-agent\data\realtime_heartbeat_jvlink.txt"

SweepStalled umaPids, hbUma, "umaconn realtime"
SweepStalled jvPids, hbJv, "jvlink realtime"

' ---- [1] MISSING: 不在（または上で落とした）なら起動タスクを実行 ----
' 死体が残っていて Count が 0 にならない場合も、ここへ来なければ再起動されない。
' そのため SweepStalled は kill の成否によらず辞書を空にする。
If umaPids.Count = 0 Then
    WriteLog "umaconn realtime process not found -> starting kiseki-UmaConn-Realtime"
    sh.Run "schtasks /run /tn kiseki-UmaConn-Realtime", 0, False
End If

If jvPids.Count = 0 Then
    WriteLog "jvlink realtime process not found -> starting kiseki-JVLink-Realtime"
    sh.Run "schtasks /run /tn kiseki-JVLink-Realtime", 0, False
End If

' ---- 前日以前に起動したプロセスを全部落として辞書から外す ----
Sub SweepStale(pids, label)
    Dim k, doomed
    Set doomed = CreateObject("Scripting.Dictionary")
    For Each k In pids.Keys
        If IsStaleProc(pids(k)) Then doomed.Add k, True
    Next
    For Each k In doomed.Keys
        WriteLog label & " STALE (started before today) -> terminating PID=" & k
        KillRealtime k, label
        pids.Remove k
    Next
End Sub

' ---- heartbeat が止まっていれば全部落として辞書から外す ----
Sub SweepStalled(pids, hbPath, label)
    If pids.Count = 0 Then Exit Sub
    Dim k, newest
    newest = ""
    For Each k In pids.Keys
        If IsNull(pids(k)) = False Then
            If newest = "" Or pids(k) > newest Then newest = pids(k)
        End If
    Next
    If Not IsStalled(hbPath, newest) Then Exit Sub
    For Each k In pids.Keys
        WriteLog label & " STALLED (heartbeat > " & STALL_MINUTES & "min) -> terminating PID=" & k
        KillRealtime k, label
    Next
    pids.RemoveAll
End Sub
