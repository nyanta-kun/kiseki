# kiseki UmaConn Realtime Agent - Windows タスクスケジューラ登録スクリプト
#
# 使い方（管理者権限の PowerShell で実行）:
#   powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\setup_task_scheduler.ps1
#
# 登録内容:
#   - タスク名: kiseki-UmaConn-Realtime
#   - トリガー: 毎日 09:00 に起動
#   - 動作: umaconn_agent.py --mode realtime を非表示ウィンドウで実行
#   - 失敗時: 5分後に最大3回リトライ
#   - ログオン不要（バックグラウンド実行）

$TaskName = "kiseki-UmaConn-Realtime"
$WorkDir  = "C:\kiseki\windows-agent"
$PythonExe = "python"
$Script   = "umaconn_agent.py"
$StartTime = "09:00"

# 既存タスクを削除（上書き登録）
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "既存タスク '$TaskName' を削除しました。"
}

# アクション定義
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c cd /d $WorkDir && $PythonExe $Script --mode realtime >> umaconn_agent.log 2>&1" `
    -WorkingDirectory $WorkDir

# トリガー定義（毎日 09:00）
$Trigger = New-ScheduledTaskTrigger -Daily -At $StartTime

# 設定定義
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 13) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -WakeToRun $false

# タスク登録（現在のログインユーザーとして実行）
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "タスク '$TaskName' を登録しました。"
Write-Host "  起動時刻: 毎日 $StartTime"
Write-Host "  実行: $WorkDir\$Script --mode realtime"
Write-Host "  失敗時: 5分後に最大3回リトライ"
Write-Host ""
Write-Host "確認コマンド: Get-ScheduledTask -TaskName '$TaskName' | Select-Object TaskName, State"
Write-Host "手動起動:     Start-ScheduledTask -TaskName '$TaskName'"
