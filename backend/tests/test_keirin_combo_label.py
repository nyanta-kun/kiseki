"""買い目ラベル（3連複 / 3連単）の表示が**ランク名で決まっていない**ことの検査。

## なぜ要るのか

`/keirin` の推奨カードは長らく「7H1 以外は 3連複」というランク名の白名簿で
券種ラベルを決めていた。三連単ランクを足すたびに**買い目を偽る**:

- 2026-08-13: RANK_7T1（三連単）が「**3連複**: 7-3-2」と表示された（ユーザー報告）
- RANK_9H1（三連単）も同型で、`三単:…` の前に「3連複:」が付いていた

券種は**買い目の区切り文字**で決まる（三連複 `1=2=4` / 三連単 `1-2-4`）。
ランクを増やすたびに直す設計に戻さないよう、機械的に縛る。

フロントに単体テスト基盤が無いため、`test_keirin_rank_consistency.py` と同じく
**ソースを読んで**検査する。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PAGE = (Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "app" / "keirin" / "page.tsx")


@pytest.fixture(scope="module")
def src() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_combo_label_helper_exists(src: str):
    assert "function formatComboLabel(" in src, (
        "券種ラベルをデータから決める関数がありません")


def test_bet_type_is_not_decided_by_rank_name(src: str):
    """🔴 ランク名で券種を決める分岐が復活していないこと。

    `const is7h1 = pick.rank === "RANK_7H1"` の形が典型。これがあると
    三連単ランクを足すたびに「3連複」と表示される。
    """
    assert not re.search(r'const\s+is7h1\s*=', src), (
        "ランク名で券種を分岐しています（三連単ランクが 3連複 と表示されます）")
    # ラベル生成のところでランク定数を直接見ていないこと
    m = re.search(r"const comboLabel =.*?;\n", src, re.S)
    assert m, "comboLabel の生成箇所が見つかりません"
    assert "RANK_" not in m.group(0), (
        f"comboLabel の生成でランク名を見ています: {m.group(0)[:120]}")


def test_label_is_derived_from_separator(src: str):
    """券種の判定が区切り文字（= と -）に基づいていること。"""
    fn = src[src.index("function formatComboLabel("):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert "3連単" in fn and "3連複" in fn, "両方の券種ラベルを持っていません"
    assert "=" in fn and "-" in fn


def test_trifecta_is_only_collapsed_when_order_is_fixed(src: str):
    """🔴 三連単は1着・2着が全目で同じときだけ畳むこと。

    共通2車が入れ替わる形（1-2-3 と 2-1-3）を畳むと**着順を偽る**。
    """
    # 🔴 **2026-09-03 に畳み込みを `lib/keirin-combo.ts` へ出した。**
    #    畳んだ表記と買った集合が一致することは vitest（`keirin-combo.test.ts`）が
    #    **展開して集合比較**で固定しているので、ここは「page.tsx が自前で畳まず
    #    その関数を使っていること」だけを見る（実装のコピーが増えるのを防ぐ）。
    fn = src[src.index("function formatComboLabel("):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert "foldTrifecta(" in fn, "三連単の畳み込みが keirin-combo.ts を経由していません"
    assert "c[0] === a1 && c[1] === a2" not in src, (
        "page.tsx に畳み込みの写しが残っています（keirin-combo.ts へ寄せること）")
