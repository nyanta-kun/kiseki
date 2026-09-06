"""netkeiba へのアクセス間隔を抑えるレートリミッタ。

sekito `lib/sekito/scraping/rate_limiter.py` からの移設。**設定値は 1:1 で
持ってきている**。ここを緩めると IP 制限を食らい、締めるとジョブが時間内に
終わらない（[[netkeiba-scraper-facts]]: 実測 8.7 秒/リクエストで、その
ほとんどがこの待ち。速くする唯一の方法は取得項目を減らすこと）。

## 移設で落としたもの: AdaptiveRateLimiter

移設元は `AdaptiveRateLimiter`（エラーで間隔を倍にし、成功で戻す）を使っていたが、
**`report_error()` / `report_success()` はスクレイパから一度も呼ばれていない**
（2026-09-06 に全呼び出しを確認。同名メソッドの呼び元は `proxy_manager` 側だけ）。
適応部分は死んでおり、実際の挙動は素の `RateLimiter` と同一。動いていない機構を
一緒に運ぶと「効いているつもり」になるので落とした。

必要になったら、**呼び元と一緒に**入れ直すこと。

## 時間帯別にする理由

netkeiba は日中（レース開催中）が最も混む。同じ間隔で叩くと開催中だけ弾かれる。
移設元の区分と値をそのまま使う。
"""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class RateConfig:
    """1 時間帯ぶんのレート制限設定。

    Attributes:
        min_interval: 直前のリクエストからの最小間隔（秒）。
        max_requests: `time_window` 秒あたりの最大リクエスト数。
        time_window: リクエスト数を数える窓（秒）。
        random_delay_min: 毎回加えるランダム遅延の下限（秒）。
        random_delay_max: 同上限。
    """

    min_interval: float
    max_requests: int
    time_window: int
    random_delay_min: float
    random_delay_max: float


# 時間帯 → 設定。値は移設元 `TimeBasedRateConfig.HOUR_CONFIGS` と同一。
# キーは「その設定が適用される時間の range」。
_HOUR_CONFIGS: tuple[tuple[str, range, RateConfig], ...] = (
    # 深夜（0-6時）— アクセスが少ないのでやや速め
    ("night", range(0, 6), RateConfig(3.0, 15, 60, 2.0, 5.0)),
    # 早朝（6-9時）— データ収集の主要時間帯
    ("early_morning", range(6, 9), RateConfig(4.0, 12, 60, 2.0, 5.0)),
    # 日中（9-15時）— レース開催中でサイト負荷が高い。いちばん保守的
    ("daytime", range(9, 15), RateConfig(5.0, 10, 60, 3.0, 6.0)),
    # 午後（15-21時）— レース終了後の結果収集
    ("afternoon", range(15, 21), RateConfig(4.0, 12, 60, 2.0, 5.0)),
    # 夜（21-24時）— 補完
    ("late_night", range(21, 24), RateConfig(3.0, 15, 60, 2.0, 5.0)),
)

# 時間帯の range は 0-23 を隙間なく覆っているので、通常この既定値は使われない。
# 範囲外の hour を渡されたときのための保険で、いちばん保守的な日中の設定にする。
_DEFAULT_CONFIG = RateConfig(5.0, 10, 60, 3.0, 6.0)


def config_for_hour(hour: int | None = None) -> RateConfig:
    """その時刻に適用される設定を返す。

    🔴 **時刻は JST で決める。** kiseki の backend コンテナは UTC で動いており
    （2026-09-06 実測）、`datetime.now().hour` を使うと 9 時間ずれた時間帯設定が
    選ばれる。たとえば JST 10 時（日中・いちばん保守的にすべき時間）に
    UTC 1 時＝「深夜」の設定が当たってしまい、**開催中に最も速く叩く**という
    最悪の取り違えになる。移設元 sekito のコンテナは JST だったので素の
    `datetime.now()` で正しかった。

    Args:
        hour: 0-23。省略時は現在の JST 時刻。
    """
    if hour is None:
        hour = datetime.now(JST).hour
    for name, hours, config in _HOUR_CONFIGS:
        if hour in hours:
            logger.debug("レート制限の時間帯: %s (%d時 JST)", name, hour)
            return config
    return _DEFAULT_CONFIG


class RateLimiter:
    """リクエスト前に `wait()` を呼んで間隔を空ける。

    3 つを順に効かせる（移設元と同じ）:
      1. 窓内のリクエスト数が上限に達していたら、最古のリクエストが窓から
         出るまで待つ
      2. 直前のリクエストから `min_interval` 秒空ける
      3. 毎回ランダム遅延を足す（等間隔アクセスは機械的で目立つため）

    移設元は `time.time()` で経過を測っていたが、ここは `time.monotonic()` を使う。
    NTP の時刻補正やサマータイムで巻き戻っても待ち時間が壊れない。挙動は同じ。
    """

    def __init__(self, config: RateConfig | None = None) -> None:
        self.config = config or config_for_hour()
        self._last_request_at: float | None = None
        self._history: deque[float] = deque()
        self._lock = Lock()
        logger.info(
            "レートリミッタ初期化: min_interval=%.1fs, max=%d/%ds, jitter=%.1f-%.1fs",
            self.config.min_interval, self.config.max_requests,
            self.config.time_window, self.config.random_delay_min,
            self.config.random_delay_max,
        )

    def wait(self) -> None:
        """次のリクエストを出してよくなるまでブロックする。"""
        with self._lock:
            now = time.monotonic()
            self._drop_old(now)

            if len(self._history) >= self.config.max_requests:
                wait_until = self._history[0] + self.config.time_window
                remaining = wait_until - now
                if remaining > 0:
                    logger.warning(
                        "レート制限: %d秒間に%d件に達しました。%.1f秒待機します",
                        self.config.time_window, self.config.max_requests, remaining,
                    )
                    time.sleep(remaining)
                    now = time.monotonic()
                    self._drop_old(now)

            if self._last_request_at is not None:
                gap = self.config.min_interval - (now - self._last_request_at)
                if gap > 0:
                    time.sleep(gap)

            time.sleep(random.uniform(self.config.random_delay_min,
                                      self.config.random_delay_max))

            now = time.monotonic()
            self._history.append(now)
            self._last_request_at = now

    def _drop_old(self, now: float) -> None:
        """窓から出たリクエスト履歴を捨てる。"""
        cutoff = now - self.config.time_window
        while self._history and self._history[0] < cutoff:
            self._history.popleft()

    @property
    def request_count(self) -> int:
        """いま窓の中にあるリクエスト数（ログ・テスト用）。"""
        self._drop_old(time.monotonic())
        return len(self._history)
