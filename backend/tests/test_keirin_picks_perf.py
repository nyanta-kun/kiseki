"""`/keirin/picks` と `/keirin/summary` の **N+1 を構造で禁じる**（2026-08-29）。

## 背景（実測）

トップページ `/keirin` は picks / summary / approval-mode / proposals-count を
並列に投げる。実測（本番 api.galloplab.com・2026-08-29）:

| 呼び出し | 温 | 冷 |
|---|---|---|
| `/keirin/summary` | 0.48〜0.79秒 | **1.4 / 4.5 / 7.0秒** |
| `/keirin/picks?include_all=true`（60行） | 0.35〜0.65秒 | — |

`/picks` はループの中で `wt_entries` と `wt_odds_snapshot` を
**1レース1本ずつ**引いており、60レースで最大120往復していた
（41行 0.37秒 ↔ 60行 0.47秒 ＝ 約5ms/行）。

🔴 **戻しても結果は変わらず遅くなるだけ**なので構造で固定する。
"""
from __future__ import annotations

import ast
import inspect

from src.api import keirin_router as R


def _await_calls_inside_loops(func) -> list[str]:
    """関数の `for` ループの**本体**にある `await` 呼び出しの名前を返す。

    ⚠️ `for m in (await db.execute(...))` の `await` は**1回しか評価されない**
       （イテレータ式）。`loop.iter` を数えると正しい書き方まで違反になるので、
       本体（`body` / `orelse`）だけを見る。
    """
    tree = ast.parse(inspect.getsource(func).lstrip())
    found: list[str] = []
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.For | ast.AsyncFor):
            continue
        for stmt in [*loop.body, *loop.orelse]:
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Await):
                    continue
                call = node.value
                fn = call.func if isinstance(call, ast.Call) else None
                found.append(getattr(fn, "attr", None) or getattr(fn, "id", None) or "?")
    return found


def test_get_picks_has_no_query_inside_the_loop():
    """🔴 レースごとの `await db.execute(...)` を戻さないこと。

    出走表は `_fetch_entries_by_race`、合成オッズは `_fetch_snapshot_odds` で
    **1本にまとめて**引き、ループの中は組み立てだけにする。
    """
    assert _await_calls_inside_loops(R.get_picks) == [], (
        "ループ内で DB を引いている（1レース1往復に戻っている）"
    )


def test_get_picks_uses_the_batched_fetchers():
    src = inspect.getsource(R.get_picks)
    assert "_fetch_entries_by_race(" in src
    assert "_fetch_snapshot_odds(" in src
    assert "FROM keirin.wt_entries" not in src, (
        "出走表の SQL は `_fetch_entries_by_race` が正本（ループ内へ戻さない）"
    )


def test_settled_submissions_reuses_the_baked_settlement():
    """🔴 焼き付け済みの採点結果を読むこと。

    読まないと当年ぶん1,091件を毎回採点し直し、`wt_odds`（7.2GB）へ
    2,138回のインデックス参照が飛ぶ。冷えていると 1.5秒かかる。
    """
    src = inspect.getsource(R._fetch_settled_submissions)
    assert "cached_settlement(" in src
    assert "ns.settled_fp" in src, "キャッシュ列を SELECT していない"
    assert "is_cacheable(" in src, "未採点の行まで焼いてはいけない"


def test_settled_submissions_tolerates_missing_columns():
    """🔴 **列がある前提で SELECT しないこと**（2026-08-29）。

    `scripts/deploy-bluegreen.sh` は **新しい backend を healthy にして
    トラフィックを渡した後**（Phase 3.5）に `alembic upgrade head` を走らせる。
    新コードが旧スキーマに当たる窓が必ずあるので、列を直に書くと
    その間 `/summary` `/picks` `/review` が丸ごと 500 になる。
    """
    src = inspect.getsource(R._fetch_settled_submissions)
    assert "_settlement_cache_ready(" in src, "列の有無を確かめてから使うこと"
    assert "if use_cache else" in src, "列が無い間はキャッシュ列を SELECT しない"
    probe = inspect.getsource(R._settlement_cache_ready)
    assert "information_schema.columns" in probe
    assert "except Exception" in probe, "判定できないときはキャッシュなしで続行する"


def test_today_is_never_baked():
    """🔴 **当日のレースを焼かないこと。**

    着順・確定配当が後から直る（再取得・訂正）のは実質その日のうちで、
    焼くとその訂正が二度と反映されない。当日ぶんは60行程度なので
    実採点しても安い（重いのは当年ぶん）。
    """
    src = inspect.getsource(R._fetch_settled_submissions)
    assert '_today_jst().isoformat()' in src
    assert 'str(s["race_date"]) <' in src, "当日以降を焼き付け対象から外すこと"


def test_settlement_write_back_is_best_effort_and_uses_its_own_session():
    """🔴 充填の失敗で表示を落とさない・リクエストのセッションを使わない。

    サマリーの3期間は別セッションで並行に走り同じ行を書きに行くので、
    行ロックの競合がありうる。落ちても次のリクエストがもう一度焼くだけ。
    """
    src = inspect.getsource(R._persist_settlements)
    assert "AsyncSessionLocal()" in src, "リクエストのセッションで書かない"
    assert "except Exception" in src, "充填の失敗は握りつぶす（表示に影響させない）"
    assert "sorted(" in src, "PK 順に並べて deadlock を避ける"


def test_candidate_counts_are_one_scan():
    """🔴 合計とランク別を2本のスキャンに戻さないこと。

    同じ WHERE の同じスキャンで、`SPLIT_PART` の関数結合はインデックスが
    効かない（当年で buffers 19,265）。実測 2本 242ms → まとめて 111ms。
    """
    src = inspect.getsource(R._aggregate)
    assert "GROUPING SETS" in src
    assert src.count("COUNT(DISTINCT SPLIT_PART") == 1, (
        "候補数のスキャンは1本にまとめる（合計はランク別の和にできないので"
        " GROUPING SETS を使う）"
    )
