"""三連単オッズモデルのメタは**車数ごとにマージ**すること（2026-08-27 新設）。

🔴 学習スクリプトは以前 `odds_tf_meta.json` を**丸ごと上書き**していた。
   9車を学習した瞬間に `target_sum["7"]` が消え、本番の `target_sum(7)` が
   `OddsPredictionUnavailable` を投げて**三連単の予測オッズが全滅**する
   （入稿の配分・型ラボ live の両方）。例外は出るが、出るのは翌朝の入稿時。

   同じ型の事故を `KEIRIN_ODDS_TF_MODEL_DIR` でも踏みかけている
   （出力先固定で本番モデルを上書き）。**車数やバージョンを増やす変更では
   「既存を消していないか」を必ずテストで固定すること。**
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "train_odds_prediction_tf.py"


def test_meta_write_reads_the_existing_file_first():
    """書く前に既存メタを読んでいること（マージの前提）。"""
    src = SCRIPT.read_text()
    assert "META_PATH.exists()" in src
    assert "META_PATH.read_text" in src
    i_read = src.index("META_PATH.read_text")
    i_write = src.index("META_PATH.write_text")
    assert i_read < i_write, "既存メタを読む前に書いている"


def test_target_sum_is_merged_not_replaced():
    """`target_sum` が既存の辞書から作られていること。

    `{str(N_CAR): target_sum}` のような**新規辞書の直接代入**に戻したら落ちる。
    """
    src = SCRIPT.read_text()
    assert 'tgt = dict(prev.get("target_sum") or {})' in src
    assert 'tgt[str(N_CAR)] = target_sum' in src
    assert '"target_sum": {str(N_CAR): target_sum}' not in src, "上書き実装に戻っている"


def test_top_level_train_end_is_the_newest():
    """🔴 最上位の `train_end` は**最も新しい**終端。

    古い方を残すと honest 判定が甘くなり、まだ in-sample な期間を通してしまう。
    """
    src = SCRIPT.read_text()
    assert "max(ends)" in src
    assert "min(ends)" not in src


def test_inference_supports_the_car_counts_we_ship_models_for():
    from src.odds_prediction_tf import SUPPORTED_N_CAR

    assert 7 in SUPPORTED_N_CAR and 9 in SUPPORTED_N_CAR


def test_model_train_end_defaults_to_the_newest():
    """車数を指定しないときは新しい方（＝厳しい方）を返すこと。"""
    from src import odds_prediction_tf as m

    src = inspect.getsource(m.model_train_end)
    tree = ast.parse(src.lstrip())
    names = {getattr(n.func, "id", None) for n in ast.walk(tree)
             if isinstance(n, ast.Call)}
    assert "max" in names and "min" not in names
