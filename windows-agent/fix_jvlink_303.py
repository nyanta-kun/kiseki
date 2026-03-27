"""JV-Link rc=-303 エラー修復スクリプト

rc=-303 (ファイル存在確認エラー) は JVNextCore の内部状態が壊れた時に発生する。
JVNextCore プロセスを強制終了して COM サーバーを再起動することで修復する。

実行方法 (Windows 管理者権限推奨):
  python fix_jvlink_303.py

修復手順:
  1. JVNextCore プロセスを強制終了
  2. 10秒待機（COM サーバー自動再起動を待つ）
  3. JVInit + JVOpen(option=1) でテスト
  4. 成功すれば jvlink_agent.py --mode setup を案内
"""

import subprocess
import time
import sys
import os
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"

# .env から JRAVAN_SID 読み込み
sid = ""
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("JRAVAN_SID="):
            sid = line.split("=", 1)[1].strip()
            break

if not sid:
    print("[ERROR] JRAVAN_SID が .env に見つかりません")
    sys.exit(1)

print(f"[INFO] JRAVAN_SID={sid[:4]}****")


def kill_jvnextcore():
    """JVNextCore プロセスを全て強制終了する。"""
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq JVNextCore.exe"],
        capture_output=True, text=True
    )
    if "JVNextCore.exe" not in result.stdout:
        print("[INFO] JVNextCore プロセスは見つかりませんでした（既に停止済み）")
        return 0

    # PIDを抽出して終了
    killed = 0
    for line in result.stdout.splitlines():
        if "JVNextCore.exe" in line:
            parts = line.split()
            pid = parts[1] if len(parts) > 1 else None
            if pid and pid.isdigit():
                print(f"[INFO] JVNextCore (PID {pid}) を終了します...")
                kill_result = subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, text=True
                )
                print(f"       → {kill_result.stdout.strip() or kill_result.stderr.strip()}")
                killed += 1
    return killed


def test_jvlink(wait_sec=10):
    """JVLink の初期化とテストを行う。"""
    import win32com.client

    print(f"\n[INFO] {wait_sec}秒待機（COM サーバー再起動待ち）...")
    time.sleep(wait_sec)

    print("[INFO] JVLink COM オブジェクト作成...")
    jv = win32com.client.Dispatch("JVDTLab.JVLink")

    rc_init = jv.JVInit(sid)
    print(f"[INFO] JVInit: rc={rc_init}")
    if rc_init != 0:
        print(f"[ERROR] JVInit 失敗 (rc={rc_init})")
        return False

    # JVOpen(option=1) で軽いテスト（大量ダウンロードなし）
    from datetime import datetime, timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d") + "000000"
    print(f"[INFO] JVOpen テスト: option=1, from={yesterday}")

    result = jv.JVOpen("RACE", yesterday, 1, 0, 0, "")
    if isinstance(result, tuple):
        rc = result[0]
    else:
        rc = result

    print(f"[INFO] JVOpen(option=1): rc={rc}")

    if rc >= 0:
        print("[OK] JVOpen 成功！JVClose で接続を解放します...")
        jv.JVClose()
        return True
    elif rc == -1:
        print("[WARN] rc=-1: 前回の接続が残存しています。JVClose を実行します...")
        jv.JVClose()
        # もう一度試みる
        result2 = jv.JVOpen("RACE", yesterday, 1, 0, 0, "")
        rc2 = result2[0] if isinstance(result2, tuple) else result2
        print(f"[INFO] 再試行 JVOpen(option=1): rc={rc2}")
        if rc2 >= 0:
            jv.JVClose()
            return True
        else:
            print(f"[ERROR] 再試行も失敗: rc={rc2}")
            return False
    elif rc == -303:
        print("[ERROR] rc=-303 が継続しています")
        print("        → 次の手順を試してください:")
        print("          1. Windows を再起動する")
        print("          2. JV-Link をアンインストール・再インストールする")
        print("          3. C:\\ProgramData\\JRA-VAN\\Data Lab\\ フォルダを確認する")
        return False
    else:
        print(f"[ERROR] JVOpen 失敗: rc={rc}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("  JV-Link rc=-303 修復スクリプト")
    print("=" * 60)

    # Step 1: JVNextCore 終了
    killed = kill_jvnextcore()

    # Step 2: テスト
    try:
        import win32com.client
        success = test_jvlink(wait_sec=10 if killed > 0 else 3)
    except ImportError:
        print("[ERROR] win32com が利用できません。Windows 環境で実行してください。")
        sys.exit(1)

    print("\n" + "=" * 60)
    if success:
        print("  [SUCCESS] JV-Link が正常に動作しています！")
        print()
        print("  次のコマンドでデータ取得を再開してください:")
        print("  python jvlink_agent.py --mode setup")
    else:
        print("  [FAILED] 修復に失敗しました")
        print()
        print("  代替手順:")
        print("  1. Windows を再起動する（最も確実）")
        print("  2. 再起動後: python jvlink_agent.py --mode setup")
    print("=" * 60)
