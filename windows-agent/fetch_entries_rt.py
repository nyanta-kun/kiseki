"""速報系 0B15（速報レース情報）から当日の RA/SE を取り出して DB へ反映する。

蓄積系（JVOpen）が固着して出馬表が取り込めないときの緊急経路。
JVRTOpen は JVOpen とは別チャネルなので、JVOpen が返らない状況でも動く。

    python fetch_entries_rt.py [--date YYYYMMDD]

0B15 は「速報レース情報」で RA / SE / AV / JC 等が混在する。realtime ループは
このうち rec_id を問わず件数だけを「出走取消」としてログに出していたが、
実体には出馬表の SE が含まれる。ここでは RA/SE のみを抜いて
/api/import/races へ送る（蓄積系 RACE と同じ取り込み口）。

🔴 **0 件は「取り込むものが無い」ではなく「まだ出ていない」ことが多い。**
2026-08-16 は 07:21 の 1 回きりの取得が 0 件で、そのまま成功（exit 0）扱いで
終わった。枠順が入らないと **DM 取り込みが馬番でマッチできず捨てられ**、
07:30 の指数算出が DM 無しで走ってレース内ばらつきが半減した
（memory: jra_entries_dm_cascade_2026_08_16）。同日 09:28 に手で叩いたら
RA:36/SE:490 が普通に取れた＝単にデータ提供開始前だっただけ。
そこで **取れるまで待ち、待ちきれなければ exit 1 で落とす**（タスクの
LastTaskResult に出す）。取得は冪等なので待ち直しても害はない。
"""

import argparse
import sys
import time
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

# 既定の粘り方: 5 分おきに最大 2 時間。07:00 起動なら 09:00 までに諦める計算で、
# 最も早い発走（およそ 09:40）には間に合う。
DEFAULT_RETRY_INTERVAL_SEC = 300
DEFAULT_MAX_WAIT_SEC = 7200


def _fetch_until_available(jv, args) -> list[dict]:
    """RA/SE が取れるまで再試行して返す。上限まで取れなければ空リスト。

    JV-Link 側にまだデータが出ていないだけのことが多いので、
    同じ呼び出しをそのまま繰り返す（冪等）。
    """
    deadline = time.monotonic() + max(args.max_wait, 0)
    attempt = 0
    while True:
        attempt += 1
        records = fetch_realtime_data(jv, RT_RACE_INFO, args.date)
        counts: dict[str, int] = {}
        for rec in records:
            counts[rec["rec_id"]] = counts.get(rec["rec_id"], 0) + 1
        logger.info(f"0B15 レコード内訳 (試行{attempt}): {counts}")

        ra_se = [r for r in records if r.get("rec_id") in ("RA", "SE")]
        if ra_se:
            return ra_se

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return []
        wait = min(args.retry_interval, remaining)
        logger.warning(
            f"RA/SE が 0 件でした。{int(wait)} 秒待って再試行します"
            f"（残り約 {int(remaining / 60)} 分）"
        )
        time.sleep(wait)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y%m%d"),
        help="取得対象日 YYYYMMDD（既定: 今日）",
    )
    parser.add_argument(
        "--retry-interval", type=int, default=DEFAULT_RETRY_INTERVAL_SEC,
        help=f"0 件だったときの再試行間隔（秒・既定 {DEFAULT_RETRY_INTERVAL_SEC}）",
    )
    parser.add_argument(
        "--max-wait", type=int, default=DEFAULT_MAX_WAIT_SEC,
        help=f"取れるまで粘る上限（秒・既定 {DEFAULT_MAX_WAIT_SEC}）。0 で再試行しない",
    )
    args = parser.parse_args()

    logger.info(f"=== RT-ENTRIES: 0B15 から出馬表を取得 (date={args.date}) ===")

    jv = init_jvlink()
    try:
        ra_se = _fetch_until_available(jv, args)
    finally:
        try:
            jv.JVClose()
        except Exception:  # noqa: BLE001 - 既に閉じている場合がある
            pass

    if not ra_se:
        # 「無い」ではなく「取れなかった」。成功で終えると誰も気付けない（上記 docstring）
        logger.error(
            f"RA/SE レコードを {args.max_wait} 秒待っても取得できませんでした。"
            "枠順が入らないと DM 取り込みと指数算出が壊れます。exit 1 で終了します。"
        )
        sys.exit(1)

    logger.info(f"RA/SE {len(ra_se)} 件 → /api/import/races へ送信します。")
    _post_in_batches("/api/import/races", ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
    logger.info("=== RT-ENTRIES 完了 ===")


if __name__ == "__main__":
    main()
