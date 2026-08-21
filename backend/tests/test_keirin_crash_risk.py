"""レース信頼度指標（落車リスク）の不変条件（2026-08-21 新設）。

🔴 **これはゲートではなく表示**。実測（7C ゲート通過 6,425R の四分位）で
   軸落車率は 1.56%→3.11% と2倍になる一方、**二軸的中はほぼ動かず ROI は
   危険側が高い**（Q4 78.1% vs Q1 71.6%）。自動で落とすと回収率の最も高い
   四分位を捨てることになる。
"""
from __future__ import annotations

import pytest

from src.services.keirin_crash_risk import (
    BASE_RATE,
    SHRINK_K,
    race_risk,
    rider_risk,
    risk_band,
)


def test_no_history_falls_back_to_the_base_rate():
    """履歴の無い選手は全体平均。0除算も極端値も作らない。"""
    assert rider_risk(0, 0) == pytest.approx(BASE_RATE)


def test_shrinkage_pulls_small_samples_toward_the_base():
    """🔴 縮約が無いと『1走1落車＝落車率100%』が生まれる。

    稀事象（基準率 1.19%）なので、数走の選手をそのまま使ってはいけない。
    """
    raw_would_be = 1.0
    got = rider_risk(1, 1)
    assert got < 0.02, f"縮約が効いていない（生値なら {raw_would_be}）"
    assert got > BASE_RATE, "落車した事実が全く反映されていない"


def test_large_samples_approach_the_observed_rate():
    """走数が十分なら実績へ寄る（縮約が強すぎて差が潰れない）。"""
    # 2000走100落車（実績5.0%）→ 縮約後 4.56%。実績へ十分寄る
    assert rider_risk(2000, 100) == pytest.approx(0.0456, abs=0.002)
    assert 0.045 < rider_risk(2000, 100) < 0.050, "縮約が強すぎて差が潰れている"
    # 2000走0落車 → ほぼ 0 側へ寄る（全体平均 1.19% には留まらない）
    assert rider_risk(2000, 0) < 0.003


def test_k_is_the_halfway_point():
    """K は『走数が K のとき全体平均と半々』という意味であること。"""
    n = int(SHRINK_K)
    # n 走で落車率 r の選手 → (r*n + K*base) / (n+K) = (r + base) / 2
    r = 0.05
    assert rider_risk(n, int(r * n)) == pytest.approx((r + BASE_RATE) / 2, abs=1e-6)


def test_race_risk_is_the_mean_over_all_riders():
    """🔴 出走者**全員**の平均。軸2車だけで測ると巻き込まれを取りこぼす。

    落車の 66.7% は「2人以上出たレース」で発生している（2026-08-21 実測）。
    """
    riders = [(300, 0)] * 4 + [(300, 10)] * 3
    expected = (rider_risk(300, 0) * 4 + rider_risk(300, 10) * 3) / 7
    assert race_risk(riders) == pytest.approx(expected)


def test_race_risk_is_none_when_there_are_no_riders():
    assert race_risk([]) is None


def test_bands_are_ordered_and_handle_none():
    assert risk_band(None) == "unknown"
    assert risk_band(0.005) == "low"
    assert risk_band(0.0115) == "mid"
    assert risk_band(0.02) == "high"


def test_negative_history_is_rejected():
    """負の実績は集計バグ。黙って通すと指標が壊れたまま表示される。"""
    with pytest.raises(ValueError):
        rider_risk(-1, 0)


def test_module_imports_only_stdlib():
    """⚠️ keirin 側が自分の venv から直接読み込む可能性がある（marquee と同じ運用）。"""
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "src" / "services"
           / "keirin_crash_risk.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            assert node.module == "__future__", f"外部依存: {node.module}"
        elif isinstance(node, ast.Import):
            raise AssertionError(f"外部依存: {[a.name for a in node.names]}")


def test_review_api_exposes_it_as_display_only():
    """API が返すこと、そして**ゲートに使っていない**こと。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "src" / "api"
           / "keirin_router.py").read_text(encoding="utf-8")
    assert '"crash_risk"' in src and '"crash_risk_band"' in src
    # 🔴 除外・スキップの条件に crash_risk を混ぜていないこと
    for line in src.splitlines():
        if "crash_risk" in line and ("continue" in line or "skip" in line):
            raise AssertionError(f"ゲートに使っている: {line.strip()}")
