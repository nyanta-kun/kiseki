"""RANK_7T2（三連単・一撃枠・ペーパー並走中）の不変条件を固定する。

守りたいのは4つ。どれも壊れても**例外もログも出ない**種類の事故:

1. **入稿されないこと。** ペーパー並走の前提が崩れると、検証していない構成で
   実際に商品が売られる
2. **目標払戻 = 日次上限 × 1レース予算** の自己整合。片方だけ動かすと
   「当たっても日次がプラスにならない」か「当たる回数が足りない」になる
3. **日次上限は日付ごとに掛かること。** バックフィルは月単位で呼ばれるので、
   一括で掛けるとその月ぜんぶで20本になり再構築が本番と別物になる
4. **母集団を絞らないこと。** 決勝系×別ラインへ戻すと本ランクの存在理由が消える
   （回収率100%超の日 36.8% → 26.1%）
"""
from __future__ import annotations

import re
from pathlib import Path

from src.strategy_wt import (
    CURRENT_PAPER_RANKS, RACE_BUDGET, RANK_7T2_DAILY_CAP, RANK_7T2_NE,
    RANK_7T2_TARGET_PAYOUT, rank_7t2_daily_select,
)

REPO = Path(__file__).resolve().parents[1]


def _cand(key: str, date: str, ev: float, *, n_entries: int = RANK_7T2_NE,
          legs: list[str] | None = None, **kw) -> dict:
    return dict(race_key=key, race_date=date, n_entries=n_entries,
                legs=legs if legs is not None else [f"1-2-3"], ev=ev,
                start_time=key, **kw)


def test_目標払戻は日次上限と1レース予算の積である() -> None:
    """🔴 `T = N × 10,000` は設計原理。片方だけ動かしてはいけない。"""
    assert RANK_7T2_TARGET_PAYOUT == RANK_7T2_DAILY_CAP * RACE_BUDGET


def test_7t2は入稿対象に入っていない() -> None:
    """ペーパー並走中なので `netkeirin_submit_wt.RANK_CONFIGS` に無いこと。

    ⚠️ 入稿を始めるときは**このテストを消すのではなく意図ごと書き換える**こと。
    """
    src = (REPO / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    body = src[src.index("RANK_CONFIGS"):]
    assert "RANK_7T2" not in body, "7T2 が入稿対象に入っている（ペーパー並走の前提が壊れる）"


def test_日次上限は日付ごとに掛かる() -> None:
    """月単位で呼ばれても「その月で20本」にならないこと。"""
    cands = [_cand(f"{d}_{i:02d}", d, ev=float(i))
             for d in ("2026-08-20", "2026-08-21")
             for i in range(RANK_7T2_DAILY_CAP + 5)]
    got = rank_7t2_daily_select(cands)
    per_day: dict[str, int] = {}
    for c in got:
        per_day[c["race_date"]] = per_day.get(c["race_date"], 0) + 1
    assert per_day == {"2026-08-20": RANK_7T2_DAILY_CAP,
                       "2026-08-21": RANK_7T2_DAILY_CAP}


def test_上限は期待値の高い順に採る() -> None:
    cands = [_cand(f"r{i:02d}", "2026-08-20", ev=float(i)) for i in range(30)]
    got = rank_7t2_daily_select(cands, daily_cap=3)
    assert {c["race_key"] for c in got} == {"r29", "r28", "r27"}


def test_evを持たない候補も落とさない() -> None:
    """旧形式の候補JSONで商品が全滅しないこと（順位づけでは最下位）。"""
    c = _cand("only", "2026-08-20", ev=0.0)
    del c["ev"]
    assert len(rank_7t2_daily_select([c])) == 1


def test_母集団を決勝系や別ラインで絞らない() -> None:
    """🔴 ここに条件を足すと本ランクの存在理由（母集団の広さ）が消える。"""
    cands = [_cand("a", "2026-08-20", 1.0, race_type="一般", is_cross_line=False),
             _cand("b", "2026-08-20", 2.0, race_type="決勝", is_cross_line=True)]
    assert len(rank_7t2_daily_select(cands)) == 2


def test_7車以外と買い目なしは除外する() -> None:
    cands = [_cand("nine", "2026-08-20", 9.0, n_entries=9),
             _cand("empty", "2026-08-20", 8.0, legs=[]),
             _cand("ok", "2026-08-20", 1.0)]
    assert [c["race_key"] for c in rank_7t2_daily_select(cands)] == ["ok"]


def test_集計対象ランクに登録されている() -> None:
    """登録が漏れると月次/年次サマリーにも live レポートにも出ない。"""
    spec = next((s for s in CURRENT_PAPER_RANKS if s.rank == "RANK_7T2"), None)
    assert spec is not None
    assert spec.suffix == "#7T2" and spec.label == "7T2"
    assert spec.in_header_total is False   # 的中率商品と混ぜない


def test_日次バッチと再構築に登録されている() -> None:
    """候補生成と tail 再構築の両方に登録されていること。

    🔴 どちらが欠けても**静かに記録が残らないだけ**で例外は出ない。
    """
    daily = (REPO / "scripts" / "daily_picks_wt.sh").read_text(encoding="utf-8")
    assert "build_7t2_candidates.py" in daily

    rec = (REPO / "scripts" / "reconcile_walkforward_tail.sh").read_text(encoding="utf-8")
    # ⚠️ コメント中の文字列を拾わないよう **for 行だけ**をパースする
    #    （7H1 で同じ抜けが実際に起きている）。
    line = next(x for x in rec.splitlines() if x.lstrip().startswith("for spec in"))
    assert re.search(r'"7t2:7T2"', line), "reconcile の for 行に 7t2 が無い"

    wc = (REPO / "scripts" / "write_candidates_wt.py").read_text(encoding="utf-8")
    assert "s7t2_candidates.json" in wc and '"RANK_7T2"' in wc
