"""v29（ラップ7列）の配線を固定する。

- 列の順序: `V29_FEATURE_NAMES = V28_FEATURE_NAMES + LAP_FORM_FEATURE_NAMES`
- 学習・配信・検証が同じ組み立て（`lap_form.build_lap_context`）を通ること（AST）
- 配信は「フィールドの馬が走った区分のレースだけ」を読んで par を作る。
  それが全期間を読む学習側と**同じ値**になること（純関数で再現して比べる）

事前登録: `docs/jra_lap_v29_q3_confirm_plan_2026_11_07.md`。
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import numpy as np

from src.indices import composite as C
from src.indices.lap_form import (
    LAP_FORM_FEATURE_NAMES,
    build_lap_context,
    lap_features_for_horses,
)

_BACKEND = Path(__file__).resolve().parents[1]


def _imports_from(path: Path, module_suffix: str) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(module_suffix):
            names |= {a.name for a in node.names}
    return names


def _calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


# ---------------------------------------------------------------------------
# 1. 列と版
# ---------------------------------------------------------------------------


def test_v29_columns_are_v28_plus_lap() -> None:
    assert C.COMPOSITE_VERSION == 29
    assert C.V29_FEATURE_NAMES == list(C.V28_FEATURE_NAMES) + list(LAP_FORM_FEATURE_NAMES)
    assert len(C.V29_FEATURE_NAMES) == 45


def test_train_scripts_use_v29_columns() -> None:
    import scripts.train_jra_iswin_head as T

    assert T.HEAD_FEATURE_NAMES == C.V29_FEATURE_NAMES
    assert T.MODEL_PATH.name == "v29_iswin_calib.txt"
    import scripts.train_jra_placed_head as P

    assert P.MODEL_PATH.name == "v29_placed_head.txt"
    assert "HEAD_FEATURE_NAMES" in _imports_from(
        _BACKEND / "scripts" / "train_jra_placed_head.py", "train_jra_iswin_head"
    )


def test_build_v29_features_appends_lap_with_nan() -> None:
    x28 = np.zeros((2, len(C.V28_FEATURE_NAMES)))
    results = [{"horse_id": 1}, {"horse_id": 2}]
    lap = {1: dict.fromkeys(LAP_FORM_FEATURE_NAMES, 0.5)}
    x = C._build_v29_features(x28, results, lap)
    assert x.shape == (2, 45)
    assert np.all(x[0, 38:] == 0.5)
    assert np.all(np.isnan(x[1, 38:]))  # 🔴 欠損は NaN のまま（50.0 で埋めない）


# ---------------------------------------------------------------------------
# 2. 同じ組み立てを通ること（AST）
# ---------------------------------------------------------------------------


def test_composite_gets_lap_only_from_lap_form() -> None:
    path = _BACKEND / "src" / "indices" / "composite.py"
    assert "build_lap_form_features_for_race" in _imports_from(path, "lap_form")
    calls = _calls(path)
    assert "build_lap_form_features_for_race" in calls
    # 別実装をしない: ラップの解析・par を composite に書かない
    for forbidden in ("parse_lap_times", "summarize_race_laps", "ParTable", "LapRunStore"):
        assert forbidden not in calls


def test_composite_falls_back_to_v28_heads() -> None:
    src = (_BACKEND / "src" / "indices" / "composite.py").read_text()
    assert '_load_v29_model("iswin")' in src and "_load_v28_iswin_model()" in src
    assert '_load_v29_model("placed")' in src and "_load_v28_placed_model()" in src


def test_serving_adapter_uses_shared_builder() -> None:
    import inspect

    from src.indices import lap_form

    src = inspect.getsource(lap_form.build_lap_form_features_for_race)
    assert "build_lap_context(" in src
    assert "lap_features_for_horses(" in src
    assert "LAP_HISTORY_START" in src


def test_training_backfill_and_validation_share_loader() -> None:
    t = _BACKEND / "scripts" / "train_jra_iswin_head.py"
    assert {"load_lap_context", "lap_features_for_horses"} <= _imports_from(t, "lap_form")
    ab = _BACKEND / "scripts" / "jra_lap_feature_ab.py"
    assert "load_lap_context" in _imports_from(ab, "lap_form")
    inf = _BACKEND / "scripts" / "inference_v29.py"
    assert "attach_lap_form" in _imports_from(inf, "train_jra_iswin_head")


# ---------------------------------------------------------------------------
# 3. 配信の「区分だけ読む」= 学習の「全期間を読む」
# ---------------------------------------------------------------------------


def _laps(distance: int, base: float) -> str:
    n = math.ceil(distance / 200)
    first = 6.0 if distance % 200 else 12.0
    segs = [first] + [base + 0.1 * (i % 3) for i in range(n - 1)]
    return "".join(f"{round(x * 10):03d}" for x in segs)


def _synthetic() -> tuple[list[tuple], list[tuple]]:
    """3区分 × 多数のレースと、3頭の過去走。区分によっては par が出ない（件数不足）。"""
    rng = np.random.default_rng(7)
    keys = [("05", "芝", 1600), ("05", "ダ", 1400), ("06", "芝", 1700), ("09", "ダ", 1800)]
    races, runs = [], []
    rid = 1
    for day in range(1, 200):
        date = f"2025{(day // 28) + 1:02d}{(day % 28) + 1:02d}"
        for course, surface, dist in keys:
            if (day + dist) % 3:
                continue
            cond = ["良", "稍", "重", None][day % 4]
            base = 11.5 + rng.normal(0, 0.2)
            races.append((rid, date, course, surface, dist, cond, _laps(dist, base)))
            for hid in (101, 102, 103):
                if (rid + hid) % 7 == 0:
                    total = sum(int(_laps(dist, base)[i : i + 3]) for i in range(0, len(_laps(dist, base)), 3)) / 10
                    runs.append((hid, rid, total + rng.uniform(0, 2), 34.0 + rng.uniform(0, 3)))
            rid += 1
    return races, runs


def test_serving_subset_matches_full_period() -> None:
    races, runs = _synthetic()
    target = "20250520"
    hids = [101, 102, 103]

    # 学習側: 全期間（対象日以降も含む）を読む
    par_all, store_all, _ = build_lap_context(races, runs)
    full = lap_features_for_horses(hids, target, store=store_all, par=par_all)

    # 配信側: `build_lap_form_features_for_race` の2本のクエリを再現する
    race_by_id = {r[0]: r for r in races}
    s_runs = [x for x in runs if race_by_id[x[1]][1] < target and x[0] in hids]
    keys = {(race_by_id[x[1]][2], race_by_id[x[1]][3], race_by_id[x[1]][4]) for x in s_runs}
    s_races = [r for r in races if (r[2], r[3], r[4]) in keys and r[1] < target]
    par_s, store_s, _ = build_lap_context(s_races, s_runs)
    serve = lap_features_for_horses(hids, target, store=store_s, par=par_s)

    assert any(v is not None for f in full.values() for v in f.values()), "合成データで特徴が出ていない"
    for h in hids:
        for name in LAP_FORM_FEATURE_NAMES:
            a, b = full[h][name], serve[h][name]
            assert (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-12), (
                h,
                name,
                a,
                b,
            )
