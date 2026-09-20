"""欠車返還を**全部の採点経路**へ通す（2026-09-20 監査 item2 の続き）。

## なぜ要るか

item2 の修正（PR #593）は

  - `backend/src/api/keirin_router.py`（Web の実売集計）
  - `scripts/settle_type_lab_picks.py`（型ラボの採点）

の2経路に入ったが、**keirin 側の `src/sold_performance.py` を通る経路**——
夜間レビュー・監査後の前向き観測・CLI レポート——は旧来の「全損」のままだった。
同じ商品の投資額が**画面と夜間レビューで食い違う**状態で、これは
2026-08-25 に一度直した「母集団・採点が経路ごとに割れる」型そのもの。

🔴 **食い違っても例外は出ない。** 出るのは ROI がわずかに違う数字だけなので、
   気づくには両方を突き合わせるしかない。だから**呼び出しの形**を検査で固定する。
"""
from __future__ import annotations

import ast
from pathlib import Path

from src.sold_performance import build_sold_races

REPO = Path(__file__).resolve().parent.parent

#: `build_sold_races` を呼ぶ keirin 側のスクリプト（増えたらここも増やす）。
CALLERS = ("audit_watch.py", "nightly_review_type_lab.py", "sold_performance_report.py")


def _calls(path: Path, name: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == name]


def test_売った商品を採点する経路はすべて出走表を渡している():
    """🔴 渡し忘れると欠車ぶんを全損で数え、Web の実売集計と食い違う。"""
    for fname in CALLERS:
        path = REPO / "scripts" / fname
        calls = _calls(path, "build_sold_races")
        assert calls, f"{fname}: build_sold_races の呼び出しが見つからない"
        for call in calls:
            has_kw = any(k.arg == "valid_cars" for k in call.keywords)
            assert has_kw or len(call.args) >= 4, (
                f"{fname}:{call.lineno} が valid_cars を渡していない")


def test_出走表の引き方は共通部品に一本化してある():
    """🔴 「出走している車番」の SQL を経路ごとに書くと、片方だけ直したときに割れる。

    ⚠️ `wt_entries` を引くこと自体は禁じない（着順も同じ表から引く）。
       固定するのは**欠車判定に使う車番の引き方**が1か所であること。
    """
    for fname in (*CALLERS, "settle_type_lab_picks.py"):
        src = (REPO / "scripts" / fname).read_text(encoding="utf-8")
        assert "from src.entrants import valid_cars_by_race" in src, fname
        assert "SELECT race_key, frame_no FROM wt_entries" not in src, (
            f"{fname} が自前で出走車番を引いている（src/entrants.py へ寄せること）")
    assert "SELECT race_key, frame_no FROM wt_entries" in (
        REPO / "src" / "entrants.py").read_text(encoding="utf-8")


def test_採点そのものは正本へ委譲している():
    """`settle_submission` は `valid_cars` を素通しするだけ（判定を書かない）。"""
    src = (REPO / "src" / "sold_performance.py").read_text(encoding="utf-8")
    assert "settle(bet_detail, finishers, payouts, valid_cars=valid_cars)" in src


def _sub(*lines) -> dict:
    return {"race_key": "R1", "race_date": "2026-09-20", "rank_key": "B_hit",
            "bet_detail": {"total": sum(x[1] for x in lines),
                           "lines": [{"bet_type": "3連単", "combo": c, "stake": s}
                                     for c, s in lines]}}


def test_出走表を渡すと欠車ぶんが投資額から落ちる():
    fin = {"R1": [(1, 1), (2, 2), (3, 5)]}
    pay = {"R1": {"1-2-5": 800}}
    got, _ = build_sold_races([_sub(("1-2-4", 3000), ("1-2-5", 2000))], fin, pay,
                              {"R1": {1, 2, 3, 5, 6, 7}})        # 4番が欠車
    assert got[0].bet == 2000
    assert got[0].payout == 16000        # 払戻は変わらない
    assert got[0].hit is True


def test_出走表を渡さなければ従来どおり():
    """🔴 出走表を引けない＝「全員欠車」ではない。fail-safe は「判定しない」側。"""
    fin = {"R1": [(1, 1), (2, 2), (3, 5)]}
    got, _ = build_sold_races([_sub(("1-2-4", 3000), ("1-2-5", 2000))], fin,
                              {"R1": {"1-2-5": 800}})
    assert got[0].bet == 5000
