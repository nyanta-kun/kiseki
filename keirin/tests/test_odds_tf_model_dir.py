"""三連単の予測オッズモデルの置き場所（2026-08-27 新設）。

🔴 **過去を評価するには「もっと古い学習終端で学習したモデル」が要る。**
   本番モデルの学習終端は 2025-12-31 で、それ以前を評価すると in-sample になる
   （三連複側には `assert_model_is_honest` があり、実際に 2026-08-21 に踏んでいる）。

🔴 出力先が固定だと、古い終端で学習し直した瞬間に**本番の配布物を上書きする**。
   `sync_models_to_vps.sh` が VPS へ配るので、**本番の入稿が古いオッズで動く**。
   三連複側には `KEIRIN_ODDS_MODEL_DIR` があるのに三連単側だけ無かった。
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path


def _reload(monkeypatch, value: str | None):
    if value is None:
        monkeypatch.delenv("KEIRIN_ODDS_TF_MODEL_DIR", raising=False)
    else:
        monkeypatch.setenv("KEIRIN_ODDS_TF_MODEL_DIR", value)
    import src.odds_prediction_tf as m
    return importlib.reload(m)


def test_model_dir_follows_the_env_override(monkeypatch, tmp_path):
    """vintage ディレクトリを指せること（本番を上書きしないため）。"""
    m = _reload(monkeypatch, str(tmp_path))
    try:
        assert m.MODEL_DIR == Path(str(tmp_path))
        assert m.META_PATH == Path(str(tmp_path)) / "odds_tf_meta.json"
    finally:
        _reload(monkeypatch, None)


def test_model_dir_defaults_to_the_shipped_location(monkeypatch):
    """環境変数が無ければ従来どおり `data/models`。既定を変えていないこと。"""
    m = _reload(monkeypatch, None)
    assert m.MODEL_DIR.name == "models"
    assert m.MODEL_DIR.parent.name == "data"


def test_training_script_writes_where_inference_reads(monkeypatch, tmp_path):
    """🔴 学習の出力先と推論の読み先が**同じ変数**であること。

    別々に持つと、環境変数で推論だけ切り替えたつもりで学習は本番へ書く、
    という最悪の組み合わせが起きる。学習スクリプトが `MODEL_DIR` を
    自分で定義し直していないことを構文で確かめる。
    """
    src = Path(__file__).resolve().parent.parent / "scripts" / "train_odds_prediction_tf.py"
    text = src.read_text()
    assert "MODEL_DIR" in text
    # import しているだけで、自分で代入していないこと
    assert "MODEL_DIR =" not in text, "学習スクリプトが MODEL_DIR を再定義している"
    assert "from src.odds_prediction_tf import" in text
