"""`AGENTS.md` が `CLAUDE.md` から乖離していないことを固定する。

## なぜ必要か

`AGENTS.md` は Codex 用に置いた `CLAUDE.md` の写しで、違うのは
**「Claude Code」を「Codex」と読み替える箇所と自分自身への参照だけ**。

このリポジトリが繰り返している事故の型は
「**同じ知識が複数ファイルにコピーされ、更新が同期していない**」ことで、
`keirin/CLAUDE.md` の「変更時チェックリスト」はまさにこれを根本原因として挙げている
（サマリーのランク漏れ3回・ランク全廃時の経路漏れ2回）。

124KB の運用ルールが2つに割れると、**片方だけ古い**という状態が静かに生まれる。
例外もログも出ないので、次に Codex で作業した人が古い手順を実行してしまう。

## 何を許すか

`ALLOWED_SUBSTITUTIONS` の置換を当てたうえで**完全一致**を要求する。
新しい読み替えが要るときはここへ足す（＝意図的な差分だけが増える）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: `CLAUDE.md` 側の文字列 → `AGENTS.md` 側の文字列。
#: 🔴 内容の差分をここへ足して黙らせないこと。足してよいのは**呼び名の読み替えだけ**。
ALLOWED_SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("Claude Code", "Codex"),
    ("CLAUDE.md", "AGENTS.md"),
)


def _expected() -> str:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for src, dst in ALLOWED_SUBSTITUTIONS:
        text = text.replace(src, dst)
    return text


@pytest.mark.skipif(not (ROOT / "AGENTS.md").exists(), reason="AGENTS.md が無い環境")
def test_agents_md_is_claude_md_with_only_known_substitutions():
    actual = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    expected = _expected()
    if actual == expected:
        return

    a, e = actual.splitlines(), expected.splitlines()
    diffs = [(i + 1, x, y) for i, (x, y) in enumerate(zip(a, e)) if x != y][:5]
    detail = "\n".join(f"  {n}行目\n    AGENTS.md: {x[:100]}\n    CLAUDE.md: {y[:100]}"
                       for n, x, y in diffs)
    if len(a) != len(e):
        detail += f"\n  行数が違う: AGENTS.md {len(a)} / CLAUDE.md（置換後） {len(e)}"
    pytest.fail(
        "AGENTS.md が CLAUDE.md から乖離しています。片方だけ古い運用ルールが残ると、\n"
        "そちらを読んだセッションが古い手順を実行します（例外もログも出ません）。\n"
        "CLAUDE.md を直したら AGENTS.md も作り直してください:\n"
        "  python -c \"import pathlib;p=pathlib.Path('CLAUDE.md').read_text();\\\n"
        "    [p:=p.replace(a,b) for a,b in "
        f"{list(ALLOWED_SUBSTITUTIONS)}];\\\n"
        "    pathlib.Path('AGENTS.md').write_text(p)\"\n"
        f"最初の食い違い:\n{detail}")
