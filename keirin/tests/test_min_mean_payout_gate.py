"""想定払戻の**平均**が安いレースを一括取消する機能の規則を固定する（2026-08-24）。

ユーザー方針「**入稿時点の買い目払戻の平均が 20,000円以下のレースは取り消す**。
リスクに見合わない配当。購入レースが減ることは許容」。

🔴🔴 **2026-08-24 に自動ゲートへ切り替えた（同日中の二転）。**
   当初は「自動では落とさない。入稿はいったん通し、レビュー画面のボタンから
   人が確認して一括取消する」と決めたが、同日「本日見る限り、手動の安い配当
   取り消しは他同様に自動として良さそう」というユーザー判断で反転した。
   設計の全文は `docs/sales_kpi.md` §11.6。

🔴🔴 **2026-08-26 にレビュー画面の一括取消 UI を廃止した**（ユーザー要望
   「入稿データ作成時に自動で行うようにし、ボタン削除」）。同時に、
   **判定を「入稿する買い目そのもの」から作る**ように直した。

   ⚠️ それまでは同じ商品の平均払戻が **2つ存在した**:
     - 入稿ゲート … `try_predicted_odds_for_legs()` の**生値**
     - レビュー画面 … `bet_detail`（予測が無い点は**板**・小数第1位で**丸め**）
     実測 8/25 の松阪2R(7M1) は前者 20,000円超・後者ちょうど 20,000円で、
     自動ゲートを通ったものが画面に取消候補として残っていた。
     いまは両方 `build_bet_lines()` の lines から作るので**構造的に一致する**。

🔴 この規則は**収支の改善策ではない**。実測（8/08〜8/23 の実入稿 562件を
   `bet_detail` の予測オッズ×実配分で引き直し、実結果で採点）では
   **回収率はほぼ動かない**（72.7% → 71.7%）。的中率だけ下がる（40.6→35.2%）。
   効くのは**投資額が30%減ること**で、1日の損失が 96,012 → 69,265円になる。
   根拠の数字は `src/stake_allocation.MIN_MEAN_PAYOUT` のコメント。

ここで固定するのは5つ:
  1. 判定の式と境界（20,000円ちょうどは**見送り対象**＝「以下」）
  2. **オッズが1点でも欠けたら判定しない**（＝出す側へ倒す）
  3. 閾値の**写しがもう無い**こと（正本は `src/stake_allocation` の1箇所だけ）
  4. **自動入稿の2経路（ランクループ・看板穴埋め）にゲートが入っている**こと
  5. **ゲートと記録が同じ関数（`build_bet_lines`）から値を作る**こと
"""
from __future__ import annotations

import re
from pathlib import Path

from src.stake_allocation import (
    MIN_MEAN_PAYOUT,
    mean_expected_payout,
    mean_payout_of_lines,
)

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent


def test_平均払戻を円で返す():
    # 4,000×5.0 = 20,000 と 2,000×10.0 = 20,000 → 平均 20,000円
    assert mean_expected_payout({1: 4000, 2: 2000}, {1: 5.0, 2: 10.0}) == 20000.0


def test_入稿する買い目から平均払戻を出す():
    """`mean_payout_of_lines` は `bet_detail` の lines をそのまま受ける。"""
    assert mean_payout_of_lines(
        [{"stake": 4000, "odds": 5.0}, {"stake": 2000, "odds": 10.0}]) == 20000.0
    # 🔴 1点でも欠けたら判定しない（欠けた点が最安だった可能性がある）
    assert mean_payout_of_lines(
        [{"stake": 4000, "odds": 5.0}, {"stake": 2000, "odds": None}]) is None
    assert mean_payout_of_lines([{"stake": 4000, "odds": 0}]) is None
    assert mean_payout_of_lines([]) is None


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


def test_閾値の写しがもうない():
    """🔴 **正本は `src/stake_allocation.MIN_MEAN_PAYOUT` の1箇所だけ**。

    2026-08-26 に backend / frontend の写しを削除した。写しが増えると
    「入稿ゲートと画面で別の平均払戻を計算する」状態（同日まで実在した）へ
    逆戻りするので、機械的に不在を見張る。
    """
    assert MIN_MEAN_PAYOUT == 20_000
    router = (REPO / "backend" / "src" / "api" / "keirin_router.py").read_text("utf-8")
    assert "CHEAP_MEAN_PAYOUT = " not in router, "API に閾値の写しが復活している"
    assert '"cheap_mean_payout"' not in router, "API が取消候補の印を返している"
    assert '"mean_payout":' not in router, "API が平均払戻を返している"
    api_ts = (REPO / "frontend" / "src" / "lib" / "api.ts").read_text("utf-8")
    assert "cheap_mean_payout:" not in api_ts, "画面の型に印が復活している"


def test_レビュー画面に手動の口がない():
    """🔴 **2026-08-26 に反転**（旧: `test_レビュー画面に一括取消の口がある`）。

    ユーザー要望で「安い配当」ボタンとダイアログを削除し、判定は入稿データを
    作る時点の自動ゲートへ一本化した。

    🔴 **画面から手で落とせる口を作り直さないこと。** 人が消したのかゲートが
       効いたのかが区別できなくなり、`submission_skips` の件数による死活監視
       （§11.6.3・「0件が続いたら壊れている合図」）が濁る。
    """
    tsx = (REPO / "frontend" / "src" / "app" / "keirin" / "review"
           / "ReviewClient.tsx").read_text("utf-8")
    assert "cheap_mean_payout" not in tsx, "画面が取消候補の印を見ている"
    assert "cheapTargets" not in tsx, "取消候補の一覧が復活している"
    assert "cancelKeirinPicksAction" not in tsx, "レース指定の一括取消が復活している"
    act = (REPO / "frontend" / "src" / "app" / "keirin" / "actions.ts").read_text("utf-8")
    assert "export async function cancelKeirinPicksAction(" not in act, \
        "唯一の呼び出し元を消したのにアクションが残っている"


def test_自動入稿の2経路にゲートが入っている():
    """🔴 **2026-08-24 に反転**（旧: `test_自動入稿にはゲートを入れない`）。

    旧テストは「人が確認して消す設計。自動で落とすとダイアログに出す対象が
    消える」を守るため、`netkeirin_submit_wt.py` に `MIN_MEAN_PAYOUT` が
    **無いこと**を固定していた。同日のユーザー判断
    「手動の安い配当取り消しは他同様に自動として良さそう」で不要になった。

    🔴 **2経路とも要る。** `_process_manual()` は看板穴埋めの入口で
    **実入稿の43%**を占める。片方だけだと看板が素通りしてゲートは
    対象の半分以下にしか効かない（§11.6.2）。
    """
    sub = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text("utf-8")
    assert "MIN_MEAN_PAYOUT" in sub, "自動入稿経路に平均払戻のゲートが無い"
    # ランクループと `_process_manual()` の両方から呼ばれていること
    body = sub[sub.index("def _process_rank("):]
    rank_loop = body[:body.index("def _process_manual(")]
    manual = body[body.index("def _process_manual("):]
    assert "_mean_payout_too_low" in rank_loop, "ランク自動入稿ループにゲートが無い"
    assert "_mean_payout_too_low" in manual, "看板穴埋め（_process_manual）にゲートが無い"
    assert "mean_payout_of_lines" in sub, "判定関数を使っていない"


def test_ゲートはcontinueで抜ける():
    """🔴 **`continue` であること。** `break` や「処理済み」にすると、
    1レース1商品の取り合いで**後続ランクがそのレースを取れなくなる**。

    落としたいのはこのランクの商品であって、そのレース自体ではない。
    しかも自動化の売上効果はここに乗っている——安い三連複が落ちた枠を
    後続ランク（7T1/7H1 の三連単）が拾うので、手動取消には無かった
    差し替えが無料で付く（§11.6.1）。**この continue が本体**。
    """
    sub = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text("utf-8")
    body = sub[sub.index("def _process_rank("):sub.index("def _process_manual(")]
    assert re.search(r"if _mean is not None:\s*\n.*?continue", body, re.S), \
        "該当レースを `continue` で飛ばしていない"
    assert not re.search(r"if _mean is not None:\s*\n.*?break", body, re.S), \
        "`break` で抜けている（後続ランクがレースを取れなくなる）"


def test_三連単経路は対象外():
    """⚠️ 予測オッズは三連複しか作れず、実測でも 7T1/7H1/7H2 は該当0件。

    ランクループの判定は `if not use_trifecta:` の中にあること。
    """
    sub = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text("utf-8")
    body = sub[sub.index("def _process_rank("):sub.index("def _process_manual(")]
    i = body.index("_mean_payout_too_low")
    head = body[:i]
    assert head.rstrip().endswith("if not use_trifecta:") or         "if not use_trifecta:" in head[-200:],         "三連単経路を除外していない（use_trifecta のガードが無い）"


def test_オッズ欠けは入稿側へ倒す():
    """🔴 判定できないことを理由に商品を落とさない。

    `mean_payout_of_lines` が None を返す（＝1点でもオッズが欠ける）とき、
    ゲートは **None を返して素通しする**こと。ここを「落とす」に倒すと、
    朝オッズが揃わない日に入稿が丸ごと消える。
    """
    import scripts.netkeirin_submit_wt as nsw

    欠け = [{"stake": 5000, "odds": 4.0}, {"stake": 5000, "odds": None}]
    assert nsw._mean_payout_too_low(欠け) is None
    assert nsw._mean_payout_too_low([]) is None
    # 境界: ちょうど 20,000円 は見送る（「以下」）
    ちょうど = [{"stake": 4000, "odds": 5.0}, {"stake": 2000, "odds": 10.0}]
    assert nsw._mean_payout_too_low(ちょうど) == 20000.0
    上 = [{"stake": 4000, "odds": 5.1}, {"stake": 2000, "odds": 10.0}]
    assert nsw._mean_payout_too_low(上) is None


def test_ゲートと記録は同じ関数から値を作る():
    """🔴🔴 **これが 2026-08-26 の修正の本体**。

    入稿ゲートが見る平均払戻と、`bet_detail` に残って画面へ出る平均払戻は
    **同じ lines から**作ること。別々に作ると、予測オッズが無い点の板
    フォールバックと小数第1位の丸めのぶんだけ食い違い、
    「ゲートは通ったのに画面では取消候補」という商品が残る（実在した）。
    """
    import scripts.netkeirin_submit_wt as nsw

    sub = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text("utf-8")
    # `build_bet_detail` は自前で展開せず `build_bet_lines` を呼ぶこと
    detail = sub[sub.index("def build_bet_detail("):sub.index("def _legs_for_record(")]
    assert "build_bet_lines(" in detail, "記録が展開を自前で持っている"
    assert "for leg in legs:" not in detail, "記録側に展開ループが復活している"
    # ゲートの2経路とも `build_bet_lines(` の戻り値を渡していること
    body = sub[sub.index("def _process_rank("):]
    rank_loop = body[:body.index("def _process_manual(")]
    manual = body[body.index("def _process_manual("):]
    for name, blk in (("ランクループ", rank_loop), ("看板穴埋め", manual)):
        i = blk.index("_mean_payout_too_low(")
        assert "build_bet_lines(" in blk[i:i + 400], \
            f"{name} が入稿する買い目そのものから判定していない"
    # 丸め込みの境界: 予測 2.04倍 は記録では 2.0倍 ＝ 平均ちょうど 20,000円。
    # 実測 8/25 の松阪2R(7M1) がこの形で自動ゲートを素通りしていた。
    lines = nsw.build_bet_lines(
        [nsw.BetLeg(nsw.BET_KIND_TRIO_AXIS2, [[2], [3], [4]], 10000)],
        predicted_odds={frozenset({2, 3, 4}): 2.04})
    assert lines[0]["odds"] == 2.0
    assert mean_payout_of_lines(lines) == 20000.0
    assert nsw._mean_payout_too_low(lines) == 20000.0


def test_見送り件数が可視化されている():
    """🔴 自動化すると入稿自体が行われず `netkeirin_submissions` に痕跡が残らない。

    ログ・実行サマリー・Discord の3つに件数が出ること（§11.6.3）。
    **0件が続いたら壊れている合図**という運用なので、どれか1つでも欠けると
    ゲートが死んでも誰も気づけない。
    """
    sub = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text("utf-8")
    assert "MEAN_PAYOUT_SKIP_TAG" in sub, "ログの目印が無い"
    assert "_mean_payout_skips" in sub, "見送り件数を数えていない"
    assert "安い配当で" in sub, "Discord 通知に見送りの行が無い"
    assert "_mean_payout_skips.clear()" in sub, "実行ごとに数え直していない"
    mq = (ROOT / "scripts" / "submit_marquee_wt.py").read_text("utf-8")
    assert "MEAN_PAYOUT_SKIP_TAG" in mq, "看板側が子プロセスの見送りを数えていない"
    assert "skipped_cheap" in mq


def test_看板の見送りは失敗として数えない():
    """⚠️ 見送りは正常終了（0件・失敗0）。`failed` に入れると毎朝の通知が
    警告で埋まり、`done` に入れると「入稿した」と嘘になる。**別枠**にすること。
    """
    mq = (ROOT / "scripts" / "submit_marquee_wt.py").read_text("utf-8")
    i = mq.index("if MEAN_PAYOUT_SKIP_TAG in p.stdout:")
    blk = mq[i:i + 260]
    assert "skipped_cheap.append" in blk
    assert "failed.append" not in blk.split("elif")[0]


def test_新しいstatus値を足していない():
    """⚠️ `netkeirin_submissions` に `skipped` 等を足すのは不可（§11.6.3）。

    `_already_submitted()` と各所の status フィルタに波及し、
    **オッズが後から取れて再入稿すべきケースを恒久的に塞ぐ**。
    """
    sub = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text("utf-8")
    for bad in ("'skipped'", '"skipped"', "'gated'", '"gated"'):
        assert bad not in sub, f"新しい status 値 {bad} を足している"
