# =============================================================================
# kiseki-Start-JVLinkAgent タスクの登録
#
# run_realtime_watchdog.vbs の [3] SERVICE 検知から呼ばれる、JVLinkAgent
# Windows サービスを起動するためだけの昇格タスク。
#
# なぜ watchdog から直接 `net start` しないのか:
#   watchdog タスク (kiseki-UmaConn-Watchdog) は RunLevel=Limited で動作する。
#   JVLinkAgent の ACL は SERVICE_START(RP) を BA/SY にしか与えておらず、
#   IU(Interactive Users) には無い:
#     D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)
#       (A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)
#                ^^^^ IU に RP(SERVICE_START) が無い
#   そのため非昇格の net start はアクセス拒否になる。さらに watchdog 側の
#   sh.Run(..., 0, False) は結果を待たないので、その失敗は一切表面化しない。
#
#   サービスの ACL を書き換える (sc sdset) 選択肢もあるが、常時 SERVICE_START を
#   非管理者へ開放するより、起動操作だけを昇格タスクに委譲するほうが権限が狭い。
#
# 実行（管理者権限の PowerShell が必要）:
#   powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\register_start_jvlinkagent_task.ps1
#
# 削除（元に戻す場合）:
#   schtasks /delete /tn kiseki-Start-JVLinkAgent /f
# =============================================================================

$ErrorActionPreference = "Stop"

$taskName = "kiseki-Start-JVLinkAgent"

$isAdmin = (New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    throw "管理者権限が必要です。管理者として PowerShell を起動して再実行してください。"
}

# Execute は絶対パスで指定すること。Task Scheduler は PATH を解決しないため
# "net.exe" と書くと LastTaskResult=2 (ERROR_FILE_NOT_FOUND) で無言で失敗する。
$netExe = Join-Path $env:SystemRoot "System32\net.exe"
$action = New-ScheduledTaskAction -Execute $netExe -Argument "start JVLinkAgent"

# RunLevel=Highest が本タスクの存在理由。ここを Limited にすると意味が無くなる。
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

# トリガーは持たせない。watchdog からの `schtasks /run` でのみ起動する。
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Principal $principal `
    -Settings $settings `
    -Description "JVLinkAgent サービスを起動する（run_realtime_watchdog.vbs から呼ばれる昇格タスク）" `
    -Force | Out-Null

Write-Output "登録しました: $taskName"
Get-ScheduledTask -TaskName $taskName |
    Select-Object TaskName,
        @{n = "RunLevel"; e = { $_.Principal.RunLevel } },
        @{n = "UserId";   e = { $_.Principal.UserId } } |
    Format-Table -AutoSize
