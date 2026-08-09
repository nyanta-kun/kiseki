' kiseki JRA 出馬表フォールバック取得（毎朝 07:00 / 平日休日問わず）
'
' Why: 蓄積系 JVOpen が 2026-08-06 18:00 から固着しており、出馬表(RACE)が
' 取り込めない。放置すると当日の出走表は netkeiba の出走想定のままで
' 枠番・馬番が入らず、07:30 の指数算出が枠順なしで走る（2026-08-09 に発生）。
' 0B15(JVRTOpen) は別チャネルなので JVOpen が返らない状況でも出馬表が取れる。
'
' JVOpen が復旧したらこのタスクは無効化してよい（実行しても冪等で無害）。
' 文字列リテラルは ASCII のみ。行終端は CRLF（windows-agent/.gitattributes 参照）。
Option Explicit
On Error Resume Next
Dim today, sh
today = Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\kiseki\windows-agent"
sh.Run "C:\Python312-32\pythonw.exe fetch_entries_rt.py --date " & today, 0, False
