"""WIN5 バックフィルの**取りこぼし防止**を固定する検査。

## なぜこの検査が要るか

WIN5 取込には「エラーにならず何も取れない」経路が3つある。いずれも
0B11（速報馬体重）が実際に踏んだ型で、ログにも DB にも異常が出ない。

1. **completed ファイルの共有** — `jvlink_historical.py` は
   `COMPLETED_KEY_RACE = "RACE"` を `jvlink_agent.py` と共有している。
   処理済みの過去ファイルは JVSkip され中身が読まれないので、
   レコードフィルタに `"WF"` を足しただけでは**過去分が1件も取れない**。
   `win5_backfill.py` は独立した `WIN5_completed.txt` を持たなければならない
2. **パーサ不在の握り潰し** — `except ImportError:` を warning にして続行すると、
   WF を全件捨てて正常終了する
3. **接頭辞の推測** — WF がどのファイル名で届くかは未確認。推測でスキップ規則を
   書くと、外れていれば全件取りこぼす

このテストは Windows 実機を必要としない（COM を触らない部分だけを見る）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest


def _code_only(path: Path) -> str:
    """docstring とコメントを除いた「実際に動くコード」だけを返す。

    説明文の中で `RACE_completed` や `startswith("H")` に言及するのは正しい
    （なぜそれをしないかを書くため）。検査対象は実行される行に限る。
    2026-09-02 に、この区別をせず自分の説明文を検出して落ちた。
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    drop: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) in docstrings and node.end_lineno):
            drop.append((node.lineno, node.end_lineno))
    lines = src.splitlines()
    keep = []
    for i, line in enumerate(lines, start=1):
        if any(a <= i <= b for a, b in drop):
            continue
        if line.lstrip().startswith("#"):
            continue
        keep.append(line)
    return "\n".join(keep)

_AGENT_DIR = Path(__file__).resolve().parents[1]
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

_BACKFILL = _AGENT_DIR / "win5_backfill.py"
_HISTORICAL = _AGENT_DIR / "jvlink_historical.py"


def test_backfill_script_exists() -> None:
    assert _BACKFILL.exists(), (
        "win5_backfill.py が無い。jvlink_historical.py のフィルタに WF を足すだけでは"
        "過去分が1件も取れない（RACE の completed が共有されており JVSkip される）"
    )


def test_backfill_uses_independent_completed_file() -> None:
    """🔴 RACE_completed.txt を使い回してはいけない。"""
    src = _code_only(_BACKFILL)
    assert "WIN5_completed.txt" in src, "独立した completed ファイルを持つこと"
    assert "RACE_completed" not in src, (
        "RACE_completed.txt を参照している。共有すると処理済みファイルが JVSkip され、"
        "過去分の WF が1件も読まれない"
    )


def test_backfill_does_not_guess_file_prefix() -> None:
    """WF の接頭辞は未確認。既定でハードコードした絞りを入れないこと。"""
    src = _code_only(_BACKFILL)
    hardcoded = re.findall(r'startswith\(\s*["\'][A-Z]["\']\s*\)', src)
    assert not hardcoded, (
        f"ファイル名接頭辞を推測でハードコードしている: {hardcoded}。"
        "WF がどのファイルに入るかは未確認なので、--discover で実測してから "
        "--only-prefix で指定する設計にすること"
    )
    assert "--discover" in src, "接頭辞を実測するための調査モードを持つこと"


def test_backfill_does_not_parse_locally() -> None:
    """🔴 エージェント側でパースしないこと（実機のパーサに依存しない）。

    windows-agent/jvlink_parser.py は git 管理外で 2026-05-04 付と4か月古く、
    更新手段も無い。さらに main のパーサは相対 import を持つため実機へ
    そのまま置けず、置くと既存の HR 払戻経路まで壊れる（2026-09-02 実機確認）。
    """
    src = _code_only(_BACKFILL)
    assert "from jvlink_parser import" not in src, (
        "実機のパーサを import している。生レコードを POST してサーバ側で"
        "パースする形（/api/import/weights と同じ）にすること"
    )
    assert "parse_wf(" not in src, "エージェント側でパースしている"


def test_backfill_reads_unresolved_races_from_response() -> None:
    """🔴 200 が返ったことを成功と見なさないこと。"""
    src = _BACKFILL.read_text(encoding="utf-8")
    assert "unresolved_races" in src, (
        "レスポンスの unresolved_races を見ていない。races に1件も一致しなくても"
        "200 は返るので、件数を読まないと取り込めたことにならない"
    )
    assert 'total["events"] == 0' in src, "1件も取り込めなかった場合に ERROR を出すこと"


def test_historical_filter_includes_wf() -> None:
    """日次経路でも WF を捨てないこと（これから届く分の受け皿）。"""
    src = _HISTORICAL.read_text(encoding="utf-8")
    m = re.search(r'rec_id"\)\s+in\s+\(([^)]*)\)', src)
    assert m, "レコードフィルタが見つからない"
    assert '"WF"' in m.group(1), (
        f"フィルタに WF が入っていない: {m.group(1)}。"
        "0B11 と同じく、ここで落ちると無言で全件破棄される"
    )


def test_historical_wf_post_sends_raw() -> None:
    """日次経路も生レコードを送ること。"""
    src = _HISTORICAL.read_text(encoding="utf-8")
    body = src[src.index("def post_wf_win5"):src.index("# JVOpen + JVRead ループ")]
    assert "parse_wf" not in body, (
        "日次経路がエージェント側でパースしている。実機のパーサ更新が必要になる"
    )
    assert '"rec_id"' in body and '"data"' in body, "生レコードの形で送ること"


@pytest.mark.parametrize("flag", ["--from-year", "--option", "--discover", "--only-prefix"])
def test_backfill_cli_flags(flag: str) -> None:
    assert flag in _BACKFILL.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 設定の読み方（2026-09-03 に実機で踏んだ）
#
# payout_backfill.py を写したとき、環境変数の読み方ごと写してしまい、
# 実機で BACKEND_URL=http://192.168.11.26:8000 / API_KEY=空 になっていた。
# そのまま流していたら POST が全て失敗し、1件も取り込めなかった。
# ---------------------------------------------------------------------------


def test_reads_env_from_project_root_only() -> None:
    """🔴 windows-agent/.env を読まないこと。

    実機の windows-agent/.env は BACKEND_URL がローカルIP、
    CHANGE_NOTIFY_API_KEY が空。load_dotenv は override=False なので、
    先にこちらを読むと**古い値が勝つ**。稼働中の jvlink_agent.py は
    親ディレクトリの .env だけを読んでおり、それに揃える。
    """
    src = _code_only(_BACKFILL)
    assert "load_dotenv(BASE_DIR.parent" in src, "プロジェクトルートの .env を読むこと"
    assert "load_dotenv(BASE_DIR /" not in src, (
        "windows-agent/.env を読んでいる。ローカルIPの BACKEND_URL が勝ってしまう"
    )


def test_uses_correct_api_key_name() -> None:
    """🔴 AGENT_API_KEY は .env に存在しない。CHANGE_NOTIFY_API_KEY が正。"""
    src = _code_only(_BACKFILL)
    assert "CHANGE_NOTIFY_API_KEY" in src
    assert "AGENT_API_KEY" not in src, (
        "AGENT_API_KEY を見ている。実機の .env に無いので常に空になり POST が 401 になる"
    )


def test_supports_dual_sid() -> None:
    """蓄積系は JRAVAN_SID_2 を使う（realtime を止めずに同時実行するため）。"""
    src = _code_only(_BACKFILL)
    assert "JRAVAN_SID_2" in src, (
        "デュアルSID に対応していない。JRAVAN_SID_2 を使えば realtime（SID1）と"
        "同時実行できる（jvlink_agent.py:70-73 / jvlink_historical.py:189）"
    )
    assert 'JVInit("UNKNOWN")' not in src, (
        "JVInit に UNKNOWN を渡している。実際の利用キーを渡すこと"
    )


def test_fails_fast_when_api_key_missing() -> None:
    """APIキーが空なら流す前に止まること（全 POST が 401 になるため）。"""
    src = _code_only(_BACKFILL)
    assert "if not API_KEY" in src, "APIキー未設定を起動時に検出すること"
