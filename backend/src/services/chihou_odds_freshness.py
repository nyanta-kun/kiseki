"""地方競馬のオッズ更新が止まっていないかを判定する。

画面（レース詳細）に常時出す信号の唯一の正本。DB にも FastAPI にも依存しない
純関数なので、API からもバッチからも同じ判定を呼べる。

🔴 **止まっていることは、値を見ても分からない。**
2026-08-20 に UmaConn realtime が終了処理の途中で固まり、オッズが 14:55 から
19:48 まで **4時間51分** 止まった。DB には最後のスナップショットが残っているので
API は 200 を返し、画面には「それらしい倍率」が出続ける。発走直前になっても朝の
値のままだったが、**画面のどこにも異常が出ていなかった**（気づいたのは人間が
公式オッズと見比べたとき）。したがって鮮度は値と一緒に必ず持ち回ること。

判定の考え方:

- 発走時刻を過ぎたレースの更新停止は**正常**（`closed`）。ここを異常にすると
  終わったレースが全部赤くなり、信号として一切役に立たなくなる
- 1件も無いのも異常ではない（`missing`）。翌日以降のレースは取得前で当然0件
- 異常と言えるのは「**まだ発走していないのに更新が来ていない**」場合だけ
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# 取得ループは全48レースを約1分で1周する（2026-08-20 実測: 1分間隔で48レース）。
# 5分（=約5周ぶん）落ちて初めて「遅延」とする。1周落とした程度で黄色くしない。
LIVE_MAX_SECONDS = 300
# ここを超えたら赤。Windows 側の外部ウォッチドッグが「ストール」と判定するのも
# 15分（`run_realtime_watchdog.vbs` の STALL_MINUTES）。同じ物差しに揃えてある。
STALE_MIN_SECONDS = 900

STATUS_LIVE = "live"
STATUS_DELAYED = "delayed"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_CLOSED = "closed"


@dataclass(frozen=True)
class OddsFreshness:
    """オッズ鮮度の判定結果。

    Attributes:
        status: live / delayed / stale / missing / closed。
        age_seconds: 最終取得からの経過秒。取得実績が無ければ None。
        last_fetched_at: 最終取得時刻（**naive UTC**）。取得実績が無ければ None。
    """

    status: str
    age_seconds: int | None
    last_fetched_at: datetime | None

    def to_dict(self) -> dict:
        """API レスポンス用の dict へ変換する。"""
        return {
            "status": self.status,
            "age_seconds": self.age_seconds,
            "last_fetched_at": (
                self.last_fetched_at.isoformat() + "Z"
                if self.last_fetched_at is not None
                else None
            ),
        }


def classify_odds_freshness(
    *,
    last_fetched_at: datetime | None,
    now_utc: datetime,
    post_at_utc: datetime | None,
    grace: timedelta = timedelta(0),
) -> OddsFreshness:
    """オッズの鮮度を判定する。

    Args:
        last_fetched_at: `chihou.odds_history.fetched_at` の最大値（**naive UTC**）。
            ⚠️ この列は API コンテナの `datetime.now()`（＝UTC）で書かれている一方、
            DB セッションの TimeZone は Asia/Tokyo。`now()` と直接引き算すると
            9時間ずれる。呼び出し側で必ず UTC に揃えてから渡すこと。
        now_utc: 現在時刻（**naive UTC**）。
        post_at_utc: 発走時刻（**naive UTC**）。`post_time` が不正なら None。
        grace: 発走時刻の猶予。発走が遅れても更新は続くため、必要なら後ろへ延ばす。

    Returns:
        OddsFreshness。
    """
    if post_at_utc is not None and now_utc >= post_at_utc + grace:
        # 発走済み。オッズが動かないのは当たり前なので、鮮度は問わない。
        age = (
            int((now_utc - last_fetched_at).total_seconds())
            if last_fetched_at is not None
            else None
        )
        return OddsFreshness(STATUS_CLOSED, age, last_fetched_at)

    if last_fetched_at is None:
        return OddsFreshness(STATUS_MISSING, None, None)

    age_seconds = int((now_utc - last_fetched_at).total_seconds())
    # 未来の時刻が入っていても負の経過にはしない（時計ずれ・投入ミスの保険）。
    age_seconds = max(0, age_seconds)

    if age_seconds <= LIVE_MAX_SECONDS:
        status = STATUS_LIVE
    elif age_seconds < STALE_MIN_SECONDS:
        status = STATUS_DELAYED
    else:
        status = STATUS_STALE
    return OddsFreshness(status, age_seconds, last_fetched_at)


def post_time_to_utc(date: str | None, post_time: str | None) -> datetime | None:
    """`races.date` (YYYYMMDD) と `races.post_time` (HHMM・JST) を naive UTC にする。

    どちらかが欠けている・桁が合わない場合は None（＝発走時刻不明として扱う）。
    """
    if not date or not post_time:
        return None
    if len(date) != 8 or not date.isdigit():
        return None
    if len(post_time) != 4 or not post_time.isdigit():
        return None
    try:
        jst = datetime.strptime(date + post_time, "%Y%m%d%H%M")
    except ValueError:
        return None
    return jst - timedelta(hours=9)
