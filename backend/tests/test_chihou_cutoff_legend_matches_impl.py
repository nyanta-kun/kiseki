"""足切りの凡例テキストが実装の閾値と一致していることを固定する。

🔴 **文言と実装の食い違いは、どちらも動くので気づけない。**
足切り閾値は 2026-08 に 20/15/5位 → 30/24/7位 へ再較正されたが、
`ChihouRaceDetailClient.tsx` の凡例は**旧値のまま 1年近く残っていた**
（PR #407 / #408 と同じ型の乖離）。

同じ数字が3箇所にある:
  - frontend の定数 CUT_GAP_HARD / CUT_GAP_SOFT / CUT_RANK_MIN（実装の正本）
  - frontend の凡例テキスト（ユーザーが読む説明）
  - backend の chihou_cutoff_venue_review.py の同名定数（検証スクリプト）

このテストは3つが揃っていることを見る。**正本が backend に無いのは既知の
弱点**（JRA は out_probability が backend 単一真実源）。揃えるだけでも
ずれには気づける。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TSX = _ROOT.parent / "frontend" / "src" / "components" / "ChihouRaceDetailClient.tsx"
_REVIEW = _ROOT / "scripts" / "chihou_cutoff_venue_review.py"


def _num(text: str, pattern: str) -> int:
    m = re.search(pattern, text)
    assert m, f"{pattern} が見つからない"
    return int(float(m.group(1)))


def test_legend_text_matches_frontend_constants() -> None:
    assert _TSX.exists(), f"{_TSX} が見つからない（移動したらこのテストも直すこと）"
    tsx = _TSX.read_text(encoding="utf-8")

    hard = _num(tsx, r"const CUT_GAP_HARD\s*=\s*([\d.]+)")
    soft = _num(tsx, r"const CUT_GAP_SOFT\s*=\s*([\d.]+)")
    rank = _num(tsx, r"const CUT_RANK_MIN\s*=\s*([\d.]+)")

    m = re.search(r"足切り候補（トップ差(\d+)以上、または差(\d+)以上かつ(\d+)位以下）", tsx)
    assert m, "足切りの凡例テキストが見つからない（文面を変えたらこのテストも直すこと）"
    legend = (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    assert legend == (hard, soft, rank), (
        f"凡例テキスト {legend} が実装の閾値 ({hard}, {soft}, {rank}) と食い違っている。"
        "画面の説明とグレーアウトの挙動がずれる"
    )


def test_backend_review_constants_match_frontend() -> None:
    tsx = _TSX.read_text(encoding="utf-8")
    py = _REVIEW.read_text(encoding="utf-8")

    front = (
        _num(tsx, r"const CUT_GAP_HARD\s*=\s*([\d.]+)"),
        _num(tsx, r"const CUT_GAP_SOFT\s*=\s*([\d.]+)"),
        _num(tsx, r"const CUT_RANK_MIN\s*=\s*([\d.]+)"),
    )
    back = (
        _num(py, r"CUT_GAP_HARD\s*=\s*([\d.]+)"),
        _num(py, r"CUT_GAP_SOFT\s*=\s*([\d.]+)"),
        _num(py, r"CUT_RANK_MIN\s*=\s*([\d.]+)"),
    )
    assert front == back, (
        f"frontend {front} と backend の検証スクリプト {back} で足切り閾値が違う。"
        "検証が本番と別のルールを測ることになる"
    )
