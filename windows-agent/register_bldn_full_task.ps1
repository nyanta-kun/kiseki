# kiseki-JVLink-BldnFull / -Stop の登録
#
# BLDN の累積マスタ取得は JV-Link を長時間占有する
# (2026-08-16 実測: 開催日の日中に JVOpen(BLDN,option=4) が 22分戻らなかった)。
# realtime が終了した夜間だけ走らせ、翌朝の realtime 起動前に必ず止める。
#
# 登録:
#   powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\register_bldn_full_task.ps1
$ErrorActionPreference = 'Stop'

$principal = New-ScheduledTaskPrincipal -UserId 'ysuzuki' -LogonType Interactive -RunLevel Limited

# --- 開始: 毎日 22:45 (jvlink realtime は 22:30 に終了する) ---
$startAction  = New-ScheduledTaskAction -Execute 'C:\Windows\System32\wscript.exe' `
                  -Argument 'C:\kiseki\windows-agent\run_jvlink_bldn_full.vbs'
$startTrigger = New-ScheduledTaskTrigger -Daily -At '22:45'
$startSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
                  -ExecutionTimeLimit (New-TimeSpan -Hours 9) `
                  -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName 'kiseki-JVLink-BldnFull' -Action $startAction `
  -Trigger $startTrigger -Principal $principal -Settings $startSettings -Force | Out-Null

# --- 停止: 毎日 08:00 (9:00 の realtime 起動前) ---
$stopAction  = New-ScheduledTaskAction -Execute 'C:\Windows\System32\wscript.exe' `
                 -Argument 'C:\kiseki\windows-agent\run_jvlink_bldn_full_stop.vbs'
$stopTrigger = New-ScheduledTaskTrigger -Daily -At '08:00'
$stopSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
                 -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'kiseki-JVLink-BldnFull-Stop' -Action $stopAction `
  -Trigger $stopTrigger -Principal $principal -Settings $stopSettings -Force | Out-Null

foreach ($n in 'kiseki-JVLink-BldnFull','kiseki-JVLink-BldnFull-Stop') {
  $t = Get-ScheduledTask -TaskName $n
  $i = $t | Get-ScheduledTaskInfo
  Write-Output ("{0,-32} state={1} next={2}" -f $t.TaskName, $t.State, $i.NextRunTime)
}
