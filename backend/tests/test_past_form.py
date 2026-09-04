"""`src/indices/past_form.py` — v28 の過去走由来特徴を固定する。

## このテストが守っているもの

1. 🔴 **`scripts/jra_winplace_feature_ab.py` との値の一致**（`test_parity_*`）。
   これが最重要。Phase C の Δ=−0.00750（§11.1）も 2026Q3 の確認成功（§18.1）も
   あの実装で出した数字なので、本番が1ビットでも違う値を作れば**検証結果は本番に
   対して無効になる**。ランダム生成した過去走・レースに対して、検証スクリプトの
   `PastRuns` / `build_pit_features` / `_pace_handicap_pit` と
   `past_form` の出力を全頭ぶん突き合わせる。
2. **point-in-time**（`test_pit_*`）。対象レースと同日・翌日の走を混ぜても
   結果が1つも変わらないこと。同日を含めてしまう実装は例外を出さず、ただ静かに
   未来を読む。
3. **欠損の意味論**（`test_past_runs_*`）。過去走 0 / 1 / 4 / 5 / 6走で
   `None` になる位置と値が正しいこと。🔴 `50.0` で埋めないこと。
4. **閾値をコピーしていないこと**（`test_runner_type_thresholds_*`）。
   `pace_handicap.RUNNER_TYPE_THRESHOLDS` を差し替えると出力が追随する
   ＝ import して使っている。

DB は使わない。純関数と、検証スクリプトの pandas 実装だけで回る。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from src.indices import pace_handicap  # noqa: E402
from src.indices.past_form import (  # noqa: E402
    PAST_FORM_FEATURE_NAMES,
    PAST_N,
    RUNNER_TYPE_ORD,
    CourseFeature,
    PastRun,
    PastRunStore,
    RaceContext,
    compute_past_form_features,
    compute_race_past_form_features,
    compute_runner_type,
    past_form_feature_row,
    predict_pace_type,
    select_past_runs,
)

TARGET_DATE = "20250601"


def _run(date: str, *, finish: float | None = 3, p4: float | None = 8,
         head: float | None = 16, race_id: int | None = None) -> PastRun:
    return PastRun(
        date=date,
        race_id=race_id if race_id is not None else int(date),
        finish_position=finish,
        passing_4=p4,
        head_count=head,
    )


def _ctx(**kw) -> RaceContext:
    base = {"date": TARGET_DATE, "course": "05", "head_count": 16,
            "course_feature": None}
    base.update(kw)
    return RaceContext(**base)


# ---------------------------------------------------------------------------
# 1. point-in-time
# ---------------------------------------------------------------------------


def test_pit_same_day_and_future_runs_do_not_change_anything() -> None:
    """🔴 対象レースと同日・翌日・翌年の走を混ぜても結果が変わらない。

    `date <=` にしてしまう実装（同日の先行レースを読む）と、上限を掛け忘れた実装を
    まとめて捕まえる。
    """
    past = [_run(f"2025050{i}", finish=i, p4=i + 1) for i in range(1, 6)]
    ctx = _ctx()
    clean = compute_past_form_features(past, race_context=ctx, pace_type="normal",
                                       field_head_count=16)

    poisoned = past + [
        _run(TARGET_DATE, finish=1, p4=1),        # 同日の先行レース
        _run("20250602", finish=1, p4=1),          # 翌日
        _run("20260101", finish=1, p4=1),          # 翌年
    ]
    assert compute_past_form_features(
        poisoned, race_context=ctx, pace_type="normal", field_head_count=16
    ) == clean

    # 順序を崩しても同じ（select_past_runs が並べ直す）
    shuffled = list(reversed(poisoned))
    assert compute_past_form_features(
        shuffled, race_context=ctx, pace_type="normal", field_head_count=16
    ) == clean


def test_pit_select_past_runs_is_strictly_before_and_newest_first() -> None:
    runs = [_run("20250101"), _run("20250201"), _run(TARGET_DATE), _run("20250701")]
    got = select_past_runs(runs, before_date=TARGET_DATE, limit=10)
    assert [r.date for r in got] == ["20250201", "20250101"]
    assert select_past_runs(runs, before_date="20250101", limit=10) == []


def test_pit_store_before_is_strictly_before() -> None:
    """学習側アダプタ（`PastRunStore`）も同じ厳密不等号であること。"""
    runs = [_run("20250101"), _run("20250201"), _run(TARGET_DATE), _run("20250701")]
    store = PastRunStore({7: runs})
    got = store.before(7, TARGET_DATE, limit=10)
    assert [r.date for r in got] == ["20250201", "20250101"]
    assert store.before(7, "20240101", limit=10) == []
    assert store.before(999, TARGET_DATE, limit=10) == []


def test_pit_store_and_pure_function_agree() -> None:
    """一括経路（store.before）と純関数の PIT フィルタが同じ集合を返すこと。"""
    runs = [_run(f"2025{m:02d}01", finish=m) for m in range(1, 6)] + [
        _run(TARGET_DATE), _run("20250901")
    ]
    store = PastRunStore({7: runs})
    assert store.before(7, TARGET_DATE, limit=3) == select_past_runs(
        runs, before_date=TARGET_DATE, limit=3
    )


# ---------------------------------------------------------------------------
# 2. 過去走 0 / 1 / 4 / 5 / 6走
# ---------------------------------------------------------------------------


def test_past_runs_zero() -> None:
    f = compute_past_form_features([], race_context=_ctx(), pace_type="normal",
                                   field_head_count=16)
    assert f["runner_type"] == "unknown"
    assert f["runner_type_ord"] is None      # 🔴 50.0 でも 0 でもない
    assert f["finish_var5"] is None
    assert f["win_place_ratio5"] is None
    assert f["n_past5"] == 0
    # pace_handicap_pit は過去走ゼロでも unknown 行から必ず値が出る
    assert f["pace_handicap_pit"] == pytest.approx(50.0)


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_past_runs_fewer_than_five_gives_none(n: int) -> None:
    """5走揃わなければ `finish_var5` / `win_place_ratio5` は None。脚質は出る。"""
    past = [_run(f"2025010{i}", finish=i, p4=2, head=16) for i in range(1, n + 1)]
    f = compute_past_form_features(past, race_context=_ctx(), pace_type="normal",
                                   field_head_count=16)
    assert f["n_past5"] == n
    assert f["finish_var5"] is None
    assert f["win_place_ratio5"] is None
    assert f["runner_type"] == "escape"          # 2/16 = 0.125 < 0.25
    assert f["runner_type_ord"] == RUNNER_TYPE_ORD["escape"] == 0.0


def test_past_runs_exactly_five() -> None:
    finishes = [1, 2, 3, 4, 5]
    past = [_run(f"2025010{i}", finish=fp, p4=8, head=16)
            for i, fp in enumerate(finishes, start=1)]
    f = compute_past_form_features(past, race_context=_ctx(), pace_type="normal",
                                   field_head_count=16)
    assert f["n_past5"] == 5
    assert f["finish_var5"] == pytest.approx(float(np.var(finishes, ddof=1)))
    assert f["win_place_ratio5"] == pytest.approx(1 / 3)   # 勝ち1 / 3着内3
    assert f["runner_type"] == "mid"                        # 8/16 = 0.5


def test_past_runs_six_uses_only_the_latest_five() -> None:
    """6走目（最古）は着順分散・勝率複勝率比に入らない。"""
    old = _run("20250101", finish=18)      # これが混ざると分散が跳ねる
    recent = [_run(f"2025020{i}", finish=fp, p4=8, head=16)
              for i, fp in enumerate([1, 2, 3, 4, 5], start=1)]
    f = compute_past_form_features([old, *recent], race_context=_ctx(),
                                   pace_type="normal", field_head_count=16)
    assert f["n_past5"] == 5
    assert f["finish_var5"] == pytest.approx(float(np.var([1, 2, 3, 4, 5], ddof=1)))


def test_past_runs_five_but_one_invalid_finish_is_none() -> None:
    """🔴 「直近5走を取ってから着順が有効なものを残す」順序であること。

    6走以上あっても直近5走に着順の付かなかった走（NULL / 0）が入っていれば None。
    「有効な着順を5つ集める」実装だと値が出てしまい、検証と別物になる。
    """
    past = [_run("20250101", finish=1), _run("20250102", finish=1),
            _run("20250201", finish=None), _run("20250202", finish=2),
            _run("20250203", finish=3), _run("20250204", finish=4)]
    f = compute_past_form_features(past, race_context=_ctx(), pace_type="normal",
                                   field_head_count=16)
    assert f["n_past5"] == 4
    assert f["finish_var5"] is None
    assert f["win_place_ratio5"] is None


def test_win_place_ratio_no_place_sentinel_is_minus_one() -> None:
    """3着内が0走なら -1.0（欠損の None とは別の水準・検証の `no_place` 群）。"""
    past = [_run(f"2025010{i}", finish=10 + i, p4=8, head=16) for i in range(1, 6)]
    f = compute_past_form_features(past, race_context=_ctx(), pace_type="normal",
                                   field_head_count=16)
    assert f["win_place_ratio5"] == -1.0
    assert f["finish_var5"] is not None


def test_runner_type_unknown_when_passing4_all_missing() -> None:
    past = [_run(f"2025010{i}", finish=i, p4=None, head=16) for i in range(1, 6)]
    f = compute_past_form_features(past, race_context=_ctx(), pace_type="normal",
                                   field_head_count=16)
    assert f["runner_type"] == "unknown"
    assert f["runner_type_ord"] is None
    assert f["finish_var5"] is not None     # 着順側は独立に出る


def test_feature_row_maps_none_to_nan_not_fifty() -> None:
    """🔴 欠損は NaN。`fillna(50.0)` は 34列側だけの作法で、新特徴には掛けない。"""
    f = compute_past_form_features([], race_context=_ctx(), pace_type="normal",
                                   field_head_count=16)
    row = past_form_feature_row(f)
    assert len(row) == len(PAST_FORM_FEATURE_NAMES) == 4
    assert np.isnan(row[0]) and np.isnan(row[1]) and np.isnan(row[2])
    assert not np.isnan(row[3])
    assert 50.0 not in row[:3]


def test_feature_names_match_verification_script_order() -> None:
    """列順が検証スクリプトの `FEAT_EXTRA` と同一であること（順序ずれは静かに壊れる）。"""
    ab = pytest.importorskip("scripts.jra_winplace_feature_ab")
    assert PAST_FORM_FEATURE_NAMES == list(ab.FEAT_EXTRA)
    assert RUNNER_TYPE_ORD == ab.RUNNER_TYPE_ORD
    assert PAST_N == pytest.importorskip("scripts.jra_place_residual_diag").PAST_N


# ---------------------------------------------------------------------------
# 3. 脚質の閾値は import であってコピーではない
# ---------------------------------------------------------------------------


def test_runner_type_thresholds_match_production_table() -> None:
    """各脚質の区間の中点を作れば、その脚質が返ること。"""
    for label, (low, high) in pace_handicap.RUNNER_TYPE_THRESHOLDS.items():
        mid = (low + high) / 2
        head = 100.0
        past = [_run("20250101", p4=mid * head, head=head)]
        assert compute_runner_type(past) == label, label


def test_runner_type_thresholds_are_imported_not_copied(monkeypatch) -> None:
    """🔴 本番の閾値表を差し替えたら出力が追随すること（= import して使っている）。

    コピーした定数で分類していると、この差し替えに追随せず落ちる。
    """
    head = 100.0
    past = [_run("20250101", p4=10.0, head=head)]     # relative_pos = 0.10
    assert compute_runner_type(past) == "escape"

    monkeypatch.setattr(
        pace_handicap,
        "RUNNER_TYPE_THRESHOLDS",
        {"escape": (0.0, 0.05), "leader": (0.05, 0.45),
         "mid": (0.45, 0.65), "closer": (0.65, 1.0)},
    )
    assert compute_runner_type(past) == "leader"


def test_pace_type_uses_production_predict_pace() -> None:
    """`_predict_pace` の閾値（indicator = escape + leader*0.5、fast は >= 2.5）に一致すること。

    ⚠️ `pace_handicap._predict_pace` の docstring は「escape 2頭以上 → fast」と書いているが、
    実装は `indicator >= PACE_INDICATOR_FAST(2.5)` なので **escape 2頭だけでは normal**。
    ここは実装に合わせる（検証スクリプトも実装を import している）。
    """
    assert predict_pace_type(["escape", "escape", "escape"]) == "fast"      # 3.0
    assert predict_pace_type(["escape", "escape", "leader"]) == "fast"      # 2.5
    assert predict_pace_type(["escape", "leader", "leader", "leader"]) == "fast"  # 2.5
    assert predict_pace_type(["escape", "escape", "mid"]) == "normal"       # 2.0
    assert predict_pace_type(["escape", "mid", "closer"]) == "normal"       # 1.0
    assert predict_pace_type(["mid", "mid", "closer"]) == "slow"            # 0.0
    assert predict_pace_type(["leader", "mid", "closer"]) == "slow"         # 0.5


def test_pace_handicap_pit_matches_score_table_without_course_feature() -> None:
    """コース特性が無ければ基本表そのまま（本番と同じく補正が no-op）。"""
    feats = compute_race_past_form_features(
        {
            1: [_run("20250101", p4=2, head=16)],    # escape
            2: [_run("20250101", p4=2, head=16)],    # escape
            3: [_run("20250101", p4=2, head=16)],    # escape -> indicator 3.0 = fast
            4: [_run("20250101", p4=14, head=16)],   # closer
        },
        race_context=_ctx(course_feature=None, head_count=4),
    )
    assert feats[1]["pace_handicap_pit"] == pytest.approx(
        pace_handicap.PACE_SCORE_TABLE["escape"]["fast"]
    )
    assert feats[4]["pace_handicap_pit"] == pytest.approx(
        pace_handicap.PACE_SCORE_TABLE["closer"]["fast"]
    )


def test_pace_handicap_pit_is_not_rounded_like_production() -> None:
    """🔴 検証実装は `round(_, 1)` をしていない。本番の丸めを持ち込むと値がずれる。

    本番 `calculate_batch` は最後に `round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)` を
    するが、`jra_winplace_feature_ab._pace_handicap_pit` はクリップだけで丸めていない。
    実データでも `52.46228571428571` のような値が出る（20260628 で実測）。
    """
    escape = [_run("20250101", p4=2, head=16)]
    feats = compute_race_past_form_features(
        {1: list(escape), 2: list(escape), 3: list(escape)},   # indicator 3.0 -> fast
        race_context=_ctx(
            head_count=16,                       # >= LARGE_FIELD(14) で多頭数補正も乗る
            course_feature=CourseFeature(straight_distance=355.7,
                                         corner_tightness=0.71,
                                         start_to_corner_m=110.0),
        ),
    )
    v = feats[1]["pace_handicap_pit"]
    # 45.0(escape/fast) + コーナー補正 - 多頭数補正2種 = 42.914285714...
    assert v == pytest.approx(42.9142857142857, abs=1e-9)
    assert abs(v - round(v, 1)) > 1e-3, v


# ---------------------------------------------------------------------------
# 4. 🔴 検証スクリプト（jra_winplace_feature_ab.py）との一致
# ---------------------------------------------------------------------------


def _build_case(seed: int, n_horses: int, n_races: int):
    """ランダムな過去走とレースを作る（検証実装と本モジュールへ同じものを渡す）。"""
    rng = np.random.default_rng(seed)
    past_rows = []
    for hid in range(1, n_horses + 1):
        n = int(rng.integers(0, 13))          # 0〜12走
        for k in range(n):
            date = f"2024{1 + k // 28:02d}{1 + k % 28:02d}"
            head = int(rng.integers(5, 19))
            p4 = rng.choice([None, float(rng.integers(1, head + 1))], p=[0.12, 0.88])
            fp = rng.choice([None, 0.0, float(rng.integers(1, head + 1))],
                            p=[0.05, 0.05, 0.90])
            past_rows.append({
                "horse_id": hid,
                "race_id": int(f"{date}{hid:03d}{k:02d}"),
                "date": date,
                "finish_position": fp,
                "passing_4": p4,
                "running_style": None,
                "head_count": float(head),
            })

    target_rows = []
    courses = ["05", "06", "09", "99"]        # 99 = racecourse_features に無いコース
    for r in range(n_races):
        race_id = 900000 + r
        date = "20250601"
        field = sorted(rng.choice(np.arange(1, n_horses + 1),
                                  size=int(rng.integers(5, 17)), replace=False).tolist())
        head_count = float(len(field)) if r % 3 else float("nan")
        for hid in field:
            target_rows.append({
                "race_id": race_id, "horse_id": int(hid), "date": date,
                "course": courses[r % len(courses)], "head_count": head_count,
            })
    return past_rows, target_rows


_COURSE_FEATURES = {
    "05": CourseFeature(straight_distance=525.9, corner_tightness=0.42,
                        start_to_corner_m=310.0),
    "06": CourseFeature(straight_distance=355.7, corner_tightness=0.71,
                        start_to_corner_m=110.0),
    "09": CourseFeature(straight_distance=264.0, corner_tightness=0.80,
                        start_to_corner_m=95.0),
}


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_parity_with_winplace_feature_ab(seed: int) -> None:
    """🔴 最重要。検証スクリプトと同じ入力に対して同じ値を返すこと。

    突き合わせるのは `runner_type` / `runner_type_ord` / `finish_var5` /
    `win_place_ratio5` / `n_past5` / `pace_handicap_pit` の全頭ぶん。
    検証側は `jra_place_residual_diag.PastRuns` + `build_pit_features` と
    `jra_winplace_feature_ab._pace_handicap_pit` をそのまま呼ぶ（再実装しない）。
    """
    pd = pytest.importorskip("pandas")
    diag = pytest.importorskip("scripts.jra_place_residual_diag")
    ab = pytest.importorskip("scripts.jra_winplace_feature_ab")
    from types import SimpleNamespace

    past_rows, target_rows = _build_case(seed, n_horses=40, n_races=12)

    # --- 検証スクリプト側 ---
    past_df = pd.DataFrame(past_rows, columns=[
        "horse_id", "date", "finish_position", "passing_4", "running_style", "head_count",
    ])
    tgt_df = pd.DataFrame(target_rows)
    expected = diag.build_pit_features(tgt_df, diag.PastRuns(past_df))
    expected["runner_type_ord"] = expected["runner_type"].map(ab.RUNNER_TYPE_ORD).astype(float)
    course_feat_ns = {
        code: SimpleNamespace(straight_distance=cf.straight_distance,
                              corner_tightness=cf.corner_tightness,
                              start_to_corner_m=cf.start_to_corner_m)
        for code, cf in _COURSE_FEATURES.items()
    }
    expected["pace_handicap_pit"] = ab._pace_handicap_pit(expected, course_feat_ns)

    # --- 本モジュール側 ---
    store = PastRunStore.from_rows(past_rows)
    got: dict[tuple[int, int], dict] = {}
    for race_id, grp in tgt_df.groupby("race_id", sort=False):
        date = str(grp["date"].iloc[0])
        course = str(grp["course"].iloc[0])
        hc = grp["head_count"].iloc[0]
        ctx = RaceContext(
            date=date,
            course=course,
            head_count=None if pd.isna(hc) else hc,
            course_feature=_COURSE_FEATURES.get(course),
        )
        feats = compute_race_past_form_features(
            {int(h): store.before(int(h), date) for h in grp["horse_id"]},
            race_context=ctx,
        )
        for hid, f in feats.items():
            got[(int(race_id), int(hid))] = f

    assert len(got) == len(expected)
    n_checked = 0
    for row in expected.itertuples():
        f = got[(int(row.race_id), int(row.horse_id))]
        key = f"race={row.race_id} horse={row.horse_id}"
        assert f["runner_type"] == row.runner_type, key
        assert f["n_past5"] == int(row.n_past5), key
        _assert_same_number(f["runner_type_ord"], row.runner_type_ord, key + " ord")
        _assert_same_number(f["finish_var5"], row.finish_var5, key + " var")
        _assert_same_number(f["win_place_ratio5"], row.win_place_ratio5, key + " ratio")
        _assert_same_number(f["pace_handicap_pit"], row.pace_handicap_pit, key + " pace")
        n_checked += 1
    assert n_checked > 50


async def test_live_adapter_and_training_adapter_map_rows_identically() -> None:
    """🔴 train/serve skew の予防そのもの。

    配信経路（`fetch_past_runs_for_race`・async SQLAlchemy）と学習経路
    （`PastRunStore.before`・一括）が、**同じ DB 行に対して同じ `PastRun` 列**を返すこと。
    DB は使わず、SQLAlchemy の行だけを模したオブジェクトを `db.execute` に返させる。

    ここがずれると地方 v13→v14 と同じ型の事故（指数1位馬の勝率 −9pt）になる。
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.indices.past_form import fetch_past_runs_for_race

    rows_raw = []
    for hid in (11, 22):
        for k in range(14):                     # LOOKBACK_RACES(10) より多く積む
            rows_raw.append({
                "horse_id": hid,
                "race_id": 1000 + k,
                "date": f"202501{k + 1:02d}",
                "finish_position": float((k % 9) + 1),
                "passing_4": float((k % 12) + 1),
                "head_count": 16.0,
            })
    # 対象レース当日・翌日の行は SQL 側（Race.date < before_date）で落ちるので渡さない。

    class _Row(SimpleNamespace):
        pass

    # SQL の ORDER BY と同じ「horse_id 昇順・date 降順」で返す（安定ソートの2段掛け）
    ordered = sorted(rows_raw, key=lambda r: r["date"], reverse=True)
    ordered = sorted(ordered, key=lambda r: r["horse_id"])
    rows = [
        _Row(
            RaceResult=SimpleNamespace(
                horse_id=r["horse_id"], race_id=r["race_id"],
                finish_position=r["finish_position"], passing_4=r["passing_4"],
            ),
            Race=SimpleNamespace(date=r["date"], head_count=r["head_count"]),
        )
        for r in ordered
    ]
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: rows)

    live = await fetch_past_runs_for_race(
        db, [11, 22], before_date=TARGET_DATE, exclude_race_id=1
    )
    store = PastRunStore.from_rows(rows_raw)
    for hid in (11, 22):
        train = store.before(hid, TARGET_DATE, limit=pace_handicap.LOOKBACK_RACES)
        assert len(live[hid]) == pace_handicap.LOOKBACK_RACES
        assert live[hid] == train, hid

    # 特徴まで通しても一致すること
    ctx = _ctx(head_count=12)
    assert compute_race_past_form_features(live, race_context=ctx) == \
        compute_race_past_form_features(
            {hid: store.before(hid, TARGET_DATE) for hid in (11, 22)}, race_context=ctx
        )


def test_bulk_entry_point_matches_per_race_entry_point() -> None:
    """学習の入口 `build_past_form_features_bulk` がレース単位版と同じ値を返すこと。"""
    from src.indices.past_form import build_past_form_features_bulk

    past_rows, target_rows = _build_case(1, n_horses=25, n_races=5)
    store = PastRunStore.from_rows(past_rows)
    by_race: dict[int, list[int]] = {}
    for r in target_rows:
        by_race.setdefault(r["race_id"], []).append(r["horse_id"])

    fields = []
    expected: dict[tuple[int, int], dict] = {}
    for rid, hids in by_race.items():
        ctx = _ctx(course="05", head_count=len(hids),
                   course_feature=_COURSE_FEATURES["05"])
        fields.append((rid, ctx, hids))
        for hid, f in compute_race_past_form_features(
            {h: store.before(h, ctx.date) for h in hids}, race_context=ctx
        ).items():
            expected[(rid, hid)] = f

    assert build_past_form_features_bulk(fields, store=store) == expected
    assert len(expected) == len(target_rows)


async def test_live_adapter_returns_empty_for_horse_without_past_runs() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.indices.past_form import fetch_past_runs_for_race

    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: [])

    got = await fetch_past_runs_for_race(db, [5, 6], before_date=TARGET_DATE)
    assert got == {5: [], 6: []}
    assert await fetch_past_runs_for_race(db, [], before_date=TARGET_DATE) == {}


def _assert_same_number(got, want, key: str) -> None:
    """None ⇔ NaN の対応を含めて完全一致を見る（検証側は NaN・本モジュールは None）。"""
    want_missing = want is None or (isinstance(want, float) and np.isnan(want))
    if got is None:
        assert want_missing, f"{key}: 本モジュール None / 検証 {want!r}"
        return
    assert not want_missing, f"{key}: 本モジュール {got!r} / 検証 欠損"
    assert float(got) == pytest.approx(float(want), rel=0, abs=0), key


def test_parity_covers_every_branch() -> None:
    """一致テストのケースが「全部 unknown / 全部 NaN」で通っていないことを確かめる。

    網羅していない一致テストは通っても何も保証しない。
    """
    pd = pytest.importorskip("pandas")
    diag = pytest.importorskip("scripts.jra_place_residual_diag")
    past_rows, target_rows = _build_case(0, n_horses=40, n_races=12)
    past_df = pd.DataFrame(past_rows, columns=[
        "horse_id", "date", "finish_position", "passing_4", "running_style", "head_count",
    ])
    ab = pytest.importorskip("scripts.jra_winplace_feature_ab")
    from types import SimpleNamespace

    out = diag.build_pit_features(pd.DataFrame(target_rows), diag.PastRuns(past_df))
    types = set(out["runner_type"])
    assert {"escape", "leader", "mid", "closer", "unknown"} <= types, types
    assert out["finish_var5"].notna().sum() > 0
    assert out["finish_var5"].isna().sum() > 0
    assert (out["win_place_ratio5"] == -1.0).sum() > 0     # no_place 群も通っている

    cf = {c: SimpleNamespace(straight_distance=v.straight_distance,
                             corner_tightness=v.corner_tightness,
                             start_to_corner_m=v.start_to_corner_m)
          for c, v in _COURSE_FEATURES.items()}
    ph = ab._pace_handicap_pit(out, cf)
    # コース補正が実際に効いている（基本表の丸い値だけになっていない）ことを確かめる。
    # 全部 50.0 / 70.0 のような表の素値だと pace_handicap_pit の一致は何も保証しない。
    assert len(set(np.round(ph, 4))) >= 8, sorted(set(np.round(ph, 4)))
    assert any(abs(v - round(v, 1)) > 1e-9 for v in ph)
    # コース特性が無いコース（"99"）＝補正 no-op の経路も通っている
    assert (pd.DataFrame(target_rows)["course"] == "99").sum() > 0
