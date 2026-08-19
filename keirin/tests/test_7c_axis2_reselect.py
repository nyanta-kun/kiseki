"""7C: 軸2を ◎◯ 以外の3着内率1位へ差し替える規則の回帰テスト（2026-08-19 新設）。

## 背景

軸2車が WT ◎◯ と完全一致するのは **7C の 92.8%** ＝既定状態。そこで外れると
**88.7% が「片方だけ来た」**で、飛ぶのはほぼ ◯ 側（◎ が3着内 87.2% / ◯ 75.6%）。
ユーザー判断（2026-08-19）:「◎◯の予想を売って外すのは印象が良くない。
軸2を WT◯ 以外から再選出する」。

選び方は **3着内率**。1着率・2着内率と比べて最良だった（数値は
`strategy_wt.rank_7c_reselect_axis2_off_marks` の定義部）。買い目は三連複・
軸2頭ながしで、軸に求めるのは「3着以内に入ること」だから理屈にも合う。

🔴 **これは二軸的中 −24.3pt と引き換えに配当中央 +40% を得る大きな取引**で、
   ユーザーが数字を見た上で採用を決めた。**勝手に戻さない／広げないこと。**

⚠️ 壊れても例外は出ない。差し替えが効かなくなっても「7C が元に戻った」だけで、
   ログにも出ない。テストでしか守れない。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategy_wt import rank_7c_reselect_axis2_off_marks  # noqa: E402

MAIN = Path(__file__).resolve().parent.parent / "src" / "cli" / "main.py"

# 3着内率（0-1）。1 が最上位。
P3 = {1: 0.80, 2: 0.70, 3: 0.60, 4: 0.50, 5: 0.40, 6: 0.30, 7: 0.20}


def test_replaces_axis2_when_axes_are_exactly_the_marks():
    """軸={◎1, ◯2} → 軸2 は ◎◯ 以外の3着内率1位 = 3。"""
    assert rank_7c_reselect_axis2_off_marks(P3, 1, 2, wt_honmei=1, wt_taikou=2) == 3


def test_works_when_taikou_is_axis1():
    """◯ が軸1 側にいても集合として一致していれば差し替える（軸1は触らない）。"""
    assert rank_7c_reselect_axis2_off_marks(P3, 2, 1, wt_honmei=1, wt_taikou=2) == 3


def test_pool_excludes_both_marks_not_just_taikou():
    """🔴 ◎ も候補から外す。◯ だけ外すと ◎ が軸2に来て軸が重複する。"""
    p3 = {1: 0.80, 2: 0.70, 3: 0.60, 4: 0.50}
    got = rank_7c_reselect_axis2_off_marks(p3, 3, 1, wt_honmei=1, wt_taikou=3)
    assert got not in (1, 3)
    assert got == 2


def test_does_nothing_when_axes_do_not_match_the_marks():
    """完全一致でなければ触らない（重なり1・0 はそのまま）。"""
    assert rank_7c_reselect_axis2_off_marks(P3, 1, 2, wt_honmei=1, wt_taikou=3) == 2
    assert rank_7c_reselect_axis2_off_marks(P3, 1, 2, wt_honmei=5, wt_taikou=6) == 2


def test_does_nothing_when_marks_are_missing():
    """🔴 印が取れないレースでは動かさない。

    判定不能のまま軸を替えると、根拠のない差し替えになる。
    """
    assert rank_7c_reselect_axis2_off_marks(P3, 1, 2, None, 2) == 2
    assert rank_7c_reselect_axis2_off_marks(P3, 1, 2, 1, None) == 2
    assert rank_7c_reselect_axis2_off_marks(P3, 1, 2, None, None) == 2


def test_falls_back_when_no_candidate_remains():
    """候補が居なければ元の軸2のまま（3車立て等の極端な形）。"""
    p3 = {1: 0.8, 2: 0.7}
    assert rank_7c_reselect_axis2_off_marks(p3, 1, 2, 1, 2) == 2


def test_is_deterministic_on_ties():
    """同率は車番の小さい方（再現性のため）。"""
    p3 = {1: 0.80, 2: 0.70, 5: 0.60, 3: 0.60}
    assert rank_7c_reselect_axis2_off_marks(p3, 1, 2, 1, 2) == 3


def _main_src() -> str:
    return MAIN.read_text(encoding="utf-8")


def test_reselection_happens_before_partners_are_chosen():
    """🔴 相手（legs_7c）を決める前に軸2を確定させること。

    出力の直前で差し替えると、相手が**旧軸2を除いたまま**作られ、
    新しい軸2が相手にも入った不正な買い目になる。
    """
    src = _main_src()
    i_sel = src.index("rank_7c_reselect_axis2_off_marks(")
    i_legs = src.index("legs_7c = rank_7c_select_legs(")
    assert i_sel < i_legs, "軸2の差し替えが相手決定より後になっている"


def test_7m1_gate_still_reads_the_original_top2():
    """🔴 `wt_overlap_7c_n` は差し替え**前**の上位2車で測ること。

    7M1 のゲートは「モデル上位2車 ≠ {◎,◯}」を見ているので、差し替え後の軸で
    測ると 7M1 の母集団が黙って変わる。
    """
    src = _main_src()
    m = re.search(r"rank_7s_wt_overlap_n\(\s*sel_7c\[0\],\s*sel_7c\[1\]", src)
    assert m, "wt_overlap_7c_n が sel_7c（差し替え前）で測られていない"
