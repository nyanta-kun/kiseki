"""想定払戻の**平均**が安いレースを入稿しない規則を固定する（2026-08-24）。

ユーザー方針「**入稿時点の買い目払戻の平均が 20,000円以下のレースは取り消す**。
リスクに見合わない配当。購入レースが減ることは許容」。

🔴 この規則は**収支の改善策ではない**。実測（8/16〜8/23 の実入稿 562件を
   `bet_detail` の予測オッズ×実配分で引き直し、実結果で採点）では
   落ちる側のほうが**的中率は高い**（rank 54.7% ↔ 残す側 36.6%）。
   採る理由は **ガミ率 17.0→14.5%** と **1件あたりの「2万円以上の的中」
   13.98→15.86%** で、方針そのもの（リスクに見合う配当）に沿うこと。
   根拠の数字は `src/stake_allocation.MIN_MEAN_PAYOUT` のコメント。

ここで固定するのは4つ:
  1. 判定そのもの（境界 20,000円ちょうどは**落とす**＝「以下」）
  2. **オッズが1点でも欠けたら判定しない**（＝出す側へ倒す）
  3. 入稿経路がこの判定を通っていること（規則が実装から外れていないこと）
  4. 既存の2ゲート（`MIN_POINT_ODDS` / `expected_payout_floor`）と**別物**であること
"""
from __future__ import annotations

import re
from pathlib import Path

from src.stake_allocation import MIN_MEAN_PAYOUT, mean_expected_payout

ROOT = Path(__file__).resolve().parent.parent


def test_平均払戻を円で返す():
    # 4,000×5.0 = 20,000 と 2,000×10.0 = 20,000 → 平均 20,000円
    assert mean_expected_payout({1: 4000, 2: 2000}, {1: 5.0, 2: 10.0}) == 20000.0


def test_ダッチングでない配分では下限と大きく開く():
    """🔴 下限で代用できないことを値で示す（均等配分の例）。"""
    from src.stake_allocation import expected_payout_floor

    stakes = {1: 2000, 2: 2000}
    odds = {1: 2.0, 2: 30.0}
    assert mean_expected_payout(stakes, odds) == 32000.0          # 平均は 32,000円
    assert expected_payout_floor(stakes, odds, 10000) == 0.4      # 下限は 0.4倍
    # 平均では通るが下限では落ちる＝**両方を通す必要がある**
    assert mean_expected_payout(stakes, odds) > MIN_MEAN_PAYOUT


def test_境界の2万円ちょうどは落とす():
    """ユーザー指示は「20000円以下」なので 20,000 は対象。"""
    mean = mean_expected_payout({1: 4000, 2: 2000}, {1: 5.0, 2: 10.0})
    assert mean is not None and mean <= MIN_MEAN_PAYOUT
    mean2 = mean_expected_payout({1: 4000, 2: 2000}, {1: 5.1, 2: 10.0})
    assert mean2 is not None and mean2 > MIN_MEAN_PAYOUT


def test_オッズが欠けたら判定しない():
    """🔴 欠けた目が一番安かった可能性がある。分からないことを理由に落とさない。"""
    assert mean_expected_payout({1: 4000, 2: 2000}, {1: 5.0}) is None
    assert mean_expected_payout({1: 4000}, {1: 0}) is None
    assert mean_expected_payout({}, {1: 5.0}) is None


def test_既定値は2万円():
    assert MIN_MEAN_PAYOUT == 20_000


def test_入稿経路がこの判定を通っている():
    """🔴 規則が実装から外れると、例外もログも出ずに元の挙動へ戻る。"""
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    assert "mean_expected_payout" in src, "入稿経路が判定を呼んでいない"
    assert re.search(r"_mean\s*=\s*_mean_payout_for", src), "判定の呼び出し形が変わった"
    assert re.search(r"if _mean is not None and _mean <= MIN_MEAN_PAYOUT:\s*\n.*?continue",
                     src, re.S), "該当レースを `continue` で飛ばしていない"


def test_三連単経路には適用しない():
    """⚠️ 予測オッズは三連複しか作れない。実測でも 7T1/7H1/7H2 は該当0件。"""
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    block = src[src.index("_mean_payout_for(\n") - 400:src.index("min_floor = ")]
    assert "if not use_trifecta:" in block, "三連単経路を除外していない"


def test_看板_手動経路にもこの判定が入っている():
    """🔴 2026-08-24 のユーザー判断で**看板にも掛ける**。

    ⚠️ これは「看板レースには必ず推奨を出す」（2026-08-09 決定）を上書きしている。
       実測では落ちる側の profile は看板のほうが悪い
       （ガミ 26.3% / 「2万円以上の的中」4.00%/件）。
    """
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    fn = src[src.index("def _process_manual("):src.index("def _resolve_race_info(")] \
        if "def _resolve_race_info(" in src[src.index("def _process_manual("):] \
        else src[src.index("def _process_manual("):]
    assert "_mean_payout_for" in fn, "看板・手動経路が判定を呼んでいない"
    assert "return 0, []" in fn


def test_ゲートは2経路に入っている():
    """ランク自動入稿と 看板・手動入稿の**両方**で発火すること。"""
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    assert src.count("_mean = _mean_payout_for") == 2, \
        "ゲートが2経路に入っていない（片方だけだと看板が素通りする）"


def test_判定は配分に使ったのと同じ板で行う():
    """⚠️ 配分が予測オッズなら判定も予測オッズ（`_expected_payout_floor_for` と同じ）。"""
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    fn = src[src.index("def _mean_payout_for("):src.index("def _build_trifecta_head_legs(")]
    assert "try_predicted_odds_for_legs" in fn, "予測オッズを優先していない"
    assert "_load_trio_board" in fn, "予測が無いときの板へのフォールバックが無い"
