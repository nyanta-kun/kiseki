"""三連複オッズモデルの honest ガードと、評価窓が消えないことの検査（2026-08-21 新設）。

予測オッズは 2026-08-21 から **配分・1.5倍の足切り・確認画面の表示** の全てを決める
単一の依存になった。それにもかかわらず

  1. 学習終端より前を評価しても止まらない（三連単側にはあるガードが三連複に無い）
  2. 評価窓が「学習終端の後ろ全部」で、再学習のたびに縮み、実行日を渡すと**空**になる

という2つの穴があった。どちらも**壊れても例外が出ない**型なので構造で塞ぐ。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_guard_fires_before_train_end(monkeypatch):
    """学習終端以前を対象にしたら SystemExit で止まる。"""
    from src import odds_prediction as op

    monkeypatch.setattr(op, "load_meta",
                        lambda: {"per_n_car": {"7": {"train_end": "2026-08-04"},
                                               "9": {"train_end": "2026-07-01"}}})
    assert op.model_train_end() == "2026-08-04", "車数をまたいで最も新しい終端を使う"
    with pytest.raises(SystemExit) as e:
        op.assert_model_is_honest("2026-01-01", who="test")
    assert "2026-08-04" in str(e.value), "終端の日付を出さないと直しようがない"


def test_guard_passes_after_train_end(monkeypatch):
    """学習終端より後は通る（live 予想は常にこちら）。"""
    from src import odds_prediction as op

    monkeypatch.setattr(op, "load_meta",
                        lambda: {"per_n_car": {"7": {"train_end": "2026-08-04"}}})
    assert op.assert_model_is_honest("2026-08-05") is None


def test_guard_is_silent_when_train_end_unknown(monkeypatch):
    """終端が記録されていない古い meta では止めない（新しい検査で過去を壊さない）。"""
    from src import odds_prediction as op

    monkeypatch.setattr(op, "load_meta", lambda: {"per_n_car": {}})
    assert op.model_train_end() is None
    assert op.assert_model_is_honest("2020-01-01") is None


def test_model_dir_is_overridable_by_env():
    """`KEIRIN_ODDS_MODEL_DIR` で向き先を変えられる。

    honest なモデルを使うために**本番の配布物を上書きする**のが一番危ない
    （戻し忘れると入稿の配分と足切りが静かに古いモデルになる）。
    """
    src = (ROOT / "src" / "odds_prediction.py").read_text(encoding="utf-8")
    assert 'os.environ.get("KEIRIN_ODDS_MODEL_DIR")' in src


def test_training_fails_when_eval_window_is_too_small():
    """評価窓が下限未満なら学習を失敗させる。

    🔴 以前は `if part.empty: continue` で **stats から評価窓が黙って消える**
       だけだった。監視が無くなったことに気づけない。
    """
    src = (ROOT / "scripts" / "train_odds_prediction.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.targets[0].id for n in ast.walk(tree)
             if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
    assert "MIN_EVAL_RACES" in names, "評価窓の下限が定数として無い"
    assert "n_eval_races < args.min_eval_races" in src, "下限で失敗させていない"
    assert "--min-eval-races" in src, "下限を明示的に下げる逃げ道が無い"


def test_training_records_the_eval_window_range():
    """評価窓の範囲を meta に残す（次の再学習でどれだけ縮んだかを追えるように）。"""
    src = (ROOT / "scripts" / "train_odds_prediction.py").read_text(encoding="utf-8")
    assert '"from": str(part.date.min())' in src
    assert '"n_races": int(part.rk.nunique())' in src


def test_gami_cut_experiment_calls_the_guard():
    """過去窓を評価する実験スクリプトがガードを通っていること。"""
    src = (ROOT / "scripts" / "exp_gami_cut_by_predicted_odds.py").read_text(encoding="utf-8")
    assert "assert_model_is_honest" in src
    assert "--production-odds-model" in src, "承知の上で使う逃げ道は残す"
