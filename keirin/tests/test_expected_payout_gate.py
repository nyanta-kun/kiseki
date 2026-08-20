"""想定払戻(下限)による足切りの回帰テスト（2026-08-19 新設 / 2026-08-21 改定）。

## 背景

ユーザー方針: 「日中の硬いレースは母数があれば売上がつき的中率にも効く、と
考えていたが、的中率も圧倒的でなく売れていない。このレンジは入稿対象から
除外する。**的中精度は同程度を確実に**」。

実測（7C三連複 505R・44日・朝の板がある全期間）で、**想定払戻(下限) < 1.0 の帯
だけ**が「売れない（無売上 64.3%）」と「当たっても返らない（表示的中 27.9% <
全体 33.1%）」を両立していた。1.2 以上へ広げると表示的中が 32.2% へ落ちる。
数値は `src/stake_allocation.MIN_EXPECTED_PAYOUT_7C` の定義部。

⚠️ 壊れても例外は出ない。**閾値を上げると静かに商品が減り、下げると静かに戻る**
   だけなので、テストでしか守れない。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.stake_allocation import (  # noqa: E402
    MIN_EXPECTED_PAYOUT_7C,
    MIN_EXPECTED_PAYOUT_7S,
    MIN_EXPECTED_PAYOUT_BY_RANK,
    expected_payout_floor,
)

BUDGET = 10_000
SUBMIT = Path(__file__).resolve().parent.parent / "scripts" / "netkeirin_submit_wt.py"


def test_floor_is_the_minimum_of_stake_times_odds():
    """買った目のうち**最低**の想定払戻倍率を返す（平均でも中央でもない）。"""
    stakes = {3: 6000, 4: 2000, 5: 2000}
    odds = {3: 1.5, 4: 8.0, 5: 20.0}          # 0.90 / 1.60 / 4.00 倍
    got = expected_payout_floor(stakes, odds, BUDGET)
    assert got is not None
    assert abs(got - 0.90) < 1e-9


def test_returns_none_when_any_odds_is_missing():
    """🔴 1つでも欠けたら None（＝入稿する側へ倒す）。

    残りだけで最小値を取ると、欠けた目が最低倍率だった場合に
    **実際より高く見積もって素通しする**。
    """
    stakes = {3: 6000, 4: 2000, 5: 2000}
    assert expected_payout_floor(stakes, {3: 1.5, 4: 8.0}, BUDGET) is None
    assert expected_payout_floor(stakes, {3: 1.5, 4: 8.0, 5: 0}, BUDGET) is None
    assert expected_payout_floor(stakes, {}, BUDGET) is None


def test_returns_none_for_empty_or_invalid_budget():
    assert expected_payout_floor({}, {}, BUDGET) is None
    assert expected_payout_floor({3: 100}, {3: 5.0}, 0) is None


def test_threshold_is_the_users_minimum_desired_odds():
    """🔴 閾値は 1.5＝ユーザーの「最低限の希望オッズ」（2026-08-21 方針）。

    **旧テストは 1.0 固定・1.1 超を禁止していた。** その根拠は
    「1.2 以上へ広げると表示的中が 33.1→32.2% に落ちる」だったが、
    ユーザー方針が「的中率そのもの（ガミ込み）には意味が無い・件数は減ってよい」
    へ変わったため、守るべき指標が表示的中ではなくなった。
    予測オッズで測り直した実測は `MIN_EXPECTED_PAYOUT_7C` 定義部を参照。

    **この定数を動かすときは定義部の表を読むこと。**
    """
    assert MIN_EXPECTED_PAYOUT_7C == 1.5
    assert MIN_EXPECTED_PAYOUT_7S == 1.5
    # 2.0 以上は 7C で頭打ち（KPI が戻る一方で件数だけ落ちる）。
    assert MIN_EXPECTED_PAYOUT_7C <= 2.0, "定義部の掃引表では 2.0 超で KPI が頭打ち"


def test_gate_targets_are_measured_ranks_only():
    """🔴 未測定のランクへ黙って広げない。

    足切りを測ったのは **7車の 7C / 7S だけ**。9車や他ランクを足すなら
    同じ掃引をやり直してから。
    """
    assert set(MIN_EXPECTED_PAYOUT_BY_RANK) == {"7C", "7S"}


def _gate_block() -> str:
    src = SUBMIT.read_text(encoding="utf-8")
    i = src.index("min_floor:", src.index("_build_tilted_legs(\n"))
    return src[i - 1400:i + 400]


def test_gate_is_driven_by_the_rank_table():
    """🔴 ランクをハードコードせず `MIN_EXPECTED_PAYOUT_BY_RANK` を引く。

    旧実装は `rank_key == "7C"` 直書きで、7S を足すのに条件式の書き換えが要った。
    表を引く形にしておけば、対象ランクの正本が1箇所に残る。
    """
    block = _gate_block()
    assert "MIN_EXPECTED_PAYOUT_BY_RANK" in block, (
        "足切り対象がランク表から引かれていない")
    assert not re.search(r'rank_key\s*==\s*"7C"', block), (
        "ランクがハードコードされたまま")


def test_gate_prefers_predicted_odds():
    """🔴 判定は**予測オッズ優先**（2026-08-21）。

    実オッズ板は買う点が全部揃うのが 8.9% しかなく、板だけで測るとゲートが
    ほぼ発火しない。7S へ広げるか検討したとき板で判定できたのは12件だけで、
    「7S では効かない」と誤読しかけた。
    """
    src = SUBMIT.read_text(encoding="utf-8")
    i = src.index("def _expected_payout_floor_for(")
    body = src[i:i + 1800]
    assert "try_predicted_odds_for_legs" in body, (
        "想定払戻の判定が予測オッズを使っていない")
    assert body.index("try_predicted_odds_for_legs") < body.index("_load_trio_board"), (
        "実オッズ板を予測オッズより先に使っている（優先順位が逆）")


def test_gate_uses_continue_so_other_ranks_can_take_the_race():
    """🔴 `continue` で抜けること。

    ここで「処理済み」にすると 1レース1商品の取り合いで後続ランクがその
    レースを取れなくなる。落としたいのはそのランクの商品であってレース自体ではない。
    """
    block = _gate_block()
    tail = block[block.index("min_floor:"):]
    assert "continue" in tail, "足切り後に continue していない（レースごと失う）"


def test_gate_skips_when_floor_is_unknown():
    """判定不能（None）なら足切りしない＝出す。"""
    block = _gate_block()
    assert "floor is not None" in block, (
        "板が足りないときに素通しする条件（floor is not None）が無い")
