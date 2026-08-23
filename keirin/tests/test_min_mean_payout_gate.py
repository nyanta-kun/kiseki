"""想定払戻の**平均**が安いレースを一括取消する機能の規則を固定する（2026-08-24）。

ユーザー方針「**入稿時点の買い目払戻の平均が 20,000円以下のレースは取り消す**。
リスクに見合わない配当。購入レースが減ることは許容」。

🔴 **自動では落とさない。** 入稿はいったん通し、**レビュー画面のボタンから人が
   確認して一括取消する**（2026-08-24 ユーザー指定）。ダイアログで場名・R・
   平均払戻・チェックボックスを出し、チェックを外せば個別に除外できる。

🔴 この規則は**収支の改善策ではない**。実測（8/08〜8/23 の実入稿 562件を
   `bet_detail` の予測オッズ×実配分で引き直し、実結果で採点）では
   **回収率はほぼ動かない**（72.7% → 71.7%）。的中率だけ下がる（40.6→35.2%）。
   効くのは**投資額が30%減ること**で、1日の損失が 96,012 → 69,265円になる。
   根拠の数字は `src/stake_allocation.MIN_MEAN_PAYOUT` のコメント。

ここで固定するのは4つ:
  1. 判定の式と境界（20,000円ちょうどは**取消対象**＝「以下」）
  2. **オッズが1点でも欠けたら判定しない**（＝残す側へ倒す）
  3. 閾値が **backend / frontend / keirin の3箇所で一致**していること
  4. **自動入稿の経路にゲートが入っていない**こと（人が消す設計を守る）
"""
from __future__ import annotations

import re
from pathlib import Path

from src.stake_allocation import MIN_MEAN_PAYOUT, mean_expected_payout

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent


def test_平均払戻を円で返す():
    # 4,000×5.0 = 20,000 と 2,000×10.0 = 20,000 → 平均 20,000円
    assert mean_expected_payout({1: 4000, 2: 2000}, {1: 5.0, 2: 10.0}) == 20000.0


def test_最低払戻では代用できない():
    """🔴 均等配分では平均と下限が大きく開く。取り違えると別のレースを消す。"""
    from src.stake_allocation import expected_payout_floor

    stakes, odds = {1: 2000, 2: 2000}, {1: 2.0, 2: 30.0}
    assert mean_expected_payout(stakes, odds) == 32000.0        # 平均 32,000円
    assert expected_payout_floor(stakes, odds, 10000) == 0.4    # 下限 0.4倍


def test_境界の2万円ちょうどは取消対象():
    """ユーザー指示は「20000円以下」なので 20,000 は含む。"""
    mean = mean_expected_payout({1: 4000, 2: 2000}, {1: 5.0, 2: 10.0})
    assert mean is not None and mean <= MIN_MEAN_PAYOUT
    mean2 = mean_expected_payout({1: 4000, 2: 2000}, {1: 5.1, 2: 10.0})
    assert mean2 is not None and mean2 > MIN_MEAN_PAYOUT


def test_オッズが欠けたら判定しない():
    """🔴 欠けた目が一番安かった可能性がある。分からないことを理由に消さない。"""
    assert mean_expected_payout({1: 4000, 2: 2000}, {1: 5.0}) is None
    assert mean_expected_payout({1: 4000}, {1: 0}) is None
    assert mean_expected_payout({}, {1: 5.0}) is None


def test_閾値は3箇所で一致する():
    """🔴 写しが増えたので機械的に突き合わせる（`SUBMIT_DEADLINE_SEC` と同じ作法）。"""
    assert MIN_MEAN_PAYOUT == 20_000
    router = (REPO / "backend" / "src" / "api" / "keirin_router.py").read_text("utf-8")
    m = re.search(r"CHEAP_MEAN_PAYOUT\s*=\s*([0-9_]+)", router)
    assert m, "keirin_router.py に CHEAP_MEAN_PAYOUT がありません"
    assert int(m.group(1).replace("_", "")) == MIN_MEAN_PAYOUT


def test_APIが平均払戻と印を返している():
    router = (REPO / "backend" / "src" / "api" / "keirin_router.py").read_text("utf-8")
    assert "def _mean_payout(" in router, "平均払戻の算出が無い"
    assert '"mean_payout": mean_pay' in router, "API が mean_payout を返していない"
    assert '"cheap_mean_payout"' in router, "API が取消候補の印を返していない"
    # 🔴 一部だけで平均を出さない（欠けた点が最安なら候補から漏れる）
    fn = router[router.index("def _mean_payout("):router.index("def _min_payout_low(")]
    assert 'any(x.get("odds") in (None, 0) for x in lines)' in fn


def test_レビュー画面に一括取消の口がある():
    tsx = (REPO / "frontend" / "src" / "app" / "keirin" / "review"
           / "ReviewClient.tsx").read_text("utf-8")
    assert "cheap_mean_payout" in tsx, "画面が API の印を見ていない"
    assert "cancelKeirinPicksAction" in tsx, "一括取消アクションを呼んでいない"
    assert 'type="checkbox"' in tsx, "チェックボックスが無い"
    assert "mean_payout" in tsx, "平均払戻を表示していない"
    # 🔴 画面で閾値を持たない（正本は Python 側）
    assert "20000" not in tsx and "20_000" not in tsx, \
        "画面が閾値を直書きしている（API の印だけを見ること）"


def test_自動入稿にはゲートを入れない():
    """🔴 人が確認して消す設計。自動で落とすと**ダイアログに出す対象が消える**。"""
    sub = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text("utf-8")
    assert "MIN_MEAN_PAYOUT" not in sub, "自動入稿経路に平均払戻のゲートが入っている"
    assert "mean_expected_payout" not in sub


def test_一括取消は1件ずつのAPIを順に呼ぶ():
    """🔴 専用の一括APIを作らない（締切判定・削除・失敗明細を二重に持たない）。"""
    act = (REPO / "frontend" / "src" / "app" / "keirin"
           / "actions.ts").read_text("utf-8")
    fn = act[act.index("export async function cancelKeirinPicksAction("):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert '"/keirin/cancel"' in fn, "既存のレース単位 API を使っていない"
    assert "force: false" in fn, "一括で force を使ってはいけない"
    assert "results.push" in fn, "失敗を明細で返していない"
