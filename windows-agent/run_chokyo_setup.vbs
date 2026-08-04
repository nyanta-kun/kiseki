' kiseki-Chokyo-Setup: 調教データ全期間backfill(option=4 setup)を realtime停止後に単独起動する。
' 前回(2026-05-31)は realtime と同時起動して JVOpen が7日間ハングした。
' EOD-Cleanup(23:00)で realtime 停止後に本VBSを実行し、クリーンな単独 JVOpen を確保する。
' 前処理: 残存 JVNextCore を kill + event ディレクトリ clear ([[jvlink_issues]] の手順)。
On Error Resume Next
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' --- 1) 残存 JVNextCore を kill ---
WshShell.Run "taskkill /F /IM JVNextCore.exe", 0, True

' --- 2) JV-Link event ディレクトリを clear (stale event が JVOpen をブロックする) ---
WshShell.Run "cmd /c del /q /s ""C:\ProgramData\JRA-VAN\Data Lab\event\*""", 0, True

' --- 3) JVLinkAgent サービス再起動 (best-effort・要管理者) ---
WshShell.Run "powershell -Command ""Restart-Service JVLinkAgent -ErrorAction SilentlyContinue""", 0, True

WScript.Sleep 5000

' --- 4) 調教 setup(option=4・全期間)を pythonw で単独起動 (日付フロア20230101) ---
WshShell.CurrentDirectory = "C:\kiseki\windows-agent"
WshShell.Run "C:\Python312-32\pythonw.exe jvlink_agent.py --mode chokyo --setup --from-date 20230101", 0, False
