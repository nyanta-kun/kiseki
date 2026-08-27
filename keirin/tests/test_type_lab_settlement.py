"""型ラボの採点が**同着**を落とさないことの回帰テスト（2026-08-28 新設）。

## なぜ要るか

2026-08-22 に `src/result_top3.py`（同着の当たり目を作る唯一の正本）を新設し、
既存ランクの `backfill_7*_rank_wt.py` は全部そちらへ移したが、
**型ラボの採点だけが移されていなかった**。旧実装は `{着順: 車番}` の辞書で
持っていたため:

  - 3着同着 → 後勝ちで片方が消える（SQL に ORDER BY が無く**非決定的**）
  - 1着/2着同着 → 1・2・3 がそろわず**永久に未採点**

実測で **14行が当たり目を買っているのに `hit=false`**、**339行が永久保留**だった。
2025-10-27 岸和田6R（3着が2番と6番の同着）では、同じレースの `A_hit` が
`1-4-2` を1,800円買っているのに外れ扱い、`A_pay` は `1-4-6` で的中と記録されていた。

🔴 **例外もログも出ない壊れ方**なので、経路そのものをテストで固定する。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from src.result_top3 import (  # noqa: E402
    hit_trifecta, hit_trio, representative, winning_trifectas, winning_trios,
)

import settle_type_lab_picks as S  # noqa: E402


# ───────────────── 経路そのものを固定する ─────────────────

def test_settlement_uses_the_dead_heat_source_of_truth():
    """🔴 採点が `src/result_top3` を使っていること。

    自前で `finish_order` の 1/2/3 を引き直すと、同着で当たりを1通りしか作れない。
    """
    src = (REPO / "scripts" / "settle_type_lab_picks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("result_top3"):
            imported |= {a.name for a in n.names}
    assert {"winning_trios", "winning_trifectas", "representative"} <= imported, imported


def test_backfill_uses_the_same_source_of_truth():
    """答え合わせのバックフィルも同じ（`win_tf_odds` が実際に来ていない並びになる）。"""
    src = (REPO / "scripts" / "backfill_type_lab_outcome.py").read_text(encoding="utf-8")
    assert "from src.result_top3 import" in src


def test_finish_query_orders_deterministically():
    """🔴 `ORDER BY finish_order, frame_no` のタイブレークが要る。

    無いと同じレースを組み直すたびに「正解」が入れ替わり、台帳の再現性が壊れる
    （`result_top3.TOP3_SQL` の docstring と同じ理由）。
    """
    for f in ("settle_type_lab_picks.py", "backfill_type_lab_outcome.py"):
        src = (REPO / "scripts" / f).read_text(encoding="utf-8")
        assert "ORDER BY race_key, finish_order, frame_no" in src, f
        assert "finish_order BETWEEN 1 AND 3" in src, f


def test_finish_does_not_use_a_position_keyed_dict():
    """🔴 `{着順: 車番}` に戻さないこと（同着で後勝ちして片方が消える）。"""
    src = (REPO / "scripts" / "settle_type_lab_picks.py").read_text(encoding="utf-8")
    assert "out[rk][int(fo)] = int(fn)" not in src


# ───────────────── 当たり目の判定 ─────────────────

def test_three_way_finish_produces_two_trifecta_wins():
    """3着同着（実データ 2025-10-27 岸和田6R）。"""
    fin = [(1, 1), (2, 4), (3, 2), (3, 6)]
    assert winning_trifectas(fin) == [(1, 4, 2), (1, 4, 6)]
    # A_hit は 1-4-2 を買っている → 的中
    bought = [(1, 4, 5), (1, 4, 3), (1, 4, 2)]
    assert hit_trifecta(bought, winning_trifectas(fin)) == (1, 4, 2)
    # A_pay は 1-4-6 を買っている → こちらも的中（同じレースで両方当たる）
    bought2 = [(1, 4, 3), (1, 4, 2), (1, 4, 6), (1, 5, 3)]
    assert hit_trifecta(bought2, winning_trifectas(fin)) == (1, 4, 2)


def test_first_place_dead_heat_is_settled_not_skipped():
    """🔴 1着同着でも採点する。旧実装は 1・2・3 を要求して**永久に保留**していた。"""
    fin = [(1, 1), (1, 2), (3, 5)]           # 着順は 1,1,3（2 が無い）
    assert winning_trifectas(fin) == [(1, 2, 5), (2, 1, 5)]
    assert winning_trios(fin) == [frozenset({1, 2, 5})]
    assert representative(winning_trifectas(fin)) == (1, 2, 5)


def test_all_bought_winning_combos_are_paid():
    """🔴🔴 同着で当たり目を2つとも買っていたら**両方とも払い戻される**。

    実測 2025-10-27 岸和田6R（3着が2番と6番の同着）: 確定オッズは
    `1-4-2 = 28.0倍` と `1-4-6 = 170.7倍` が**両方とも板にある**。
    `A_pay` はこの2目を 1,800円 / 1,300円 で買っていたので
    払戻は 50,400 + 221,910 = **272,310円**。

    ⚠️ `result_top3.hit_trifecta` は**1つしか返さない**ので、そのまま使うと
       取りこぼす。あちらは他ランクが共有する正本なので API は変えず、
       型ラボの採点側で全当たり目を舐める。
    """
    fin = [(1, 1), (2, 4), (3, 2), (3, 6)]
    wins = {"-".join(str(x) for x in w) for w in winning_trifectas(fin)}
    legs = [{"combo": "1-4-3", "stake": 4100}, {"combo": "1-4-2", "stake": 1800},
            {"combo": "1-4-6", "stake": 1300}, {"combo": "1-5-3", "stake": 1100}]
    odds = {"1-4-2": 28.0, "1-4-6": 170.7}
    won = [l for l in legs if l["combo"] in wins]
    assert [l["combo"] for l in won] == ["1-4-2", "1-4-6"]
    assert sum(round(l["stake"] * odds[l["combo"]]) for l in won) == 272_310
    # 1目しか返さない API を素で使うと 50,400 で止まる
    assert hit_trifecta([tuple(int(c) for c in l["combo"].split("-")) for l in legs],
                        winning_trifectas(fin)) == (1, 4, 2)


def test_settlement_sums_every_winning_leg():
    """採点が「当たった leg を全部足す」形になっていること（構文で固定）。"""
    src = (REPO / "scripts" / "settle_type_lab_picks.py").read_text(encoding="utf-8")
    assert "won_legs" in src and "payout = sum(" in src
    # 1目しか採らない旧実装へ戻していないこと
    assert "hit_trifecta(" not in src and "hit_trio(" not in src


def test_trio_dead_heat_gives_two_wins():
    fin = [(1, 4), (2, 3), (3, 1), (3, 7)]
    wins = winning_trios(fin)
    assert wins == [frozenset({1, 3, 4}), frozenset({3, 4, 7})]
    assert hit_trio([frozenset({3, 4, 7})], wins) == frozenset({3, 4, 7})


def test_incomplete_finish_is_not_settled():
    """3着までそろわないレースは採点しない（0 を書くと外れと区別できない）。"""
    assert winning_trifectas([(1, 1), (2, 2)]) == []


# ───────────────── 表記の変換 ─────────────────

def test_combo_string_matches_the_legs_notation():
    """`legs[].combo` と同じ表記でないと的中を照合できない。"""
    assert S._combo_str((1, 4, 2), "trifecta") == "1-4-2"
    assert S._combo_str(frozenset({7, 5, 2}), "trio") == "2=5=7"
    assert S._combo_str(None, "trio") == ""


def test_cars_parses_both_notations():
    assert S._cars("1-4-2") == [1, 4, 2]
    assert S._cars("2=5=7") == [2, 5, 7]


def test_finish_helper_drops_races_without_three_finishers():
    """`_finish` は当たり目を作れないレースを落とす（採点対象にしない）。"""
    assert winning_trifectas([(1, 1), (2, 2)]) == []
    assert winning_trifectas([(1, 1), (2, 2), (3, 3)]) == [(1, 2, 3)]


# ───────────────── 予測オッズの畳み込み ─────────────────

def test_board_trio_odds_do_not_double_count_the_payback_rate():
    """🔴 三連複の予測オッズは `1/Σ(1/PO)`。`払戻率/Σ` は 0.75 倍ずれる。

    `build_type_lab_picks._fold_to_trio` の docstring が明記している罠だが、
    `build_race_type_board.py` だけ直っていなかった（2026-08-28 是正）。
    台を作り直すと三連複の予測オッズが一律 25% 下がり、平均想定払戻ゲートも
    `docs/type_lab/type_d.md` の数値も**エラー無しで別物になる**。
    """
    # ⚠️ コメントに罠そのものを書いてあるので、**コード行だけ**を見る。
    code = [ln.split("#")[0] for ln
            in (REPO / "scripts" / "build_race_type_board.py")
            .read_text(encoding="utf-8").splitlines()]
    body = "\n".join(code)
    assert "1.0 / q3" in body
    assert "PAYBACK / q3" not in body


def test_fold_to_trio_matches_the_definition():
    """三連複の予測オッズ = 1/Σ_perm(1/PO_perm)。"""
    import build_type_lab_picks as B
    tf = {(1, 2, 3): 10.0, (1, 3, 2): 20.0, (2, 1, 3): 40.0,
          (2, 3, 1): 40.0, (3, 1, 2): 40.0, (3, 2, 1): 40.0}
    odds, _ = B._fold_to_trio(tf, {k: 0.0 for k in tf})
    s = sum(1.0 / v for v in tf.values())
    assert abs(odds[frozenset({1, 2, 3})] - 1.0 / s) < 1e-9
    # 払戻率を掛けると 0.75 倍ずれる（その値になっていないこと）
    assert abs(odds[frozenset({1, 2, 3})] - 0.75 / s) > 1e-6
