"""総合指数 v28（単勝ヘッド38列 / 複勝の独立ヘッド）を固定する。

計画: `docs/jra_winplace_structure_plan_2026_09_04.md` §16。

## このテストが守っているもの

1. 🔴 **train/serve の特徴一致**（`test_train_and_serve_*`）。**本実装の最大のリスク**。
   学習（`scripts/train_jra_iswin_head.load_v28_dataset` の経路）と配信
   （`composite._build_v26_features` + `_build_v28_features` の経路）が、
   **同じ入力に対して同じ38列を同じ順序で**作ること。
   地方 v13→v14 はこの型の skew で指数1位馬の勝率を 9pt 落とした。
2. **`place_slots` ごとにラベルが変わる**こと（§13.1 罠1 / §18.3-2）。
   `n>=8` → 3着以内 / `5<=n<=7` → 2着以内 / `n<5` → 学習から除外。
3. **`Σp_place = place_slots`**（クリップの扱い込み）。クリップは**再正規化しない**
   ので、1 を超える馬が出たレースだけ Σ が崩れることまで固定する。
4. **モデル未ロード時に Harville へ落ちる**こと。
5. `COMPOSITE_VERSION = 28` と `SUBINDEX_MIN_VERSION = 26` の関係。

DB は使わない（接続は全て模擬）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from src.indices import composite as C  # noqa: E402
from src.indices.past_form import PAST_FORM_FEATURE_NAMES  # noqa: E402

# ---------------------------------------------------------------------------
# 1. 特徴名と版
# ---------------------------------------------------------------------------


class TestFeatureNames:
    def test_v28_is_34_plus_past_form_in_this_order(self) -> None:
        assert C.V28_FEATURE_NAMES == (
            list(C.OUT_PROB_FEATURE_NAMES) + list(PAST_FORM_FEATURE_NAMES)
        )
        assert len(C.V28_FEATURE_NAMES) == 38
        assert len(C.OUT_PROB_FEATURE_NAMES) == 34

    def test_matches_the_verified_arm(self) -> None:
        """🔴 検証で `feat` 腕として測った列名・順序と1文字も違わないこと。

        §11.1 の Δ=−0.00750 も §18.1 の 2026Q3 確認成功も、この列並びで出した数字。
        """
        from scripts.jra_winplace_feature_ab import ARMS

        assert ARMS["feat"]["names"] == C.V28_FEATURE_NAMES
        assert ARMS["feat"]["cols"] == C.V28_FEATURE_NAMES

    def test_out_prob_features_unchanged(self) -> None:
        """🔴 着外率ヘッド・順位回帰ヘッドは 34列のまま（v28 で変えない）。"""
        assert C.OUT_PROB_FEATURE_NAMES == C._V26_FEATURE_NAMES


class TestVersions:
    def test_composite_version_is_28(self) -> None:
        assert C.COMPOSITE_VERSION == 28

    def test_subindex_min_version_stays_26(self) -> None:
        """🔴 サブ指数の下限は v26 のまま。`COMPOSITE_VERSION` で引いてはいけない。"""
        assert C.SUBINDEX_MIN_VERSION == 26
        assert C.SUBINDEX_MIN_VERSION < C.COMPOSITE_VERSION
        assert f"version >= {C.SUBINDEX_MIN_VERSION}" in C.SUBINDEX_SOURCE_SQL
        assert str(C.COMPOSITE_VERSION) not in C.SUBINDEX_SOURCE_SQL


# ---------------------------------------------------------------------------
# 2. place_slots とラベル
# ---------------------------------------------------------------------------


class TestPlaceSlots:
    @pytest.mark.parametrize(
        "n,expected",
        [(18, 3), (8, 3), (7, 2), (6, 2), (5, 2), (4, 0), (2, 0), (1, 0), (0, 0)],
    )
    def test_slots_from_field_size(self, n: int, expected: int) -> None:
        assert C.place_slots_for_field(n) == expected

    def test_agrees_with_harville_branch_for_n_ge_5(self) -> None:
        """独立ヘッドの枠数と Harville の払戻対象着順が 5頭以上で一致すること。

        （n<5 は Harville 側に「複勝なし」の概念が無いので対象外。
        本番はそのレースを Harville へ落とす。）
        """
        for n in range(5, 19):
            probs = [1.0 / n] * n
            pp = C.CompositeIndexCalculator._harville_place_probs(probs)
            assert round(sum(pp), 6) == C.place_slots_for_field(n)

    def test_label_changes_with_slots(self) -> None:
        """🔴 n=8 → 3着以内 / n=6 → 2着以内 / n=4 → 学習から除外。"""
        from scripts.train_jra_placed_head import drop_no_place_slots, is_placed_label

        df = pd.DataFrame({
            "race_id": [1] * 8 + [2] * 6 + [3] * 4,
            "n_runners": [8] * 8 + [6] * 6 + [4] * 4,
            "finish_position": list(range(1, 9)) + list(range(1, 7)) + list(range(1, 5)),
        })
        df["place_slots"] = [C.place_slots_for_field(n) for n in df["n_runners"]]
        assert list(df["place_slots"].unique()) == [3, 2, 0]

        kept = drop_no_place_slots(df)
        assert len(kept) == 14                      # n=4 のレースだけ落ちる
        assert set(kept["race_id"]) == {1, 2}

        y = is_placed_label(kept)
        assert list(y[:8]) == [1, 1, 1, 0, 0, 0, 0, 0]      # n=8 → 3着以内
        assert list(y[8:]) == [1, 1, 0, 0, 0, 0]            # n=6 → 2着以内

    def test_label_refuses_slots_zero(self) -> None:
        """`place_slots=0` を学習に渡したら静かに通さず落ちること（§18.3-2）。"""
        from scripts.train_jra_placed_head import is_placed_label

        df = pd.DataFrame({"place_slots": [3, 0], "finish_position": [1, 1]})
        with pytest.raises(ValueError, match="place_slots"):
            is_placed_label(df)


# ---------------------------------------------------------------------------
# 3. Σp = place_slots とクリップ
# ---------------------------------------------------------------------------


class TestNormalizeToSlots:
    def test_sums_to_slots(self) -> None:
        rng = np.random.default_rng(0)
        for n, slots in ((16, 3), (12, 3), (8, 3), (7, 2), (5, 2)):
            raw = rng.uniform(0.01, 0.9, size=n)
            p = C.normalize_place_to_slots(raw, slots)
            assert len(p) == n
            assert sum(p) == pytest.approx(slots, abs=1e-9)

    def test_preserves_within_race_order(self) -> None:
        raw = [0.1, 0.5, 0.3, 0.2, 0.8, 0.05, 0.4, 0.35]
        p = C.normalize_place_to_slots(raw, 3)
        assert np.argsort(p).tolist() == np.argsort(raw).tolist()

    def test_clip_only_no_renormalize(self) -> None:
        """🔴 1 を超えた馬はクリップするだけで、**再正規化しない**（決定・固定）。

        その結果 Σ は `place_slots` からクリップした分だけ小さくなる。
        これは検証実装（`jra_place_head_ab._top3_norm`）と同一の挙動で、
        §13.1 罠2 が「最大乖離 0.134」と報告したのがこの崩れ。
        """
        # 1頭が突出していると正規化後に 1 を超える
        raw = [0.95] + [0.02] * 7
        p = C.normalize_place_to_slots(raw, 3)
        assert p[0] == pytest.approx(1.0, abs=1e-8)          # クリップされた
        assert sum(p) < 3.0                                   # 再正規化していない
        assert sum(p) == pytest.approx(3.0 - (0.95 * 3 / sum(raw) - 1.0), abs=1e-6)

    def test_returns_empty_when_no_place_payout(self) -> None:
        """n<5（`place_slots=0`）では空を返す ＝ 呼び出し側が Harville に落ちる。"""
        assert C.normalize_place_to_slots([0.3, 0.2, 0.1, 0.4], 0) == []
        assert C.normalize_place_to_slots([], 3) == []

    def test_zero_total_is_passed_through(self) -> None:
        p = C.normalize_place_to_slots([0.0, 0.0, 0.0, 0.0, 0.0], 2)
        assert all(v == pytest.approx(C._PROB_EPS) for v in p)


# ---------------------------------------------------------------------------
# 4. フォールバック（モデル未ロード）
# ---------------------------------------------------------------------------


class TestFallback:
    def test_place_override_none_falls_back_to_harville(self) -> None:
        results = [{"composite_index": 60.0 - i, "horse_id": i} for i in range(10)]
        win = [0.1] * 10
        C.CompositeIndexCalculator._attach_probabilities(results, win_override=win)
        expected = C.CompositeIndexCalculator._harville_place_probs(win)
        assert [r["place_probability"] for r in results] == [
            round(v, 4) for v in expected
        ]
        assert sum(r["place_probability"] for r in results) == pytest.approx(3.0, abs=1e-3)

    def test_length_mismatch_falls_back_to_harville(self) -> None:
        results = [{"composite_index": 60.0 - i, "horse_id": i} for i in range(10)]
        win = [0.1] * 10
        C.CompositeIndexCalculator._attach_probabilities(
            results, win_override=win, place_override=[0.5] * 3  # 長さが合わない
        )
        expected = C.CompositeIndexCalculator._harville_place_probs(win)
        assert [r["place_probability"] for r in results] == [
            round(v, 4) for v in expected
        ]

    def test_place_override_is_used_when_given(self) -> None:
        results = [{"composite_index": 60.0 - i, "horse_id": i} for i in range(8)]
        win = [0.125] * 8
        override = C.normalize_place_to_slots([0.4] * 8, 3)
        C.CompositeIndexCalculator._attach_probabilities(
            results, win_override=win, place_override=override
        )
        assert sum(r["place_probability"] for r in results) == pytest.approx(3.0, abs=1e-3)
        assert results[0]["place_probability"] == round(override[0], 4)

    def test_loader_returns_none_when_model_file_missing(self, monkeypatch, tmp_path) -> None:
        """モデルが無ければ None（例外を投げない）＝ 呼び出し側が Harville へ落ちる。"""
        monkeypatch.setattr(C, "_V28_PLACED_MODEL_PATH", tmp_path / "nope.txt")
        monkeypatch.setattr(C, "_v28_placed_model_cache", None)
        monkeypatch.setattr(C, "_v28_placed_load_attempted", False)
        assert C._load_v28_placed_model() is None

        monkeypatch.setattr(C, "_V28_ISWIN_MODEL_PATH", tmp_path / "nope2.txt")
        monkeypatch.setattr(C, "_v28_iswin_model_cache", None)
        monkeypatch.setattr(C, "_v28_iswin_load_attempted", False)
        assert C._load_v28_iswin_model() is None


# ---------------------------------------------------------------------------
# 5. 🔴 train/serve の特徴一致（本実装の最大のリスク）
# ---------------------------------------------------------------------------

_SUBINDEX_DB_COLS = [
    "speed_index", "last_3f_index", "course_aptitude", "position_advantage",
    "rotation_index", "jockey_index", "pace_index", "pedigree_index",
    "training_index", "anagusa_index", "paddock_index", "rebound_index",
    "rivals_growth_index", "career_phase_index", "distance_change_index",
    "jockey_trainer_combo_index", "going_pedigree_index",
]
# 配信側 `results` の key（`_build_v26_features` が読む名前）。DB 列名と1つだけ違う。
_SUBINDEX_SERVE_KEYS = ["last3f_index" if c == "last_3f_index" else c
                        for c in _SUBINDEX_DB_COLS]

_RACE = dict(date="20260215", course="05", head_count=10, distance=1800,
             surface="芝", condition="良", grade="G3")
_N = 10


def _horses() -> list[dict]:
    """1レース分の入力（DB から来る生の値）。欠損の意味論も混ぜる。"""
    rng = np.random.default_rng(7)
    rows = []
    for i in range(_N):
        r = {"horse_id": 100 + i, "horse_number": i + 1,
             "frame_number": i // 2 + 1, "horse_age": 3 + (i % 4),
             "weight_carried": 54.0 + i * 0.5, "horse_weight": 460 + i * 3,
             "weight_change": i - 4,
             "jvan_time_dm": None if i == 2 else 40.0 + i,      # 欠損 → 両経路 50.0
             "jvan_battle_dm": None if i in (2, 5) else 45.0 + i}
        for c in _SUBINDEX_DB_COLS:
            r[c] = float(round(rng.uniform(30, 70), 1))
        if i == 7:
            r["horse_weight"] = None          # 欠損 → 両経路 NaN
            r["weight_change"] = None
        rows.append(r)
    return rows


def _past_run_rows(horses: list[dict]) -> list[dict]:
    """過去走（`past_form.PAST_RUNS_SQL` / `fetch_past_runs_for_race` が返す形）。"""
    rows = []
    for i, h in enumerate(horses):
        n_past = 0 if i == 3 else (3 if i == 4 else 8)   # 0走 / 5走未満 / 十分
        for k in range(n_past):
            rows.append({
                "horse_id": h["horse_id"], "race_id": 900 + k,
                "date": f"20251{k % 2}{10 + k:02d}",
                "finish_position": float((i + k) % 12 + 1),
                "passing_4": float((i + k) % 14 + 1),
                "head_count": 14.0,
            })
    return rows


_COURSE_ROW = ("05", 525.0, 0.5, 400.0)


class _FakeCursor:
    """`past_form.load_past_run_store` / `load_course_features` 用の擬似カーソル。"""

    def __init__(self, past_rows: list[dict]) -> None:
        self._past = past_rows
        self._result: list = []

    def execute(self, sql: str, params: dict | None = None) -> None:
        if "racecourse_features" in sql:
            self._result = [_COURSE_ROW]
        else:
            self._result = [
                (r["horse_id"], r["race_id"], r["date"], r["finish_position"],
                 r["passing_4"], r["head_count"])
                for r in sorted(self._past, key=lambda x: (x["horse_id"], x["date"]))
            ]

    def fetchall(self) -> list:
        return self._result

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, past_rows: list[dict]) -> None:
        self._past = past_rows

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._past)


def _train_rows() -> np.ndarray:
    """学習経路: 生の DB 行 → `prod_featurize` → `attach_past_form` → 38列。"""
    from scripts.train_jra_iswin_head import attach_past_form

    horses = _horses()
    df = pd.DataFrame([
        {"race_id": 1, "date": _RACE["date"], "course": _RACE["course"],
         "head_count": _RACE["head_count"], "distance": _RACE["distance"],
         "surface": _RACE["surface"], "condition": _RACE["condition"],
         "grade": _RACE["grade"], **h}
        for h in horses
    ])
    from scripts.train_jra_out_rate import featurize as prod_featurize

    df = prod_featurize(df)
    df = attach_past_form(df, _FakeConn(_past_run_rows(horses)), end="20260215")
    return df[C.V28_FEATURE_NAMES].to_numpy(dtype=float)


async def _serve_rows() -> np.ndarray:
    """配信経路: `_build_v26_features` + `build_past_form_features_for_race` → 38列。"""
    from src.indices.past_form import build_past_form_features_for_race

    horses = _horses()
    results = [
        {"horse_id": h["horse_id"],
         **{k: h[c] for k, c in zip(_SUBINDEX_SERVE_KEYS, _SUBINDEX_DB_COLS)}}
        for h in horses
    ]
    race = SimpleNamespace(id=1, **_RACE)
    entries = {
        h["horse_id"]: SimpleNamespace(
            frame_number=h["frame_number"], horse_age=h["horse_age"],
            weight_carried=h["weight_carried"], horse_weight=h["horse_weight"],
            jvan_time_dm=h["jvan_time_dm"], jvan_battle_dm=h["jvan_battle_dm"])
        for h in horses
    }
    wc = {h["horse_id"]: h["weight_change"] for h in horses}
    x26 = C._build_v26_features(results, race, entries, wc)

    # 配信側の DB（過去走 → `.all()` / コース特性 → `.scalars().first()`）を模擬する
    past = _past_run_rows(horses)
    ordered = sorted(past, key=lambda r: r["date"], reverse=True)
    ordered = sorted(ordered, key=lambda r: r["horse_id"])
    rows = [
        SimpleNamespace(
            RaceResult=SimpleNamespace(
                horse_id=r["horse_id"], race_id=r["race_id"],
                finish_position=r["finish_position"], passing_4=r["passing_4"]),
            Race=SimpleNamespace(date=r["date"], head_count=r["head_count"]),
        )
        for r in ordered
    ]
    course_row = SimpleNamespace(
        straight_distance=_COURSE_ROW[1], corner_tightness=_COURSE_ROW[2],
        start_to_corner_m=_COURSE_ROW[3])
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(all=lambda: rows),
        SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: course_row)),
    ]
    feats = await build_past_form_features_for_race(
        db, race=race, horse_ids=[r["horse_id"] for r in results]
    )
    return C._build_v28_features(x26, results, feats)


async def test_train_and_serve_build_identical_38_columns() -> None:
    """🔴 **本実装の最大のリスク**。学習経路と配信経路が同じ38列を同じ順序で作ること。

    NaN の位置まで含めて完全一致を見る（`50.0` で埋めてしまうと NaN の位置がずれて落ちる）。
    """
    train, serve = _train_rows(), await _serve_rows()
    assert train.shape == serve.shape == (_N, 38)
    np.testing.assert_array_equal(np.isnan(train), np.isnan(serve))
    np.testing.assert_allclose(
        np.nan_to_num(train, nan=-12345.0), np.nan_to_num(serve, nan=-12345.0),
        rtol=0, atol=0,
    )


async def test_new_features_stay_nan_and_are_not_filled_with_fifty() -> None:
    """🔴 新特徴の欠損は NaN のまま（`50.0` にしない）。34列側の `fillna(50.0)` は維持。"""
    train, serve = _train_rows(), await _serve_rows()
    _ = len(C.OUT_PROB_FEATURE_NAMES)

    # 過去走 0走(index 3) / 3走(index 4) の馬は着順分散・勝率複勝率比が NaN
    i_var = C.V28_FEATURE_NAMES.index("finish_var5")
    i_ratio = C.V28_FEATURE_NAMES.index("win_place_ratio5")
    i_ord = C.V28_FEATURE_NAMES.index("runner_type_ord")
    for arr in (train, serve):
        assert np.isnan(arr[3, i_var]) and np.isnan(arr[3, i_ratio])
        assert np.isnan(arr[4, i_var]) and np.isnan(arr[4, i_ratio])
        assert np.isnan(arr[3, i_ord])          # 過去走ゼロ ⇒ 脚質 unknown ⇒ NaN
        assert not np.isnan(arr[0, i_var])
        # 欠損しうる3列が `50.0` で埋まっていないこと
        # （`pace_handicap_pit` は常に値が出る量で、素点が 50 付近なので対象外）
        assert not np.any(arr[[3, 4]][:, [i_ord, i_var, i_ratio]] == 50.0)

    # 34列側: サブ指数と DM は 50.0 で埋まる / 馬体重・増減は NaN のまま
    i_dm = C.V28_FEATURE_NAMES.index("jvan_time_dm")
    i_hw = C.V28_FEATURE_NAMES.index("horse_weight")
    for arr in (train, serve):
        assert arr[2, i_dm] == 50.0
        assert np.isnan(arr[7, i_hw])


async def test_serve_uses_field_size_when_head_count_is_null() -> None:
    """🔴 `races.head_count` は発走前 NULL。配信でもフィールドの馬数へ落ちること。

    `place_slots` は `head_count` を一切見ない（`place_slots_for_field`）。
    `pace_handicap_pit` の頭数補正だけが `head_count` を見るので、そこが
    NULL でも例外にならず、フィールドの馬数で埋まることを固定する。
    """
    from src.indices.past_form import build_past_form_features_for_race

    horses = _horses()
    race = SimpleNamespace(id=1, **{**_RACE, "head_count": None})
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(all=lambda: []),
        SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None)),
    ]
    feats = await build_past_form_features_for_race(
        db, race=race, horse_ids=[h["horse_id"] for h in horses]
    )
    assert len(feats) == _N
    assert all(f["pace_handicap_pit"] is not None for f in feats.values())
    assert C.place_slots_for_field(len(horses)) == 3
