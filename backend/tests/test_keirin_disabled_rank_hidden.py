"""入稿対象OFFのランクを Web の集計・表示から外す仕組みの回帰テスト（2026-08-12）。

ユーザー要望「入稿対象オフにしたものは web の表示からも外してください」。

守るのは3点:

1. **fail-open であること**（`netkeirin_settings` に行が無いランクは表示する）。
   ここを fail-closed にすると、設定行を入れ忘れたランクが静かに Web から消える。
   keirin 側 `netkeirin_submit_wt._is_enabled()` と同じ規約に揃える。
2. **rank の綴りの違いを吸収していること**。picks_history は `RANK_7C`、
   netkeirin_settings は `7C`。突き合わせを間違えると**全ランクが素通り**して
   除外が効かないのに、エラーも出ないので気づけない。
3. **ランクで絞る全てのクエリに条件が入っていること**。1箇所でも漏れると
   その画面だけ古いランクが出続ける（このリポジトリが繰り返している型）。
"""
from __future__ import annotations

import re
from pathlib import Path

from src.api import keirin_router as kr

SRC = Path(kr.__file__).read_text(encoding="utf-8")


def test_exclusion_is_fail_open():
    """行が無い＝表示する。`NOT EXISTS ... enabled = FALSE` の形であること。

    `enabled = TRUE` を要求する形（EXISTS）にすると fail-closed になり、
    設定行の無いランクが消える。
    """
    cond = kr._enabled_rank_cond()
    assert "NOT EXISTS" in cond, f"fail-open な NOT EXISTS でない: {cond}"
    assert "enabled = FALSE" in cond, f"OFF の行だけを除外する形でない: {cond}"
    assert "enabled = TRUE" not in cond, "fail-closed（ONの行だけ表示）になっている"


def test_exclusion_joins_on_rank_prefix():
    """`RANK_` の接頭辞を補って突き合わせていること。

    ⚠️ ここを `s.rank_key = ph.rank` にすると**永遠に一致せず**、除外が
       まったく効かないまま静かに通る。
    """
    cond = kr._enabled_rank_cond()
    assert "'RANK_' || s.rank_key" in cond, f"接頭辞の補完が無い: {cond}"


def test_exclusion_uses_the_given_alias():
    """picks_history の別名（ph / ph2）に追随すること。"""
    assert "ph.rank" in kr._enabled_rank_cond("ph")
    assert "ph2.rank" in kr._enabled_rank_cond("ph2")


def test_every_rank_filtered_query_has_the_exclusion():
    """ランクで絞るクエリ全てに除外条件が入っていること。

    `ph.rank IN (...)` を書いた直後に `_enabled_rank_cond` を置く規約。
    新しいクエリを足して条件を忘れると、その画面だけ OFF のランクが出る。
    """
    hits = re.findall(r"AND (ph2?)\.rank IN \{[^}]+\}\s*\n\s*AND \{_enabled_rank_cond",
                      SRC)
    total = len(re.findall(r"AND ph2?\.rank IN \{", SRC))
    assert total > 0, "ランクで絞るクエリが1つも見つからない（検出方法が古い）"
    assert len(hits) == total, (
        f"ランクで絞るクエリ {total} 箇所のうち {len(hits)} 箇所にしか "
        f"除外条件が入っていない")


def test_visible_rank_labels_returns_display_labels():
    """`visible_rank_labels` は表示ラベル（7C 等）を定義順で返すこと。

    内部rank（RANK_7C）を返すとフロントの RANK_ORDER と突き合わない。
    """
    src = SRC[SRC.index("async def visible_rank_labels"):]
    src = src[:src.index("\n\n\n")] if "\n\n\n" in src else src
    assert "_PAPER_RANK_LABELS.items()" in src
    assert "for internal, label in" in src and "label for internal, label" in src


def test_summary_exposes_visible_ranks():
    """/keirin/summary が `visible_ranks` を返すこと（フロントの絞り込みの元）。

    ⚠️ **行の書き方ではなく「呼んでいること」と「返していること」を見る**
       （2026-08-23 更新）。以前は
       `'"visible_ranks": await visible_rank_labels(db)'` という**1行の文字列**を
       固定していたため、`get_summary` を並列化して呼び出しをヘルパへ移した
       だけで落ちた。中身は同じなのに落ちるテストは、リファクタを妨げるだけで
       退行を捕まえない。
    """
    import inspect

    from src.api.keirin_router import get_summary

    src = inspect.getsource(get_summary)
    assert "visible_rank_labels(" in src, "visible_rank_labels を呼ぶこと"
    assert '"visible_ranks":' in src, "レスポンスに visible_ranks を含めること"


def test_frontend_filters_are_fail_open():
    """フロントも `visible_ranks` が無ければ絞らないこと。

    API 側の追加を忘れた瞬間に全ランクが消えるのを防ぐ。
    """
    front = Path(__file__).resolve().parents[2] / "frontend" / "src"
    page = (front / "app" / "keirin" / "page.tsx").read_text(encoding="utf-8")
    assert "allow ? RANK_ORDER.filter" in page, "トップの絞り込みが fail-open でない"
    stats = (front / "app" / "keirin" / "stats" / "page.tsx").read_text(encoding="utf-8")
    assert "!visibleRanks || visibleRanks.includes" in stats, \
        "統計ページのチップ絞り込みが fail-open でない"
