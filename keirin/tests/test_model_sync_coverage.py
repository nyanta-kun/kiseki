"""日次パイプラインが読むモデルが VPS 配布リストから漏れないことの回帰テスト（2026-08-08）。

## 背景（実際に起きた抜け）

`data/models/*.pkl` は `.gitignore` 対象で、**git 経由では VPS に絶対に届かない**。
唯一の配布経路が `scripts/sync_models_to_vps.sh` の固定リストなので、
新しいモデルの「種類」を足したときにここへの追加が漏れると、
コードだけが本番へ入りモデルが無い状態になる。

同型の抜けを繰り返し踏んでいる:

- 2026-08-05 `lgbm_wt_bad` の月次vintage を新設 → glob 追加漏れ → VPS へ1本も配布されず、
  翌日の tail 再構築が全ランク中断
- 2026-08-08 `lgbm_upset_screen`（RANK_9H1 の波乱スコア）を新設 → PROD_FILES 追加漏れ

後者がとりわけ危険なのは、`daily_picks_wt.sh` が
`|| echo "9H1候補生成に失敗（他ランクには影響しないため継続）"` で握り潰すため、
**ログ1行だけ残して 9H1 が永久に0件になる**こと。落ちてくれない。

## 何を守るか

`daily_picks_wt.sh` / `evening_picks_wt.sh` が起動するスクリプトが
**既定値として読むモデル名**は、必ず `sync_models_to_vps.sh` の
転送対象（固定リスト or vintage glob）に載っていること。

コメントや docstring 中の例示を拾わないよう、正規表現ではなく AST で
`load_model("...")` と `add_argument("--...model...", default="...")` を集める。
逆向き（配布されているのに誰も読まない）は、退役直後などに起こりうるので許容する。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = ROOT / "scripts" / "sync_models_to_vps.sh"
PIPELINES = ("daily_picks_wt.sh", "evening_picks_wt.sh")

_SCRIPT_RE = re.compile(r"scripts/([a-z0-9_]+\.py)")
_ARRAY_RE = re.compile(r"^(PROD_FILES|EXTRA_FILES)=\((.*?)\)", re.MULTILINE | re.DOTALL)
_VINTAGE_GLOB_RE = re.compile(r'"\$MODEL_DIR"/([a-z0-9_]+)_m\[0-9\]\[0-9\]\[0-9\]\[0-9\]\.pkl')
_VINTAGE_NAME_RE = re.compile(r"^(?P<base>[a-z0-9_]+)_m\d{4}$")


def _pipeline_scripts() -> set[Path]:
    """朝夕パイプラインが起動する python スクリプト。"""
    found: set[Path] = set()
    for sh in PIPELINES:
        text = (ROOT / "scripts" / sh).read_text(encoding="utf-8")
        for name in _SCRIPT_RE.findall(text):
            path = ROOT / "scripts" / name
            if path.exists():
                found.add(path)
    return found


def _model_names_read_by(path: Path) -> set[str]:
    """スクリプトが既定で読むモデル名を AST から集める。"""
    names: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fn_name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")

        if fn_name == "load_model" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)

        if fn_name == "add_argument":
            flag = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
            if not (isinstance(flag, str) and "model" in flag):
                continue
            for kw in node.keywords:
                if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        names.add(kw.value.value)

    return {n for n in names if n.startswith("lgbm")}


def _synced_names() -> tuple[set[str], set[str]]:
    """(固定リストのモデル名, vintage glob の基底名)。"""
    text = SYNC_SCRIPT.read_text(encoding="utf-8")

    fixed: set[str] = set()
    for _, body in _ARRAY_RE.findall(text):
        for token in re.findall(r'"([^"]+)"', body):
            if token.endswith(".pkl"):
                fixed.add(token[: -len(".pkl")])

    vintage = set(_VINTAGE_GLOB_RE.findall(text))
    return fixed, vintage


def test_pipeline_models_are_distributed_to_vps():
    """朝夕パイプラインが読むモデルは全て配布対象に載っていること。"""
    fixed, vintage = _synced_names()
    assert fixed, "sync_models_to_vps.sh から固定リストを1件も読めていない（パース失敗）"

    missing: dict[str, set[str]] = {}
    for script in sorted(_pipeline_scripts()):
        for name in sorted(_model_names_read_by(script)):
            m = _VINTAGE_NAME_RE.match(name)
            covered = name in fixed or (m is not None and m.group("base") in vintage)
            if not covered:
                missing.setdefault(script.name, set()).add(name)

    assert not missing, (
        "以下のモデルが sync_models_to_vps.sh の転送対象に載っていない。\n"
        "data/models/*.pkl は .gitignore 対象で git では届かないため、\n"
        "このままマージすると本番でモデル無しになる:\n"
        + "\n".join(f"  {s}: {sorted(n)}" for s, n in sorted(missing.items()))
    )


def test_upset_screen_model_is_listed():
    """RANK_9H1 の波乱スコアモデルが固定リストにあること（実際に踏んだ抜け）。"""
    fixed, _ = _synced_names()
    assert "lgbm_upset_screen" in fixed


def test_guard_detects_a_removed_entry():
    """固定リストから外すと検出できること（検査自体が空振りしていないかの確認）。"""
    fixed, vintage = _synced_names()
    fixed.discard("lgbm_upset_screen")

    names: set[str] = set()
    for script in _pipeline_scripts():
        names |= _model_names_read_by(script)

    uncovered = {
        n
        for n in names
        if n not in fixed
        and not ((m := _VINTAGE_NAME_RE.match(n)) and m.group("base") in vintage)
    }
    assert "lgbm_upset_screen" in uncovered


# ---------------------------------------------------------------------------
# オッズ予測モデル（2026-08-11 追加）
# ---------------------------------------------------------------------------
# 上の走査は `load_model("...")` の第1引数（= data/models/{name}.pkl）を拾う設計で、
# **LightGBM テキスト形式を `lgb.Booster(model_file=...)` で読むモデルは対象外**。
# `src/odds_prediction.py` はその形なので、専用に配布リストとの対応を固定する。
#
# 配布漏れの怖さ: 無くても入稿は止まらず WARNING を出して従来配分へ落ちるだけなので、
# **黙って実質的中率が 3〜5pt 落ちた状態で回り続ける**（[[keirin_odds_prediction_model_2026_08_11]]）。
def test_odds_prediction_models_are_distributed():
    from src import odds_prediction as op

    sync = SYNC_SCRIPT.read_text(encoding="utf-8")
    expected = {f"odds_trio_n{n}.txt" for n in op.SUPPORTED_N_CAR}
    expected.add(op.META_PATH.name)
    missing = sorted(n for n in expected if f'"{n}"' not in sync)
    assert not missing, (
        f"{missing} が sync_models_to_vps.sh の転送対象にありません。"
        "配布されないと予測オッズが使われず、黙って実質的中率が落ちます"
    )


def test_odds_prediction_model_path_matches_sync_list():
    """`load_model()` が組み立てるファイル名と配布リストの名前が一致すること。

    片方だけ改名すると、コードは動くのに配布されない（or 逆）状態になる。
    """
    from src import odds_prediction as op

    for n in op.SUPPORTED_N_CAR:
        expected = op.MODEL_DIR / f"odds_trio_n{n}.txt"
        assert expected.name in SYNC_SCRIPT.read_text(encoding="utf-8")
        assert expected.parent == op.META_PATH.parent, (
            "モデルと meta が別ディレクトリを向いている"
        )
