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


# ---- 落差ガード（2026-08-21）--------------------------------------------
# 差し替えは「軸2を ◎◯ から離す」操作なので、離した先が弱すぎると
# **的中率と配当を同時に失う**。両窓で有害が確認された 30pt 以上では止める。

def test_does_not_replace_when_the_cliff_is_large():
    """🔴 実例: 2026-08-21 西武園1R。

    複勝率 1号 88.2 / 4号 88.1 に対し3番手 5号 35.0（落差 53pt）。
    ◎=1・◯=4 なので従来は軸2が 4 → 5 へ差し替わり、買い目 1=4=5 / 1=5=7 /
    1=2=5 / 1=3=5 は 1-4-3 の決着で全滅した（軸2の5号は7着）。
    """
    p3 = {1: 0.882, 4: 0.881, 5: 0.350, 7: 0.306, 3: 0.296, 2: 0.218, 6: 0.054}
    assert rank_7c_reselect_axis2_off_marks(
        p3, 1, 4, wt_honmei=1, wt_taikou=4) == 4, "落差 53pt でも差し替えている"


def test_replaces_when_the_cliff_is_small():
    """落差が小さいレースでは従来どおり差し替える（判定は落差だけで行う）。"""
    p3 = {1: 0.80, 2: 0.70, 3: 0.55, 4: 0.40}
    # 軸2(0.70) − 差し替え先(0.55) = 0.15 < 0.30
    assert rank_7c_reselect_axis2_off_marks(p3, 1, 2, wt_honmei=1, wt_taikou=2) == 3


def test_cliff_boundary():
    """閾値をまたぐと挙動が変わる（境界そのものは浮動小数の丸めに委ねない）。

    ⚠️ `0.70 - 0.40` は 0.2999999999999999 になる。境界ちょうどの値で
       検査を書くと、実装ではなく**浮動小数の丸めを試す**テストになる。
    """
    over = {1: 0.90, 2: 0.70, 3: 0.35, 4: 0.30}      # 落差 0.35 > 0.30 → 止める
    assert rank_7c_reselect_axis2_off_marks(over, 1, 2, wt_honmei=1, wt_taikou=2) == 2
    under = {1: 0.90, 2: 0.70, 3: 0.45, 4: 0.30}     # 落差 0.25 < 0.30 → 差し替える
    assert rank_7c_reselect_axis2_off_marks(under, 1, 2, wt_honmei=1, wt_taikou=2) == 3


def test_cliff_threshold_is_overridable_and_defaults_to_30pt():
    """閾値は引数で動かせる（掃引スクリプトが本番関数を呼べるように）。"""
    from src.strategy_wt import RANK_7C_RESELECT_CLIFF_MAX

    assert RANK_7C_RESELECT_CLIFF_MAX == 0.30
    p3 = {1: 0.882, 4: 0.881, 5: 0.350, 7: 0.306}
    assert rank_7c_reselect_axis2_off_marks(
        p3, 1, 4, wt_honmei=1, wt_taikou=4, cliff_max=0.60) == 5


def test_scale_is_zero_to_one_not_percent():
    """🔴 スケール取り違えの検知。

    `top3_probs` は 0-1（`pred_prob`）。もし呼び出し側が % を渡すと落差が
    100倍になり、**すべてのレースで差し替えが止まる**（静かに仕様が消える）。
    """
    pct = {1: 88.2, 4: 88.1, 5: 35.0, 7: 30.6}
    # % で渡すと落差 53.1 ≧ 0.30 なので必ず止まる ＝ 呼び出し側の単位を疑うこと
    assert rank_7c_reselect_axis2_off_marks(pct, 1, 4, wt_honmei=1, wt_taikou=4) == 4
