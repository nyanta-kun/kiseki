"""Discord の通知チャンネルキーが実在することを機械的に固定する（2026-08-29 新設）。

🔴 **無効なキーは実行時まで分からない。** `send()` は未知のチャンネルで
   `ValueError` を投げるので、通知の1行が入稿バッチ全体を落とす。
   2026-08-28〜29 の型ラボ入稿がまさにこれで、`channel="keirin"`（存在しない）
   のせいで **入稿は成功しているのに `type_lab_daily.sh` が「入稿に失敗」と
   記録し、Discord には1通も出ていなかった**。

   通知の呼び出しは点在していて実行経路も日に数回なので、
   **キーの正しさは静的に検査する**しかない。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from src.notify.discord import _WEBHOOK_ENV_KEYS

REPO = Path(__file__).resolve().parents[1]
#: 検査対象。`src/` と `scripts/` の実コードだけを見る（テストと実験は除く）。
TARGET_DIRS = ("src", "scripts")
SKIP_PARTS = ("__pycache__", "exp_", ".venv")


def _python_files() -> list[Path]:
    out: list[Path] = []
    for d in TARGET_DIRS:
        for p in (REPO / d).rglob("*.py"):
            if any(s in str(p) for s in SKIP_PARTS):
                continue
            out.append(p)
    return sorted(out)


def _channel_literals(path: Path) -> list[tuple[int, str]]:
    """`channel="..."` の**リテラル**だけを集める（変数渡しは対象外）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:                     # pragma: no cover
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "channel" and isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, str):
                found.append((node.lineno, kw.value.value))
    return found


def test_全ての通知チャンネルキーが実在する():
    bad: list[str] = []
    seen = 0
    for path in _python_files():
        for lineno, ch in _channel_literals(path):
            seen += 1
            if ch not in _WEBHOOK_ENV_KEYS:
                bad.append(f"{path.relative_to(REPO)}:{lineno} channel={ch!r}")
    assert seen > 0, "channel= のリテラルを1つも見つけられていない（検査が空振り）"
    assert not bad, ("存在しない Discord チャンネルキーが使われています"
                     f"（有効: {sorted(_WEBHOOK_ENV_KEYS)}）:\n  " + "\n  ".join(bad))


def test_型ラボの入稿通知は入稿完了チャンネルへ出す():
    """🔴 既存ランクの入稿通知と同じ `netkeirin` に出す（人が見る場所を分けない）。"""
    src = (REPO / "scripts" / "netkeirin_submit_type_lab.py").read_text(encoding="utf-8")
    assert re.search(r'channel="netkeirin"', src), "型ラボの入稿通知の宛先が変わっています"


def test_通知の失敗で入稿を落とさない():
    """入稿が終わったあとの通知なので、例外を上げると再実行を誘発する。"""
    src = (REPO / "scripts" / "netkeirin_submit_type_lab.py").read_text(encoding="utf-8")
    i = src.index('channel="netkeirin"')
    block = src[max(0, i - 600):i + 400]
    assert "try:" in block and "except" in block, \
        "型ラボの Discord 通知が try/except で守られていません"


def test_チャンネルキーの一覧が想定どおり():
    """🔴 増減したら通知の宛先が変わっている。気づかず変える場所ではない。

    ⚠️ `_load_webhook_url` 自体の例外は**ここでは検査できない**。
       `tests/conftest.py::_block_discord` が実 webhook を叩かないよう
       repo 全体で monkeypatch しているため（本番では ValueError になる。
       2026-08-29 の型ラボ入稿がその実例）。
    """
    assert set(_WEBHOOK_ENV_KEYS) == {"picks", "prerace", "results", "netkeirin", "system"}
    assert "keirin" not in _WEBHOOK_ENV_KEYS, \
        "紛らわしいキーを増やさないこと（型ラボが 'keirin' で落ちた）"
