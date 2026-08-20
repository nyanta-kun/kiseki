"""`pred_combo` の解釈・整形の回帰テスト（2026-08-14）。

## 背景（実際に誤通知していた）

Discord の確定通知は `pred_combo` を自前パースしていたが、想定していたのは
**畳んだ形**（`5=1-2,3,4`）だけだった。7H1/7H2 は**展開形**
（`三複:2=5=7,2=5=6,… / 三単:5-2-3,5-2-4,…`）で保存されるため、

- 軸と相手を取り違え、**三連複が当たっていても外れ**と通知していた
- `❌ 不的中（軸3/2）` という**2車のはずが3車**の表示が出ていた

さらに表示も1点ずつの羅列のままだった（ユーザー指摘）。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.combo_label import (  # noqa: E402
    axis_cars, format_bet_lines, format_pred_combo, is_hit, parse_pred_combo,
)

# 実データ（picks_history 2026-08-13 の RANK_7H1）
H1 = ("三複:2=5=7,2=5=6,5=6=7,2=6=7 / "
      "三単:5-2-3,5-2-4,5-2-7,5-2-6,5-7-3,5-7-2,5-7-4,5-7-6")


def test_collapsed_trio_is_parsed():
    assert parse_pred_combo("5=1-2,3") == [("trio", [(1, 2, 5), (1, 3, 5)])]


def test_collapsed_trifecta_keeps_order():
    assert parse_pred_combo("三単:5-2-3,4") == [("trifecta", [(5, 2, 3), (5, 2, 4)])]


def test_axis_sum_annotation_is_ignored():
    """`(axis_sum=1.5)` のような補助情報を車番として拾わないこと。"""
    assert parse_pred_combo("5=1-2,3 (axis_sum=1.5)") == \
        [("trio", [(1, 2, 5), (1, 3, 5)])]


def test_expanded_two_bet_kinds_are_split():
    parsed = parse_pred_combo(H1)
    assert [k for k, _ in parsed] == ["trio", "trifecta"]
    assert (2, 5, 7) in parsed[0][1]          # 三連複は昇順
    assert (5, 2, 3) in parsed[1][1]          # 三連単は着順のまま


def test_trio_hit_is_order_independent():
    """🔴 三連複 2=5=7 は着順が 5-2-7 でも的中（以前は外れと通知していた）。"""
    assert is_hit(H1, (5, 2, 7)) is True
    assert is_hit(H1, (2, 7, 5)) is True


def test_trifecta_hit_requires_the_exact_order():
    tf = "三単:5-2-3,4"
    assert is_hit(tf, (5, 2, 3)) is True
    assert is_hit(tf, (5, 3, 2)) is False, "着順違いを的中にしてはいけない"
    assert is_hit(tf, (2, 5, 3)) is False


def test_box_has_no_axis():
    """🔴 BOX には共通の軸が無い。空を返すこと（『軸3/2』の再発防止）。"""
    assert axis_cars(H1) == []
    assert len(axis_cars("5=1-2,3,4")) == 2


def test_trifecta_is_collapsed_by_first_and_second():
    """1着・2着が同じ点をまとめる（7H1 の8点 → 2フォーメーション）。"""
    body = format_pred_combo(H1).split("三単:")[1]
    assert body == "5-2-3,4,7,6 5-7-3,2,4,6"


def test_trio_collapses_only_with_a_common_pair():
    assert format_pred_combo("5=1-2,3,4") == "1=5=2,3,4"
    # BOX は共通2車が無いので列挙のまま（畳むと嘘になる）
    assert format_pred_combo("2=5=7,2=5=6,5=6=7,2=6=7") == \
        "2=5=7,2=5=6,5=6=7,2=6=7"


def test_unparsable_text_is_returned_as_is():
    assert format_pred_combo("見送り") == "見送り"
    assert format_pred_combo("") == ""
    assert format_pred_combo(None) == ""
    assert is_hit("見送り", (1, 2, 3)) is None


def test_incomplete_result_is_undecidable():
    assert is_hit("5=1-2,3", (1, 2)) is None


def test_notifier_uses_the_shared_parser():
    """🔴 通知側に自前パースを戻さないこと（誤通知の再発防止）。"""
    src = (REPO / "scripts" / "notify_race_result_wt.py").read_text(encoding="utf-8")
    assert "from src.combo_label import" in src
    assert "combo.startswith(\"三単:\")" not in src, \
        "自前の券種判定が復活している。src/combo_label を使うこと"


# --- 券種ラベルの省略 / bet_detail からの整形（2026-08-21）-------------------

def test_labels_can_be_dropped_because_the_separator_carries_the_bet_kind():
    """三連複 `=` / 三連単 `-` で券種が判るので接頭辞は冗長（ユーザー方針）。"""
    assert format_pred_combo("三単:5-2-3,5-2-4", labels=False) == "5-2-3,4"
    assert format_pred_combo("三複:2=5=7,2=5=6", labels=False) == "2=5=7,6"
    got = format_pred_combo("三複:2=5=7,2=5=6 / 三単:5-2-3", labels=False)
    assert got == "2=5=7,6 / 5-2-3"


def test_labels_false_still_returns_the_raw_text_when_unparsable():
    """解釈できない断片は券種が判らないので原文のまま返す。"""
    assert format_pred_combo("見送り", labels=False) == "見送り"


def test_bet_detail_lines_fold_like_pred_combo():
    """`bet_detail.lines` を pred_combo と同じ表記へ畳む。"""
    trio = [{"bet_type": "3連複", "combo": c}
            for c in ("1=3=5", "2=3=5", "3=4=5", "3=5=7")]
    assert format_bet_lines(trio) == "3=5=1,2,4,7"
    tf = [{"bet_type": "3連単", "combo": c}
          for c in ("3-2-1", "3-2-4", "3-7-1", "3-7-2")]
    assert format_bet_lines(tf) == "3-2-1,4 3-7-1,2"


def test_bet_detail_of_two_bet_kinds_keeps_both():
    """7H2 のような2券種の商品は両方出す（区切り文字で見分けられる）。"""
    got = format_bet_lines([{"bet_type": "3連複", "combo": "1=2=3"},
                            {"bet_type": "3連単", "combo": "1-2-3"}])
    assert got == "1=2=3 / 1-2-3"


def test_bet_detail_without_lines_is_empty_not_an_error():
    """買い目が引けないときは空文字（通知を落とさない）。"""
    assert format_bet_lines(None) == ""
    assert format_bet_lines([]) == ""
    assert format_bet_lines([{"bet_type": "2車複", "combo": "1=2"}]) == ""
