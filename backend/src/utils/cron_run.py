"""cron ジョブの実行記録（`keiba.cron_runs`）。

## なぜ要るか（2026-09-08）

統合 Phase 2 で sekito のスクレイプジョブをすべて kiseki の host cron へ移した
結果、`check_scrape_supply.py` の②「当日のスクレイプジョブに failed なし」が
**完全に空になった**（`sekito.script_requests` に書くのは sekito のスケジューラだけ）。
②は「netkeiba-index が毎日 10 分でタイムアウト kill されている」ことを唯一
検知していたチェックだったので、同じ事実を kiseki 側で持ち直す。

## 使い方

    from src.utils.cron_run import record

    with record("scrape_netkeiba_index") as run:
        ...
        run.summary = "成功 36 / スキップ 0"
        run.exit_code = 0

`with` を抜けるときに `finished_at` と `exit_code` を書く。例外で抜けたときは
`exit_code` を 1（未設定なら）にして再送出する。

🔴 **記録の失敗でジョブを落とさない。** 監視のための記録が本体を壊したら本末転倒
なので、書き込みに失敗しても WARNING を出して続ける。

## 「走ったか」と「どう終わったか」を分ける

開始時に 1 行入れて、終了時に更新する。**開始行が残ったまま終了が無ければ
「途中で死んだ」**と分かる。終了時に 1 行だけ書く形だと、この状態が
「そもそも実行されなかった」と区別できない。今回の 10 分 kill はまさに前者。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import text

from ..db.session import SyncSessionLocal

logger = logging.getLogger(__name__)


@dataclass
class RunRecord:
    """実行中の 1 件。`summary` と `exit_code` は呼び出し側が埋める。"""

    job_name: str
    row_id: int | None = None
    summary: str | None = None
    exit_code: int | None = None


_INSERT = text(
    "INSERT INTO keiba.cron_runs (job_name, started_at) "
    "VALUES (:job, now()) RETURNING id"
)
_FINISH = text(
    "UPDATE keiba.cron_runs "
    "   SET finished_at = now(), exit_code = :rc, summary = :summary "
    " WHERE id = :id"
)


@contextmanager
def record(job_name: str):
    """実行を記録する。記録に失敗してもジョブは続行する。"""
    run = RunRecord(job_name=job_name)
    try:
        with SyncSessionLocal() as session:
            run.row_id = session.execute(_INSERT, {"job": job_name}).scalar()
            session.commit()
    except Exception as e:
        logger.warning("cron_runs への開始記録に失敗しました（続行します）: %s", e)

    try:
        yield run
    except BaseException:
        if run.exit_code is None:
            run.exit_code = 1
        _finish(run)
        raise
    else:
        if run.exit_code is None:
            run.exit_code = 0
        _finish(run)


def _finish(run: RunRecord) -> None:
    if run.row_id is None:
        return
    try:
        with SyncSessionLocal() as session:
            session.execute(_FINISH, {
                "id": run.row_id, "rc": run.exit_code, "summary": run.summary,
            })
            session.commit()
    except Exception as e:
        logger.warning("cron_runs への終了記録に失敗しました: %s", e)
