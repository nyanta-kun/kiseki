"""POG のマテビューを LISTEN/NOTIFY で更新する常駐（sekito `mv-refresh-listener.js` の移設先）。

統合 Phase 5。`keiba.race_results` / `chihou.race_results` の AFTER INSERT/UPDATE/DELETE
トリガが `pg_notify('mv_horse_runs_dirty', '')` を撃つので、それを受けて
`keiba.mv_horse_runs` / `keiba.mv_graded_wins` を `REFRESH ... CONCURRENTLY` する。

## 🔴 これが動かないと POG が静かに凍結する

マテビューは更新が止まっても**最後の内容を返し続ける**。例外もログも空表示も
出ないので、順位表が古いことに誰も気づけない。移設元では sekito の
バックエンドがこれを担っており、**sekito を止めた瞬間に kiseki の POG が
凍る**状態だった。

## 🔴 デバウンスは REFRESH 1 回の所要時間より必ず長く取る

`REFRESH ... CONCURRENTLY` は変更行数に関わらず**定義全体を毎回再計算する**ので、
1 レース分の差分でも実測 25〜35 秒（平均 28.5 秒）かかる。移設元は当初 30 秒に
していて、

    デバウンス満了 → REFRESH 28秒 → その間に届いた通知で即再スケジュール

が途切れず連鎖し、実質ずっと回り続けていた。実測（2026-09-05 の 24 時間）:
**260 回 / 合計 123 分 = 1 日の 8.57%** を REFRESH が占有（結果が流れ込む
繁忙帯は 16%）。通知元は毎分の cron が更新する `race_results` のトリガ。

300 秒にすると最大でも 288 回/日 が上限になり、鮮度は最大 5 分遅れる。
POG は前日までの成績集計なのでこの遅延は許容できる。**縮めないこと。**

## 冗長性

LISTEN が落ちても気づけないので、`REFRESH_INTERVAL_SEC` ごとに通知の有無に
関わらず 1 回走らせる保険を入れてある（移設元は「cron 併用推奨」と書きつつ
実際には無かった）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import asyncpg

from ..config import settings

logger = logging.getLogger(__name__)

# 🔴 **この常駐のログだけは本番で見えるようにする。**
#    kiseki のバックエンドは `logging.basicConfig` も `dictConfig` も呼んでおらず、
#    uvicorn が握るのは `uvicorn.*` のロガーだけ。そのため `src.*` の
#    `logger.info(...)` は**本番で 1 行も出ていない**（2026-09-10 実測。
#    WARNING 以上は Python の `lastResort` が stderr へ出すので失敗は見える）。
#    ここは「動いていること」を確認できないと止まっても気づけない性質の処理
#    なので、このロガーにだけ stderr ハンドラを付ける。
#    ⚠️ 全体を INFO にはしない。毎分の cron が叩く API のログまで増えて、
#       ログローテーションの容量計算が変わる。
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # uvicorn 側へ二重に流さない

CHANNEL = "mv_horse_runs_dirty"

#: 通知をまとめる時間。🔴 REFRESH 1 回（実測 25〜35 秒）より必ず長く。
DEBOUNCE_SEC = 300.0

#: LISTEN 接続が切れたときの再接続間隔。
RECONNECT_DELAY_SEC = 5.0

#: 通知が来なくても走らせる保険の間隔（LISTEN が黙って死んでも止まらないように）。
REFRESH_INTERVAL_SEC = 3600.0

#: REFRESH するマテビュー。`race_results` の取込で両方とも陳腐化する。
TARGETS = ("keiba.mv_horse_runs", "keiba.mv_graded_wins")


def _dsn() -> str:
    """asyncpg へ直接渡す DSN。

    `settings.database_url` は SQLAlchemy 用（`postgresql+asyncpg://` と
    ドライバ固有のクエリ）なので、そのままでは asyncpg に渡せない。
    """
    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )
    if settings.api_env == "production":
        dsn += "?sslmode=require"
    return dsn


class MvRefresher:
    """通知を受けてマテビューを更新する常駐。

    FastAPI の lifespan から `start()` / `stop()` を呼ぶ。
    """

    def __init__(self) -> None:
        self._dirty = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 起動/停止

    async def start(self) -> None:
        """LISTEN と REFRESH のループを開始する。"""
        self._listen_task = asyncio.create_task(self._listen_forever(), name="mv-listen")
        self._task = asyncio.create_task(self._refresh_loop(), name="mv-refresh")
        logger.info("[mv-refresh] 起動しました（デバウンス %.0f秒）", DEBOUNCE_SEC)

    async def stop(self) -> None:
        """ループを止める。進行中の REFRESH は待たない（DB 側で完結する）。"""
        for task in (self._listen_task, self._task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        logger.info("[mv-refresh] 停止しました")

    # ------------------------------------------------------------------ LISTEN

    async def _listen_forever(self) -> None:
        """LISTEN 専用の接続を張り続ける。

        ⚠️ プールから借りてはいけない。idle で回収されると LISTEN が黙って
        外れ、**通知が来ないだけで例外は出ない**。
        """
        while True:
            conn = None
            try:
                conn = await asyncpg.connect(_dsn())
                await conn.add_listener(CHANNEL, self._on_notify)
                logger.info("[mv-refresh] LISTEN 開始: %s", CHANNEL)
                # 接続が生きている限りここで待つ。切れたら例外か is_closed で抜ける。
                while not conn.is_closed():
                    await asyncio.sleep(RECONNECT_DELAY_SEC)
                logger.warning("[mv-refresh] LISTEN 接続が切れました。張り直します")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 常駐なので何が来ても続ける
                logger.error("[mv-refresh] LISTEN 失敗: %s", exc)
            finally:
                if conn is not None:
                    with contextlib.suppress(Exception):
                        await conn.close()
            await asyncio.sleep(RECONNECT_DELAY_SEC)

    def _on_notify(self, *_args: object) -> None:
        """通知を受けたら「汚れている」印を立てるだけ。

        ここで REFRESH を始めない。1 レース 18 頭の連続 INSERT で 18 回
        呼ばれるので、まとめるのはループ側の仕事。
        """
        self._dirty.set()

    # ------------------------------------------------------------------ REFRESH

    async def _refresh_loop(self) -> None:
        """汚れていれば REFRESH する。通知が無くても保険の間隔で 1 回走らせる。"""
        while True:
            try:
                # 通知待ち。来なければ保険の間隔で起きて 1 回走らせる。
                try:
                    await asyncio.wait_for(self._dirty.wait(), timeout=REFRESH_INTERVAL_SEC)
                    # 🔴 最初の通知から一定時間ためてから走る。ここを縮めると
                    #    REFRESH が終わる前に次が積まれ、回り続ける。
                    await asyncio.sleep(DEBOUNCE_SEC)
                except TimeoutError:
                    pass
                # 走る直前に降ろす。REFRESH 中に来た通知は次の周回で拾う。
                self._dirty.clear()
                await self._refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 常駐なので何が来ても続ける
                logger.error("[mv-refresh] ループが落ちました: %s", exc)
                await asyncio.sleep(RECONNECT_DELAY_SEC)

    async def _refresh_once(self) -> None:
        """マテビューを順に更新する。

        ⚠️ `CONCURRENTLY` はトランザクションの中で実行できない。
        asyncpg の `execute()` は暗黙のトランザクションを張らないのでそのまま通る。
        """
        conn = None
        started = time.monotonic()
        try:
            conn = await asyncpg.connect(_dsn())
            for target in TARGETS:
                await conn.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {target}")
            logger.info(
                "[mv-refresh] 完了 %.1f秒 (%s)",
                time.monotonic() - started,
                ", ".join(TARGETS),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[mv-refresh] REFRESH 失敗: %s", exc)
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()


refresher = MvRefresher()
