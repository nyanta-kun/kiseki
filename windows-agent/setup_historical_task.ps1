# kiseki-Historical task registration (24h, every 2h)
$taskName = "kiseki-Historical"
$vbsPath  = "C:\kiseki\windows-agent\run_historical.vbs"
$logDir   = "C:\kiseki\windows-agent"

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "`"$vbsPath`"" `
    -WorkingDirectory $logDir

$triggerHours = 0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22
$triggers = @()
foreach ($h in $triggerHours) {
    $t = New-ScheduledTaskTrigger -Daily -At ("{0:D2}:00" -f $h)
    $triggers += $t
}

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances    IgnoreNew `
    -ExecutionTimeLimit   (New-TimeSpan -Hours 3) `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId    $env:USERNAME `
    -LogonType Interactive `
    -RunLevel  Highest

$task = Register-ScheduledTask `
    -TaskName  $taskName `
    -Action    $action `
    -Trigger   $triggers `
    -Settings  $settings `
    -Principal $principal `
    -Force

if ($task) {
    Write-Host "[OK] Task registered: $taskName"
} else {
    Write-Host "[NG] Failed. Try running as Administrator."
    exit 1
}
