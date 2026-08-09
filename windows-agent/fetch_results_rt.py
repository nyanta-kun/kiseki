"""速報系 0B12（速報成績）から指定日の RA/SE/HR を取り出して DB へ反映する。

蓄積系（JVOpen）が固着して確定成績が取り込めないときの緊急経路。
JVRTOpen は JVOpen とは別チャネルなので、JVOpen が返らない状況でも動く。

    python fetch_results_rt.py [--date YYYYMMDD] [--probe]

realtime ループも 0B12 をポーリングしているが、あちらは「発走済み・未確定」の
レースだけを対象にし、取得済みキーを `seen_results.json` で除外する。過去日の
取りこぼしを埋めるにはこのスクリプトで全レースキーを走査しなおす。

--probe を付けるとレースキーごとの rec_id 内訳を出すだけで DB へは送らない。
「なぜ 3 頭分しか入らなかったのか」のような取りこぼしの切り分けに使う。
"""

import argparse
from datetime import datetime

from jvlink_agent import (
    API_KEY,
    BACKEND_URL,
    _fetch_today_races,
    _post_hr_payouts,
    fetch_realtime_data,
    init_jvlink,
    logger,
    post_to_backend,
)

RT_RESULT = "0B12"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y%m%d"),
        help="取得対象日 YYYYMMDD（既定: 今日）",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="レースキーごとの rec_id 内訳を出すだけで DB へ送らない",
    )
    args = parser.parse_args()

    logger.info(f"=== RT-RESULTS: 0B12 から成績を取得 (date={args.date}) ===")

    races = _fetch_today_races(args.date)
    race_keys = [r["jravan_race_id"] for r in races if r.get("jravan_race_id")]
    if not race_keys:
        logger.warning(f"対象レースがありません: date={args.date}")
        return
    logger.info(f"対象レース: {len(race_keys)} 件")

    jv = init_jvlink()
    ra_se: list[dict] = []
    hr: list[dict] = []
    try:
        for race_key in race_keys:
            # 0B12 のキーは YYYYMMDDJJRR（12文字: 日付8+場所2+レース番号2）
            result_key = race_key[:10] + race_key[14:]
            records = fetch_realtime_data(jv, RT_RESULT, result_key)
            counts: dict[str, int] = {}
            for rec in records:
                counts[rec["rec_id"]] = counts.get(rec["rec_id"], 0) + 1
            logger.info(f"  {result_key}: {counts}")
            if args.probe:
                continue
            for rec in records:
                if rec["rec_id"] in ("RA", "SE"):
                    ra_se.append(rec)
                elif rec["rec_id"] == "HR":
                    hr.append(rec)
    finally:
        try:
            jv.JVClose()
        except Exception:  # noqa: BLE001 - 既に閉じている場合がある
            pass

    if args.probe:
        logger.info("=== RT-RESULTS (probe) 完了 ===")
        return

    batch_size = 50
    logger.info(f"RA/SE {len(ra_se)} 件 → /api/import/races へ送信します。")
    for i in range(0, len(ra_se), batch_size):
        batch = ra_se[i:i + batch_size]
        ok = post_to_backend(
            "/api/import/races", {"records": batch}, BACKEND_URL, API_KEY, timeout=300
        )
        state = "OK" if ok else "NG"
        logger.info(f"  POST /api/import/races batch[{i}:{i + len(batch)}] -> {state}")

    if hr:
        logger.info(f"HR {len(hr)} 件 → /api/import/payouts へ送信します。")
        _post_hr_payouts(hr)

    logger.info("=== RT-RESULTS 完了 ===")


if __name__ == "__main__":
    main()
