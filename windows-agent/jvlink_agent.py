"""
kiseki Windows Agent - JV-Link SDK データ取得・リアルタイム通知

JRA-VAN Data Lab SDKを直接操作し、以下を行う:
1. 蓄積系データ取得（出馬表・成績・血統・調教）→ Mac側FastAPIへPOST
2. 速報系オッズ取得（全券種）→ Mac側FastAPIへPOST
3. リアルタイム通知（出走取消・騎手変更）→ Mac側FastAPIへPOST

動作環境:
- Windows 10/11 (Parallels上でも可)
- Python 3.x 32bit版 (JV-Linkが32bit COMのため必須)
- pywin32
- JV-Linkインストール済み + 利用キー設定済み

使い方:
  python jvlink_agent.py                   # 全機能起動（蓄積取得+リアルタイム監視）
  python jvlink_agent.py --mode setup      # 初回セットアップ（過去データ一括取得）
  python jvlink_agent.py --mode daily      # 当日データ取得のみ
  python jvlink_agent.py --mode realtime   # リアルタイム監視のみ
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# 環境変数読み込み
from dotenv import load_dotenv

# .envはプロジェクトルートにある想定
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

JRAVAN_SID = os.getenv("JRAVAN_SID", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://host.internal:8000")
API_KEY = os.getenv("CHANGE_NOTIFY_API_KEY", "")

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("jvlink_agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("jvlink_agent")

# JV-Link データ種別ID
# 蓄積系 (JVOpen)
DATASPEC_RACE = "RACE"   # レース情報(RA) + 馬毎レース情報(SE)
DATASPEC_TOKU = "TOKU"   # 特別登録馬
DATASPEC_DIFF = "TOKU"   # 出馬表(特別登録馬) ※"DIFF"は無効なDataSpec
DATASPEC_BLOD = "BLOD"   # 血統
DATASPEC_MING = "MING"   # 馬名意味由来
DATASPEC_SNAP = "SNAP"   # 調教データ
DATASPEC_SLOP = "SLOP"   # 坂路調教
DATASPEC_YSCH = "YSCH"   # 予定スケジュール
DATASPEC_HOSE = "HOSE"   # 馬基本データ
DATASPEC_HOYU = "HOYU"   # 馬主データ
DATASPEC_WOOD = "WOOD"   # ウッドチップ調教

# 速報系 (JVRTOpen)
RT_RACE_INFO = "0B12"    # 出馬表（速報）
RT_ODDS_ALL = "0B31"     # オッズ速報（全賭式）
RT_WEIGHT = "0B14"       # 馬体重
RT_SCRATCH = "0B15"      # 出走取消・競走除外
RT_JOCKEY_CHANGE = "0B20"  # 騎手変更
RT_RESULT = "0B30"       # 成績速報
RT_PAYOUT = "0B32"       # 払戻速報

# レコード種別IDとデータ内容の対応
RECORD_TYPES = {
    "RA": "レース情報",
    "SE": "馬毎レース情報",
    "HR": "払戻情報",
    "O1": "単勝オッズ",
    "O2": "複勝オッズ",
    "O3": "枠連オッズ",
    "O4": "馬連オッズ",
    "O5": "ワイドオッズ",
    "O6": "馬単オッズ",
    "O7": "三連複オッズ",
    "O8": "三連単オッズ",
    "WE": "馬体重",
    "AV": "出走取消",
    "JC": "騎手変更",
    "CC": "コース変更",
    "WH": "天候馬場変更",
}


def init_jvlink():
    """JV-Link COMオブジェクトを初期化する"""
    try:
        import win32com.client
        jv = win32com.client.Dispatch("JVDTLab.JVLink")
        rc = jv.JVInit(JRAVAN_SID)
        if rc != 0:
            logger.error(f"JVInit failed: rc={rc}")
            sys.exit(1)
        logger.info("JV-Link initialized successfully")
        return jv
    except Exception as e:
        logger.error(f"JV-Link initialization error: {e}")
        logger.error("Python 32bit版で実行していますか？ JV-Linkはインストール済みですか？")
        sys.exit(1)


def post_to_backend(endpoint: str, data: dict) -> bool:
    """Mac側FastAPIにデータをPOSTする"""
    try:
        resp = requests.post(
            f"{BACKEND_URL}{endpoint}",
            json=data,
            headers={"X-API-Key": API_KEY},
            timeout=30,
        )
        if resp.status_code == 200:
            return True
        else:
            logger.warning(f"POST {endpoint} failed: {resp.status_code} {resp.text}")
            return False
    except requests.exceptions.ConnectionError:
        logger.error(f"Backend unreachable: {BACKEND_URL}")
        return False
    except Exception as e:
        logger.error(f"POST error: {e}")
        return False


def fetch_stored_data(jv, dataspec: str, from_time: str, option: int = 1):
    """
    蓄積系データを取得する (JVOpen)

    Args:
        jv: JV-Link COMオブジェクト
        dataspec: データ種別ID (例: "RACE", "DIFF")
        from_time: 取得開始日時 "YYYYMMDDhhmmss"
        option: 1=通常, 2=今週, 3=セットアップ
    """
    logger.info(f"JVOpen: dataspec={dataspec}, from={from_time}, option={option}")
    result = jv.JVOpen(dataspec, from_time, option, 0, 0, "")
    rc = result[0] if isinstance(result, tuple) else result

    if rc < 0:
        logger.error(f"JVOpen error: rc={rc}")
        return []

    records = []
    read_count = 0

    while True:
        r = jv.JVRead("", 256000, "")
        ret_code = r[0]

        if ret_code == 0:
            # 全データ読み込み完了
            break
        elif ret_code == -1:
            # ファイル切り替わり（正常）: 次のレコードを継続して読む
            continue
        elif ret_code == -3:
            # ダウンロード中: 少し待機してリトライ
            time.sleep(0.5)
            continue
        elif ret_code < -1:
            logger.error(f"JVRead error: rc={ret_code}")
            break
        else:
            buff = r[1]
            rec_id = buff[:2]
            records.append({"rec_id": rec_id, "data": buff})
            read_count += 1

            if read_count % 1000 == 0:
                logger.info(f"  ... {read_count} records read")

    jv.JVClose()
    logger.info(f"JVOpen complete: {read_count} records from {dataspec}")
    return records


def fetch_realtime_data(jv, dataspec: str, key: str):
    """
    速報系データを取得する (JVRTOpen)

    Args:
        jv: JV-Link COMオブジェクト
        dataspec: データ種別ID (例: "0B31")
        key: レースキー (例: "20260322")
    """
    rc = jv.JVRTOpen(dataspec, key)
    if rc < 0:
        logger.error(f"JVRTOpen error: rc={rc}, dataspec={dataspec}")
        return []

    records = []
    while True:
        r = jv.JVRead("", 256000, "")
        ret_code = r[0]

        if ret_code == 0:
            break
        elif ret_code == -1:
            continue
        elif ret_code < -1:
            logger.error(f"JVRead error: rc={ret_code}")
            break
        else:
            buff = r[1]
            rec_id = buff[:2]
            records.append({"rec_id": rec_id, "data": buff})

    jv.JVClose()
    return records


def _filter_race_records(records: list[dict]) -> list[dict]:
    """RA/SEレコードのみ抽出する。RACE dataspaceにはJG等も混在するため。"""
    return [r for r in records if r.get("rec_id") in ("RA", "SE")]


def _post_in_batches(endpoint: str, records: list[dict], batch_size: int = 200) -> None:
    """レコードをbatch_size件ずつ分割してPOSTする。"""
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        ok = post_to_backend(endpoint, {"records": batch})
        logger.info(f"  POST {endpoint} batch[{i}:{i+batch_size}] -> {'OK' if ok else 'NG'}")


def run_daily_fetch(jv):
    """当日データ取得（毎朝実行）"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d") + "000000"

    # レース情報(RA) + 出馬表(SE) ※出馬表はRACEデータに含まれる
    # RACE dataspaceにはJG等の非対象レコードも混在するためRA/SEのみ送信
    logger.info("=== レース情報・出馬表取得 ===")
    records = fetch_stored_data(jv, DATASPEC_RACE, yesterday, option=2)
    ra_se = _filter_race_records(records)
    logger.info(f"  RA/SE: {len(ra_se)} / 全体: {len(records)}")
    if ra_se:
        _post_in_batches("/api/import/races", ra_se)

    logger.info("Daily fetch complete")


def run_realtime_monitor(jv):
    """リアルタイム監視ループ"""
    logger.info("=== Realtime monitor started ===")
    today = datetime.now().strftime("%Y%m%d")

    while True:
        try:
            # オッズ取得
            odds_records = fetch_realtime_data(jv, RT_ODDS_ALL, today)
            if odds_records:
                post_to_backend("/api/import/odds", {
                    "date": today,
                    "records": odds_records,
                })

            # 出走取消チェック
            scratch_records = fetch_realtime_data(jv, RT_SCRATCH, today)
            for rec in scratch_records:
                logger.warning(f"出走取消検知: {rec['data'][:30]}")
                post_to_backend("/api/changes/notify", {
                    "change_type": "scratch",
                    "raw_data": rec["data"],
                    "detected_at": datetime.now().isoformat(),
                })

            # 騎手変更チェック
            jc_records = fetch_realtime_data(jv, RT_JOCKEY_CHANGE, today)
            for rec in jc_records:
                logger.warning(f"騎手変更検知: {rec['data'][:30]}")
                post_to_backend("/api/changes/notify", {
                    "change_type": "jockey_change",
                    "raw_data": rec["data"],
                    "detected_at": datetime.now().isoformat(),
                })

            # 馬体重
            weight_records = fetch_realtime_data(jv, RT_WEIGHT, today)
            if weight_records:
                post_to_backend("/api/import/weights", {
                    "date": today,
                    "records": weight_records,
                })

            time.sleep(30)  # 30秒間隔

        except KeyboardInterrupt:
            logger.info("Realtime monitor stopped by user")
            break
        except Exception as e:
            logger.error(f"Realtime monitor error: {e}")
            time.sleep(10)


def run_setup(jv):
    """初回セットアップ（過去データ一括取得）"""
    logger.info("=== SETUP MODE: 過去データ一括取得 ===")
    logger.info("※ 大量のデータをダウンロードします。数時間かかる場合があります。")

    # 2年前から
    from_time = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d") + "000000"

    # RACE: RA/SEレコードのみ抽出して /api/import/races へ
    logger.info(f"Fetching RACE from {from_time}...")
    race_records = fetch_stored_data(jv, DATASPEC_RACE, from_time, option=3)
    ra_se = _filter_race_records(race_records)
    logger.info(f"  -> {len(ra_se)} RA/SE records (filtered from {len(race_records)})")
    if ra_se:
        _post_in_batches("/api/import/races", ra_se)

    # HOSE/BLOD: 馬基本データ・血統はバックエンド実装後に追加予定
    # TODO: /api/import/horses, /api/import/bloodlines エンドポイント実装後に送信
    for spec in [DATASPEC_HOSE, DATASPEC_BLOD]:
        logger.info(f"Fetching {spec} from {from_time}... (送信先未実装のためスキップ)")
        records = fetch_stored_data(jv, spec, from_time, option=3)
        logger.info(f"  -> {len(records)} records fetched (skipped)")


def main():
    parser = argparse.ArgumentParser(description="kiseki JV-Link Agent")
    parser.add_argument(
        "--mode",
        choices=["all", "setup", "daily", "realtime"],
        default="all",
        help="動作モード (default: all)",
    )
    args = parser.parse_args()

    if not JRAVAN_SID:
        logger.error("JRAVAN_SID が設定されていません。.env を確認してください。")
        sys.exit(1)

    logger.info(f"kiseki JV-Link Agent starting (mode={args.mode})")
    logger.info(f"Backend URL: {BACKEND_URL}")

    jv = init_jvlink()

    if args.mode == "setup":
        run_setup(jv)
    elif args.mode == "daily":
        run_daily_fetch(jv)
    elif args.mode == "realtime":
        run_realtime_monitor(jv)
    elif args.mode == "all":
        run_daily_fetch(jv)
        run_realtime_monitor(jv)


if __name__ == "__main__":
    main()
