# kiseki-Chokyo-Daily の登録
#
# 調教データ（坂路/ウッド）の差分取得を毎日 06:00 に回す。
#
# 2026-08-17 まで調教は自動取得されておらず（最終自動実行 2026-06-07）、
# 開催週の調教が欠けたまま気づかれない状態だった。JRA-VAN の差分は
# 2025-05-27 より前を保持しておらず過去へのバックフィルもできないため、
# 「これから貯める」以外に評価窓を伸ばす手段がない。止めてはいけないジョブ。
#
# 実行: powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\register_chokyo_daily_task.ps1
$ErrorActionPreference = 'Stop'

$principal = New-ScheduledTaskPrincipal -UserId 'ysuzuki' -LogonType Interactive -RunLevel Limited

# 06:00 は realtime(9:00-22:30) の外で、07:00 の kiseki-JRA-Entries-RT より前の空き枠。
$action  = New-ScheduledTaskAction -Execute 'C:\Windows\System32\wscript.exe' `
             -Argument 'C:\kiseki\windows-agent\run_chokyo_daily.vbs'
$trigger = New-ScheduledTaskTrigger -Daily -At '06:00'
# 差分取得は実測 60 秒。長引くのは異常なので 30 分で打ち切る。
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
              -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName 'kiseki-Chokyo-Daily' -Action $action `
  -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName 'kiseki-Chokyo-Daily'
$i = $t | Get-ScheduledTaskInfo
Write-Output ("{0,-24} state={1} next={2}" -f $t.TaskName, $t.State, $i.NextRunTime)
