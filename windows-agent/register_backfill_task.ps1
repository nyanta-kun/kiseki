# kiseki-UmaConn-Backfill / -Stop の登録
#
# 蓄積系(NVOpen)の全期間再ダウンロードは UmaConn COM を長時間占有するため、
# realtime が終了した夜間だけ走らせ、翌朝の realtime 起動前に必ず止める。
$ErrorActionPreference = 'Stop'

$principal = New-ScheduledTaskPrincipal -UserId 'ysuzuki' -LogonType Interactive -RunLevel Limited

# --- 開始: 毎日 23:50 (realtime は約23:20に終了する) ---
$startAction  = New-ScheduledTaskAction -Execute 'C:\Windows\System32\wscript.exe' `
                  -Argument 'C:\kiseki\windows-agent\run_umaconn_backfill.vbs'
$startTrigger = New-ScheduledTaskTrigger -Daily -At '23:50'
$startSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
                  -ExecutionTimeLimit (New-TimeSpan -Hours 10) `
                  -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName 'kiseki-UmaConn-Backfill' -Action $startAction `
  -Trigger $startTrigger -Principal $principal -Settings $startSettings -Force | Out-Null

# --- 停止: 毎日 08:30 (9:00 の realtime 起動前) ---
$stopAction  = New-ScheduledTaskAction -Execute 'C:\Windows\System32\wscript.exe' `
                 -Argument 'C:\kiseki\windows-agent\run_umaconn_backfill_stop.vbs'
$stopTrigger = New-ScheduledTaskTrigger -Daily -At '08:30'
$stopSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
                 -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'kiseki-UmaConn-Backfill-Stop' -Action $stopAction `
  -Trigger $stopTrigger -Principal $principal -Settings $stopSettings -Force | Out-Null

foreach ($n in 'kiseki-UmaConn-Backfill','kiseki-UmaConn-Backfill-Stop') {
  $t = Get-ScheduledTask -TaskName $n
  $i = $t | Get-ScheduledTaskInfo
  Write-Output ("{0,-32} state={1} next={2}" -f $t.TaskName, $t.State, $i.NextRunTime)
}
