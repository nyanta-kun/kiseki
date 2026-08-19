"""想定払戻(下限)による 7C の足切りの回帰テスト（2026-08-19 新設）。

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


def test_threshold_is_one_and_not_widened():
    """🔴 閾値は 1.0。**1.1 より上へ動かしてはいけない。**

    1.0〜1.3 の帯は表示的中 41.8% と全帯で最高で、そこを落とすとユーザー条件
    「的中精度は同程度」を破る（実測: >=1.2 で残る側の表示的中 33.1→32.2%）。
    売上（無売上率）だけを見ると 1.5 まで切りたくなるが、売上データは帯あたり
    12〜25件しか無い。**この定数を動かすときは定義部の表を読むこと。**
    """
    assert MIN_EXPECTED_PAYOUT_7C == 1.0
    assert MIN_EXPECTED_PAYOUT_7C <= 1.1, (
        "閾値を 1.1 より上へ広げている。`MIN_EXPECTED_PAYOUT_7C` 定義部の実測表を参照"
    )


def _gate_block() -> str:
    src = SUBMIT.read_text(encoding="utf-8")
    i = src.index("MIN_EXPECTED_PAYOUT_7C:", src.index("_build_tilted_legs(\n"))
    return src[i - 1200:i + 400]


def test_gate_is_scoped_to_7c():
    """🔴 7C 以外へ持ち込まない（測ったのは 7C の三連複だけ）。"""
    assert re.search(r'rank_key\s*==\s*"7C"', _gate_block()), (
        "足切りが 7C に限定されていない")


def test_gate_uses_continue_so_other_ranks_can_take_the_race():
    """🔴 `continue` で抜けること。

    ここで「処理済み」にすると 1レース1商品の取り合いで後続ランクがその
    レースを取れなくなる。落としたいのは 7C の商品であってレース自体ではない。
    """
    block = _gate_block()
    tail = block[block.index("MIN_EXPECTED_PAYOUT_7C:"):]
    assert "continue" in tail, "足切り後に continue していない（レースごと失う）"


def test_gate_skips_when_floor_is_unknown():
    """判定不能（None）なら足切りしない＝出す。"""
    block = _gate_block()
    assert "floor is not None" in block, (
        "板が足りないときに素通しする条件（floor is not None）が無い")
