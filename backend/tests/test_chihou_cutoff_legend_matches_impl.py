"""足切りルールが単一真実源に集約され、凡例テキストと一致していることを固定する。

🔴 **文言と実装の食い違いは、どちらも動くので気づけない。**
足切り閾値は 2026-08 に 20/15/5位 → 30/24/7位 へ再較正されたが、
`ChihouRaceDetailClient.tsx` の凡例は**旧値のまま 1年近く残っていた**
（PR #407 / #408 と同じ型の乖離）。

2026-09-06 まで、同じ数字が4箇所にあった:
  - frontend の定数 CUT_GAP_HARD / CUT_GAP_SOFT / CUT_RANK_MIN（表示の実体）
  - frontend の凡例テキスト（ユーザーが読む説明）
  - `scripts/chihou_cutoff_venue_review.py` の同名定数（検証）
  - `scripts/chihou_cutoff_review.py` の自前 apply_rule（スイープ）

正本を `src/indices/chihou_cutoff.py` に置き、frontend は API の `is_cut_off` を
受け取るだけにした（JRA の `out_probability` / `is_cut_off` と同じ形）。
このテストは **正本が1つであること**と**凡例が正本と一致していること**を見る。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.indices.chihou_cutoff import CUT_GAP_HARD, CUT_GAP_SOFT, CUT_RANK_MIN, cut_flags

_ROOT = Path(__file__).resolve().parents[1]
_TSX = _ROOT.parent / "frontend" / "src" / "components" / "ChihouRaceDetailClient.tsx"
_ROUTER = _ROOT / "src" / "api" / "chihou_races_router.py"


def test_legend_text_matches_backend_constants() -> None:
    assert _TSX.exists(), f"{_TSX} が見つからない（移動したらこのテストも直すこと）"
    tsx = _TSX.read_text(encoding="utf-8")

    m = re.search(r"足切り候補（トップ差(\d+)以上、または差(\d+)以上かつ(\d+)位以下）", tsx)
    assert m, "足切りの凡例テキストが見つからない（文面を変えたらこのテストも直すこと）"
    legend = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    impl = (int(CUT_GAP_HARD), int(CUT_GAP_SOFT), int(CUT_RANK_MIN))

    assert legend == impl, (
        f"凡例テキスト {legend} が正本の閾値 {impl} と食い違っている。"
        "画面の説明とグレーアウトの挙動がずれる"
    )


def test_frontend_does_not_reimplement_the_rule() -> None:
    """frontend が自前で閾値を持っていないこと（正本は backend 単一）。"""
    tsx = _TSX.read_text(encoding="utf-8")
    for name in ("CUT_GAP_HARD", "CUT_GAP_SOFT", "CUT_RANK_MIN"):
        assert f"const {name}" not in tsx, (
            f"{name} が frontend に復活している。足切りの正本は "
            "backend/src/indices/chihou_cutoff.py で、frontend は API の "
            "is_cut_off を受け取るだけにすること"
        )
    assert "is_cut_off" in tsx, "frontend が API の is_cut_off を使っていない"


def test_api_returns_is_cut_off() -> None:
    """API が足切りフラグを返していること（返さないと画面が全馬白くなる）。"""
    router = _ROUTER.read_text(encoding="utf-8")
    assert "is_cut_off: bool" in router, "ChihouHorseIndexOut に is_cut_off が無い"
    assert "cut_flags(" in router, "router が正本 cut_flags を呼んでいない"


def test_rule_shape() -> None:
    """gap>=hard 単独、gap>=soft かつ 順位>=rank_min、の2経路が効くこと。"""
    # 最高 100 との差が 30 / 25 / 5 の3頭 + 順位を押し下げる埋め草
    comp = [100.0, 95.0, 90.0, 85.0, 80.0, 76.0, 75.0, 70.0]
    flags = cut_flags(comp)
    #   gap: 0, 5, 10, 15, 20, 24, 25, 30 / rank: 1..8
    assert flags[-1] is True, "gap=30 は hard 経路で足切り"
    assert flags[6] is True, "gap=25 かつ 7位 は soft 経路で足切り"
    assert flags[5] is False, "gap=24 でも 6位 なら順位条件を満たさない"
    assert flags[:5] == [False] * 5, "gap<24 は足切りしない"


def test_none_index_is_never_cut() -> None:
    assert cut_flags([None, None]) == [False, False]
    assert cut_flags([50.0, None, 10.0]) == [False, False, True]
