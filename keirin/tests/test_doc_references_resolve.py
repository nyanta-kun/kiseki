"""コード・CLAUDE.md から参照している **`docs/…` が実在する**ことを固定する。

## なぜ必要か

`src/` の定数コメントは「なぜこの値なのか」を docs へ委ねている
（例: `RANK_7T3_LINE_ADJ_W` → `docs/tf_rival614_line_pair_2026_08_26.md`）。
参照先が消えると**根拠を辿れない定数**だけが残り、次に触る人が
「実測があるのか無いのか」を判断できなくなる。

2026-09-01 のドキュメント整理（130本 → 60本）で、
**残す／消すの判断そのものをこの参照関係で行った**。
参照が生きているものは残し、ゼロのものだけ消している。
この関係を壊さないための固定。

⚠️ 逆（「参照ゼロの docs を禁じる」）はしない。
   `RECOMMENDATION.md` や `RUNBOOK.md` のように**人が読むためだけの文書**が
   正しく存在するため。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

KEIRIN = Path(__file__).resolve().parents[1]
REPO = KEIRIN.parent

#: `docs/…md` の参照を拾う。行頭の `#` だけを見ればよい用途ではないので全文を走査する。
PATTERN = re.compile(r"(?<![\w/])docs/[A-Za-z0-9_][A-Za-z0-9_./-]*\.md")

#: 実体を持たない参照（テンプレートの穴埋め・例示）。
ALLOWED_PLACEHOLDERS: frozenset[str] = frozenset({
    "docs/monthly_rollover/YYYYMM.md",
})


def _sources() -> list[Path]:
    out = [KEIRIN / "CLAUDE.md", REPO / "CLAUDE.md"]
    for d in ("src", "scripts"):
        out += sorted((KEIRIN / d).rglob("*.py"))
    return [p for p in out if p.exists()]


def test_every_doc_reference_resolves():
    missing: list[str] = []
    for f in _sources():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for ref in sorted(set(PATTERN.findall(text))):
            if ref in ALLOWED_PLACEHOLDERS:
                continue
            # keirin 相対 → ルート相対 の順で探す（両方の書き方が実在する）
            if (KEIRIN / ref).exists() or (REPO / ref).exists():
                continue
            missing.append(f"{f.relative_to(REPO)} → {ref}")

    assert not missing, (
        "存在しないドキュメントを参照しています。定数の根拠が辿れなくなります。\n"
        "文書を消したなら参照も直すこと（`RECOMMENDATION.md` へ寄せるのが既定）:\n  "
        + "\n  ".join(sorted(set(missing))))


@pytest.mark.parametrize("name", ["RECOMMENDATION.md", "type_lab/RUNBOOK.md",
                                  "trifecta_playbook.md", "prediction-factors.md",
                                  "vintage_model_policy.md", "system-architecture.md"])
def test_core_docs_exist(name: str):
    """整理で消してはいけない中核文書。`RECOMMENDATION.md` は現状の唯一の入口。"""
    assert (KEIRIN / "docs" / name).exists(), f"docs/{name} が無い"
