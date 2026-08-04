$ErrorActionPreference = "Stop"
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "C:\kiseki\windows-agent\run_jvlink_toku.vbs"
$trigger = New-ScheduledTaskTrigger -Daily -At 18:00
$principal = New-ScheduledTaskPrincipal -UserId "ysuzuki" -RunLevel Limited -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$settings.DisallowStartIfOnBatteries = $false
$settings.StopIfGoingOnBatteries = $false
Register-ScheduledTask -TaskName "kiseki-JVLink-TOKU" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "kiseki TOKU daily at 18:00" -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName "kiseki-JVLink-TOKU"
$task = Get-ScheduledTask -TaskName "kiseki-JVLink-TOKU"
Write-Host "Registered: $($task.TaskName)"
Write-Host "  State       : $($task.State)"
Write-Host "  NextRunTime : $($info.NextRunTime)"
Write-Host "  Battery     : $($task.Settings.DisallowStartIfOnBatteries)"
Write-Host "  StopOnBatt  : $($task.Settings.StopIfGoingOnBatteries)"
