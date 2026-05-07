# kiseki JV-Link 週次エントリー取得タスク登録
#
# 使い方（管理者権限の PowerShell で実行）:
#   powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\setup_weekly_entry_tasks.ps1
#
# 登録内容:
#   - kiseki-JVLink-TOKU-Wed  : 毎週水曜 19:00 特別登録馬（想定）取得
#   - kiseki-JVLink-Race-Thu  : 毎週木曜 19:00 出馬表（確定）取得

$WorkDir = "C:\kiseki\windows-agent"

# -----------------------------------------------------------------------
# タスク1: 毎週水曜 19:00 - 特別登録馬（TOKU）取得
# -----------------------------------------------------------------------
$TokuTaskName = "kiseki-JVLink-TOKU-Wed"

if (Get-ScheduledTask -TaskName $TokuTaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TokuTaskName -Confirm:$false
    Write-Host "既存タスク '$TokuTaskName' を削除しました。"
}

$TokuAction = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "//B //NoLogo $WorkDir\run_jvlink_toku.vbs" `
    -WorkingDirectory $WorkDir

# 毎週水曜 19:00
$TokuTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Wednesday -At "19:00"

$TokuSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -WakeToRun $false

Register-ScheduledTask `
    -TaskName $TokuTaskName `
    -Action $TokuAction `
    -Trigger $TokuTrigger `
    -Settings $TokuSettings `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "タスク '$TokuTaskName' を登録しました。"
Write-Host "  起動: 毎週水曜 19:00"
Write-Host "  実行: run_jvlink_toku.vbs (--mode toku)"
Write-Host ""

# -----------------------------------------------------------------------
# タスク2: 毎週木曜 19:00 - 出馬表（確定）取得
# -----------------------------------------------------------------------
$RaceTaskName = "kiseki-JVLink-Race-Thu"

if (Get-ScheduledTask -TaskName $RaceTaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $RaceTaskName -Confirm:$false
    Write-Host "既存タスク '$RaceTaskName' を削除しました。"
}

$RaceAction = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "//B //NoLogo $WorkDir\run_jvlink_race.vbs" `
    -WorkingDirectory $WorkDir

# 毎週木曜 19:00
$RaceTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Thursday -At "19:00"

$RaceSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -WakeToRun $false

Register-ScheduledTask `
    -TaskName $RaceTaskName `
    -Action $RaceAction `
    -Trigger $RaceTrigger `
    -Settings $RaceSettings `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "タスク '$RaceTaskName' を登録しました。"
Write-Host "  起動: 毎週木曜 19:00"
Write-Host "  実行: run_jvlink_race.vbs (--mode daily)"
Write-Host ""

Write-Host "=== 登録完了 ==="
Write-Host "確認: Get-ScheduledTask | Where-Object { `$_.TaskName -like 'kiseki-JVLink-*' } | Select-Object TaskName, State"
Write-Host "手動起動(TOKU): Start-ScheduledTask -TaskName '$TokuTaskName'"
Write-Host "手動起動(Race): Start-ScheduledTask -TaskName '$RaceTaskName'"
