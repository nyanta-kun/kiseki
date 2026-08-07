"""速報系 0B15（速報レース情報）から当日の RA/SE を取り出して DB へ反映する。

蓄積系（JVOpen）が固着して出馬表が取り込めないときの緊急経路。
JVRTOpen は JVOpen とは別チャネルなので、JVOpen が返らない状況でも動く。

    python fetch_entries_rt.py [--date YYYYMMDD]

0B15 は「速報レース情報」で RA / SE / AV / JC 等が混在する。realtime ループは
このうち rec_id を問わず件数だけを「出走取消」としてログに出していたが、
実体には出馬表の SE が含まれる。ここでは RA/SE のみを抜いて
/api/import/races へ送る（蓄積系 RACE と同じ取り込み口）。
"""

import argparse
from datetime import datetime

from jvlink_agent import (
    API_KEY,
    BACKEND_URL,
    PENDING_DIR,
    _post_in_batches,
    fetch_realtime_data,
    init_jvlink,
    logger,
)

RT_RACE_INFO = "0B15"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y%m%d"),
        help="取得対象日 YYYYMMDD（既定: 今日）",
    )
    args = parser.parse_args()

    logger.info(f"=== RT-ENTRIES: 0B15 から出馬表を取得 (date={args.date}) ===")

    jv = init_jvlink()
    try:
        records = fetch_realtime_data(jv, RT_RACE_INFO, args.date)
    finally:
        try:
            jv.JVClose()
        except Exception:  # noqa: BLE001 - 既に閉じている場合がある
            pass

    counts: dict[str, int] = {}
    for rec in records:
        counts[rec["rec_id"]] = counts.get(rec["rec_id"], 0) + 1
    logger.info(f"0B15 レコード内訳: {counts}")

    ra_se = [r for r in records if r.get("rec_id") in ("RA", "SE")]
    if not ra_se:
        logger.warning("RA/SE レコードがありません。取り込むものがないため終了します。")
        return

    logger.info(f"RA/SE {len(ra_se)} 件 → /api/import/races へ送信します。")
    _post_in_batches("/api/import/races", ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
    logger.info("=== RT-ENTRIES 完了 ===")


if __name__ == "__main__":
    main()
