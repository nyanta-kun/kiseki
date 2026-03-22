@echo off
REM kiseki JV-Link Agent 自動起動スクリプト
REM Windows タスクスケジューラに登録して自動起動させる
REM
REM 登録方法:
REM   1. タスクスケジューラを開く
REM   2. 「基本タスクの作成」を選択
REM   3. トリガー: 「ログオン時」または「スタートアップ時」
REM   4. 操作: 「プログラムの開始」→ このbatファイルを指定
REM   5. 「最上位の特権で実行する」にチェック

cd /d "%~dp0"

REM Python 32bit版のパスを指定（環境に合わせて変更）
set PYTHON32=C:\Python312-32\python.exe

echo [%date% %time%] kiseki JV-Link Agent starting...
%PYTHON32% jvlink_agent.py --mode all

echo [%date% %time%] Agent stopped. Restarting in 10 seconds...
timeout /t 10
goto :eof
