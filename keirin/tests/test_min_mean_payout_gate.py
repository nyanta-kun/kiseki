"""想定払戻の**平均**が安いレースを一括取消する機能の規則を固定する（2026-08-24）。

ユーザー方針「**入稿時点の買い目払戻の平均が 20,000円以下のレースは取り消す**。
リスクに見合わない配当。購入レースが減ることは許容」。

🔴🔴 **2026-08-24 に自動ゲートへ切り替えた（同日中の二転）。**
   当初は「自動では落とさない。入稿はいったん通し、レビュー画面のボタンから
   人が確認して一括取消する」と決めたが、同日「本日見る限り、手動の安い配当
   取り消しは他同様に自動として良さそう」というユーザー判断で反転した。
   設計の全文は `docs/sales_kpi.md` §11.6。

   ⚠️ **レビュー画面の一括取消 UI は残す**（§11.6.4）。`mean_expected_payout` は
   オッズが1点でも欠けたら None を返して**入稿側へ倒す**ので、朝オッズが
   揃わなかったレースは自動ゲートを素通りする。後から揃えば画面で判定できる。
   自動化後は表示件数が大きく減るはずで、**減らなければ自動ゲートが効いていない
   合図**になる。

🔴 この規則は**収支の改善策ではない**。実測（8/08〜8/23 の実入稿 562件を
   `bet_detail` の予測オッズ×実配分で引き直し、実結果で採点）では
   **回収率はほぼ動かない**（72.7% → 71.7%）。的中率だけ下がる（40.6→35.2%）。
   効くのは**投資額が30%減ること**で、1日の損失が 96,012 → 69,265円になる。
   根拠の数字は `src/stake_allocation.MIN_MEAN_PAYOUT` のコメント。

ここで固定するのは4つ:
  1. 判定の式と境界（20,000円ちょうどは**取消対象**＝「以下」）
  2. **オッズが1点でも欠けたら判定しない**（＝残す側へ倒す）
  3. 閾値が **backend / frontend / keirin の3箇所で一致**していること
  4. **自動入稿の2経路（ランクループ・看板穴埋め）にゲートが入っている**こと
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


def test_ダイアログが判断材料を並べている():
    """🔴 取消の可否を人が決める場なので、既に算出済みのリスク指標を並べる。

    ⚠️ **選定条件は平均払戻の1つだけ**。ここに並ぶ他の列は判断材料であって
       条件ではない。列が増えたときに「これも条件だ」と読まれないよう、
       画面にも但し書きを出している。
    """
    tsx = (REPO / "frontend" / "src" / "app" / "keirin" / "review"
           / "ReviewClient.tsx").read_text("utf-8")
    dlg = tsx[tsx.index("aria-label=\"想定払戻の平均が安いレースの取消\""):]
    for col in ("平均払戻", "最低払戻", "最高払戻"):
        assert f">{col}</th>" in dlg, f"ダイアログに {col} 列が無い"
    # ⚠️ 落車リスクは**載せない**（2026-08-24 ユーザー判断）。取消の判断に使わない
    #    ので列を増やさない（危険帯のほうが ROI は高く、理由にならない）。
    assert ">落車</th>" not in dlg, "落車リスクの列は出さない"
    # 🔴 最低払戻は下振れ側を優先（板由来の min_payout は楽観的）
    assert "p.min_payout_low ?? p.min_payout" in dlg, "下振れ側を優先していない"
    assert "p.gami_risk" in dlg, "ガミの印が無い"
    # ⚠️ 選定条件は1つだけ、と画面にも書いてあること
    assert "選定には使っていません" in dlg


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
    assert "mean_expected_payout" in sub, "判定関数を使っていない"


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

    `mean_expected_payout` が None を返す（＝1点でもオッズが欠ける）とき、
    ゲートは **None を返して素通しする**こと。ここを「落とす」に倒すと、
    朝オッズが揃わない日に入稿が丸ごと消える。
    """
    import scripts.netkeirin_submit_wt as nsw

    assert nsw._mean_payout_too_low({1: 5000, 2: 5000}, {1: 2.0}) is None
    assert nsw._mean_payout_too_low({1: 5000, 2: 5000}, None) is None
    assert nsw._mean_payout_too_low({}, {1: 2.0}) is None
    # 境界: ちょうど 20,000円 は見送る（「以下」）
    assert nsw._mean_payout_too_low({1: 4000, 2: 2000}, {1: 5.0, 2: 10.0}) == 20000.0
    assert nsw._mean_payout_too_low({1: 4000, 2: 2000}, {1: 5.1, 2: 10.0}) is None


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


def test_一括取消は1件ずつのAPIを順に呼ぶ():
    """🔴 専用の一括APIを作らない（締切判定・削除・失敗明細を二重に持たない）。"""
    act = (REPO / "frontend" / "src" / "app" / "keirin"
           / "actions.ts").read_text("utf-8")
    fn = act[act.index("export async function cancelKeirinPicksAction("):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert '"/keirin/cancel"' in fn, "既存のレース単位 API を使っていない"
    assert "force: false" in fn, "一括で force を使ってはいけない"
    assert "results.push" in fn, "失敗を明細で返していない"
