"""予測オッズモデルの**存在**監視（2026-08-26 新設）。

`check_model_freshness.py` は週次再学習の4モデルを mtime で見張っているが、
`odds_trio_n7.txt` / `n9.txt` / `odds_trio_meta.json` は対象外だった。
このモデルは 2026-08-21 以降、**賭け金の配分・`MIN_POINT_ODDS`・想定払戻(下限)の
足切り・表示**を全部決めている。消えても入稿は成功し続け、配分だけが黙って
朝の板／p3 単独へ落ちる（実測で板の配分は明確に悪い: 重みのL1 中央 0.27〜0.41 ↔
予測 0.18〜0.20）。

🔴 **古さ（mtime）では見張らない。** 再学習の自動化が無いので毎日鳴り続け、
   鳴りっぱなしの監視は無いのと同じになる。しかも鮮度に精度上の価値が無いことは
   実測済み（学習終端を 8か月動かしても logMAE 0.1400→0.1397・偏りも不変。
   `docs/oddspred_gap_2026_08_26.md` §5）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_model_freshness.py"


def _mod():
    spec = importlib.util.spec_from_file_location("check_model_freshness", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_odds_model_files_are_watched():
    m = _mod()
    assert set(m.ODDS_MODEL_FILES) == {
        "odds_trio_n7.txt", "odds_trio_n9.txt", "odds_trio_meta.json"}


def test_missing_files_are_reported(tmp_path):
    m = _mod()
    assert m.check_missing(tmp_path, m.ODDS_MODEL_FILES) == list(m.ODDS_MODEL_FILES)
    (tmp_path / "odds_trio_n7.txt").write_text("x", encoding="utf-8")
    assert m.check_missing(tmp_path, m.ODDS_MODEL_FILES) == [
        "odds_trio_n9.txt", "odds_trio_meta.json"]


def test_present_files_are_quiet(tmp_path):
    """存在していれば何も言わない（古くても鳴らさない）。"""
    m = _mod()
    for name in m.ODDS_MODEL_FILES:
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert m.check_missing(tmp_path, m.ODDS_MODEL_FILES) == []


def test_odds_models_are_not_in_the_weekly_staleness_list():
    """🔴 週次の mtime 監視へ混ぜない（毎日鳴って監視が死ぬ）。"""
    m = _mod()
    assert not set(m.TARGET_FILES) & set(m.ODDS_MODEL_FILES)
