' kiseki JRA 確定成績フォールバック取得（22:00 / 全レース確定後）
'
' Why: 0B12 は確定直後には RA + 上位3頭の SE + HR しか載せず、残りの SE は
' 数十分〜数時間遅れて載る。realtime は定期的に呼び戻しているが、最終レース直後の
' 分は当日の稼働時間内に取り切れないことがある。蓄積系 JVOpen が固着している間は
' 週次取込による穴埋めも効かないため、1日1回まとめて取り直す。
'
' JVOpen が復旧したらこのタスクは無効化してよい（実行しても冪等）。
' 文字列リテラルは ASCII のみ。行終端は CRLF（windows-agent/.gitattributes 参照）。
Option Explicit
On Error Resume Next
Dim today, sh
today = Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\kiseki\windows-agent"
sh.Run "C:\Python312-32\pythonw.exe fetch_results_rt.py --date " & today, 0, False
