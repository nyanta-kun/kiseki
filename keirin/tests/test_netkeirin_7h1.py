"""7H1（穴推奨・本命バスト型）の netkeirin 入稿変換のテスト（2026-08-06）。

🔴 **2026-08-15 に三連単一本化**（ユーザー指示「三連複BOX分を全て三連単の買い目に
振り直す」）。それ以前は三連単フォーメーション + 三連複BOX の2券種を1商品として
入稿する唯一のランクで、専用経路 `_normalize_multi_candidate` を持っていた。
いまは 9H1 と同じ `_normalize_formation_candidate` を共用する。

入稿側は候補JSONの `legs` / `legs_tf`（strategy_wt の `rank_7h1_build_legs()` が
生成した実際の買い目）を**正**とし、そこから車番グループを復元する。

ここで守るのは1点だけ:
  **復元したグループを展開し直した目集合が、元の買い目と完全一致すること。**
一致しないまま入稿すると、意図と違う買い目が外部（＝有料商品）へ出る。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import src.strategy_wt as sw

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.netkeirin_submit_wt import (  # noqa: E402
    RANK_CONFIGS,
    _normalize_formation_candidate,
    _trifecta_formation_groups,
)
from src.netkeirin_client import (  # noqa: E402
    ACT_TYPE_LONGSHOT,
    BET_KIND_TRIFECTA_FORMATION,
    expand_bet,
)
from src.preprocessing.favbust_features import (  # noqa: E402
    ROLE_FAV_MATE,
    ROLE_LEAD_TOP,
    ROLE_OTHER_MATE,
)
from src.strategy_wt import rank_7h1_build_legs  # noqa: E402

# 7車・本命=7（本命ラインは 7と6）。others はモデル3着内率の降順。
# roles は favbust_features.roles_of() の戻り値と同じ語彙。
_ROLE_LEAD_TOP = ROLE_LEAD_TOP
_FAV_LINE = ROLE_FAV_MATE
_OTHER = ROLE_OTHER_MATE

_OTHERS = [3, 4, 5, 1, 2, 6]
_ROLES = {3: _ROLE_LEAD_TOP, 4: _OTHER, 5: _OTHER, 1: _OTHER, 2: _OTHER,
          6: _FAV_LINE}


def _cand(others=None, roles=None, **extra) -> dict:
    """候補JSON1件を本番の買い目生成そのままで作る。

    🔴 `legs`（入稿が読む）と `legs_tf`（発走前判定・採点が読む）は**同じ値**。
       build_7h1_candidates が両方の名前で出すのと揃えてある。
    """
    others = others or _OTHERS
    tf = rank_7h1_build_legs(others, roles or _ROLES)
    return {"race_key": "20260807_85_07", "venue_name": "佐世保", "race_no": 7,
            "fav": 7, "others": others, "legs": tf, "legs_tf": tf, **extra}


def test_build_legs_returns_trifecta_only():
    """🔴 三連単一本化。三連複を返していた頃の2値タプルに戻していないこと。"""
    tf = rank_7h1_build_legs(_OTHERS, _ROLES)
    assert isinstance(tf, list)
    assert len(tf) == 8
    assert all(isinstance(x, str) and x.count("-") == 2 for x in tf)


def test_formation_groups_roundtrip_on_real_shape():
    """本番の rank_7h1_build_legs() が作る8点が、フォーメーション復元で再現されること。"""
    tf = rank_7h1_build_legs(_OTHERS, _ROLES)
    groups = _trifecta_formation_groups(tf)
    assert groups[0] == [3]                       # 1着＝別ライン先頭で固定
    assert len(groups[1]) == 2                    # 2着＝プール上位2車
    assert groups[2] == sorted(set(_OTHERS) - {3})  # 3着＝本命以外の総流し


def test_formation_groups_rejects_non_expandable_legs():
    """フォーメーションで表現できない目の集合は復元させない（黙って通さない）。"""
    # 1着[3]×2着[4,5]×3着[1,2] を展開すると4点になるが、ここでは3点しか無い
    with pytest.raises(ValueError, match="一致しません"):
        _trifecta_formation_groups(["3-4-1", "3-4-2", "3-5-1"])


def test_formation_groups_rejects_bad_format():
    with pytest.raises(ValueError):
        _trifecta_formation_groups(["3-4"])
    with pytest.raises(ValueError):
        _trifecta_formation_groups([])


def test_normalize_builds_single_trifecta_row_and_marks(monkeypatch):
    """候補JSON1件から三連単フォーメーション1行と印が組み上がること。

    印はユーザー確定の規則（2026-08-06）:
      ◎=1着固定車 / ○=2着列1番手 / ▲=2着列2番手 /
      △=3着だけで買っている車 / 除外した本命は印なし
    """
    import scripts.netkeirin_submit_wt as sub
    monkeypatch.setattr(sub, "_load_trifecta_board", lambda rk: {})   # 板なし＝均等

    legs, marks, axis1, axis2 = _normalize_formation_candidate(
        _cand(), RANK_CONFIGS["7H1"], "20260807_85_07")

    # 🔴 単一券種。三連複の行が混ざっていないこと（一本化の中核）
    assert len(legs) == 1
    assert legs[0].bet_kind == BET_KIND_TRIFECTA_FORMATION
    # 8点なので 10,000 // 8 を100円単位で切り捨て＝1,200円/点（他ランクと同じ規則）
    assert legs[0].stake_per_line == sw.unit_stake(8) == 1200
    assert legs[0].stake_per_line * 8 == 9600 <= sw.RANK_7H1_BUDGET_CAP

    assert axis1 == 3 and marks[3] == "◎"
    assert marks[axis2] == "○"
    assert sorted(marks) == [1, 2, 3, 4, 5, 6]   # 本命(7)には印を付けない
    assert 7 not in marks
    assert marks[4] == "○" and marks[5] == "▲"   # others 順で ○/▲ を割り当てる
    assert marks[1] == marks[2] == marks[6] == "△"


def test_marks_follow_others_order_not_car_number(monkeypatch):
    """○/▲ は車番順ではなく others（モデル3着内率の降順）順で決まること。

    ⚠️ 9H1 は同じ序列を `order` というキーで渡す。共用関数が片方しか見ないと
       ○▲ が車番順に落ちる（表示の序列と予想の序列が食い違う）。
    """
    import scripts.netkeirin_submit_wt as sub
    monkeypatch.setattr(sub, "_load_trifecta_board", lambda rk: {})

    others = [3, 5, 4, 1, 2, 6]     # プール上位は 5 → 4 の順
    roles = {3: _ROLE_LEAD_TOP, 5: _OTHER, 4: _OTHER, 1: _OTHER, 2: _OTHER,
             6: _FAV_LINE}
    _, marks, _, _ = _normalize_formation_candidate(
        _cand(others, roles), RANK_CONFIGS["7H1"], "x")
    assert marks[5] == "○" and marks[4] == "▲"


def test_falls_back_to_equal_when_odds_incomplete(monkeypatch):
    """オッズが一部でも欠けたらダッチにせず均等へ戻す。

    🔴 欠けたままダッチに入れると「安いから切った」のか「板が無いから消えた」のか
       区別できず、買い目が黙って痩せる。
    """
    import scripts.netkeirin_submit_wt as sub

    tf = rank_7h1_build_legs(_OTHERS, _ROLES)
    points = sorted(expand_bet(BET_KIND_TRIFECTA_FORMATION,
                               _trifecta_formation_groups(tf)))
    partial = {p: 60.0 for p in points[:-1]}      # 1点だけ板が無い
    monkeypatch.setattr(sub, "_load_trifecta_board", lambda rk: partial)

    legs, _, _, _ = _normalize_formation_candidate(
        _cand(), RANK_CONFIGS["7H1"], "20260807_85_07")
    assert len(legs) == 1                          # ダッチなら1点=1行で8行になる
    assert legs[0].stake_per_line == sw.unit_stake(8)


def test_uses_dutch_when_odds_complete(monkeypatch):
    """オッズが買う目**すべて**に揃えばダッチ配分（1点=1行）になること。"""
    import scripts.netkeirin_submit_wt as sub

    tf = rank_7h1_build_legs(_OTHERS, _ROLES)
    points = sorted(expand_bet(BET_KIND_TRIFECTA_FORMATION,
                               _trifecta_formation_groups(tf)))
    # 🔴 板は**明示的に**与える。実DBに任せると、板を持つ環境ではダッチ経路・
    #    持たない環境（CI）ではフォールバック経路と、同じテストが環境によって
    #    別の物を検査してしまう。
    tf_board = {p: 60.0 + 5 * i for i, p in enumerate(points)}
    monkeypatch.setattr(sub, "_load_trifecta_board", lambda rk: tf_board)

    legs, _, _, _ = _normalize_formation_candidate(
        _cand(), RANK_CONFIGS["7H1"], "20260807_85_07")

    total = sum(leg.stake_per_line for leg in legs)
    assert total <= sw.RANK_7H1_BUDGET_CAP
    # 採用したどの目が来ても予算の1.3倍以上が返る（ダッチの不変条件）
    pays = [leg.stake_per_line * tf_board[tuple(g[0] for g in leg.groups)]
            for leg in legs]
    assert min(pays) >= total * 1.3


def test_7h1_config_shape():
    """7H1 は三連単フォーメーション単一券種なので `formation_bet` で分岐する。

    🔴 `multi_bet`（2券種の旧経路）が復活していないこと。復活すると
       `_normalize_multi_candidate` を探しに行って入稿が丸ごと落ちる。
    """
    cfg = RANK_CONFIGS["7H1"]
    assert cfg["formation_bet"] is True
    assert "multi_bet" not in cfg
    assert cfg["n_cars"] == 7
    assert cfg["file_key"] == "s7h1"
    assert "bet_kind" not in cfg and "stake_per_line" not in cfg
    # 勝負アイコンは「穴狙い」（ユーザー確定・2026-08-06）
    assert cfg["act_type"] == ACT_TYPE_LONGSHOT
