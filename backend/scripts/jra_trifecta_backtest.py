"""JRA jra_race_ticket 3連複戦略(3F-2軸 / 3F-BOX)の OOS バックテスト

jra_verify_signals.py で「価値系バッジは全てOOS脆弱」と判明したが、3連複券種は
払戻が必要なため未検証だった。buy_signal.py の 3F-2軸(ROI主張3.606)/3F-BOX(4.660)
は is_verified=True でフロント表示されている。実際に trio(3連複) 払戻で決済して
OOS + drop1 + ブートストラップCI で再現するか検証する。

決済:
  - 3連複(trio)払戻 keiba.race_payouts(bet_type='trio', combination='a-b-c' 昇順)
  - ticket_combos の各組が 実際の1-2-3着(順不同) と一致すれば的中・payout/100=倍率
  - ROI = Σ払戻倍率 / Σ点数

使い方:
  cd backend
  .venv/bin/python scripts/jra_trifecta_backtest.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.jra_verify_signals import annotate, fetch_base, fetch_external  # noqa: E402
from src.indices.buy_signal import jra_race_ticket  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_trifecta")


def fetch_trio(conn, race_ids) -> dict[int, list[tuple[frozenset, float]]]:
    """race_id → [(frozenset({a,b,c}), 倍率), ...]。trio 払戻。"""
    if not race_ids:
        return {}
    cur = conn.cursor()
    cur.execute(
        "SELECT race_id, combination, payout FROM keiba.race_payouts "
        "WHERE bet_type='trio' AND race_id = ANY(%s)", (list(race_ids),))
    out: dict[int, list] = {}
    for rid, combo, payout in cur.fetchall():
        try:
            hs = frozenset(int(x) for x in str(combo).split("-"))
        except (TypeError, ValueError):
            continue
        if payout is not None:
            out.setdefault(rid, []).append((hs, float(payout) / 100.0))
    cur.close()
    return out


def _roi_ci(payouts: np.ndarray, points: np.ndarray, rng, n_boot=2000):
    """点数加重 ROI(=Σ払戻/Σ点数) + drop1 + bootstrap95%CI(レース単位リサンプル)。"""
    n = len(payouts)
    if n == 0:
        return {"n": 0, "hit": 0.0, "roi": 0.0, "drop1": 0.0, "lo": 0.0, "hi": 0.0, "pts": 0}
    tot_pts = points.sum()
    roi = payouts.sum() / tot_pts if tot_pts else 0.0
    hit = (payouts > 0).mean() * 100
    if payouts.max() > 0:
        i = int(np.argmax(payouts))
        rem_pts = tot_pts - points[i]
        drop1 = (payouts.sum() - payouts[i]) / rem_pts if rem_pts else 0.0
    else:
        drop1 = roi
    boot = []
    idx = np.arange(n)
    for _ in range(n_boot):
        s = rng.choice(idx, size=n, replace=True)
        pt = points[s].sum()
        boot.append(payouts[s].sum() / pt if pt else 0.0)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"n": n, "hit": hit, "roi": roi, "drop1": drop1, "lo": lo, "hi": hi, "pts": int(tot_pts)}


def backtest(df_ann: pd.DataFrame, trio: dict, rng) -> dict[str, list]:
    """各レースで jra_race_ticket を再現し、3連複 tier を決済。tier別 結果リスト。"""
    rows: dict[str, list] = {"3F-2軸": [], "3F-BOX": []}
    for rid, g in df_ann.groupby("race_id", sort=False):
        g = g.sort_values("composite_index", ascending=False).reset_index(drop=True)
        ranked = [{
            "horse_number": int(r.horse_number) if pd.notna(r.horse_number) else -1,
            "dm_signals": r.dm_signals,
            "purchase_signal": r.purchase_signal,
            "anagusa_rank": r.anagusa_rank,
        } for r in g.itertuples()]
        sweet = [ranked[i] for i, r in enumerate(g.itertuples()) if bool(r.is_sweet_spot)]
        comp = g["composite_index"].tolist()
        wp = g["win_probability"].tolist()
        gap_1_2 = (comp[0] - comp[1]) if len(comp) >= 2 else None
        top2_t3_gap = (comp[1] - comp[2]) if len(comp) >= 3 else None
        wp_sorted = sorted([float(x) for x in wp if pd.notna(x)], reverse=True)
        gap12_prob = (wp_sorted[0] - wp_sorted[1]) if len(wp_sorted) >= 2 else None
        win_prob_rank1 = float(wp[0]) if pd.notna(wp[0]) else None
        course_name = g["course_name"].iloc[0]
        head_count = int(g["head_count"].iloc[0]) if pd.notna(g["head_count"].iloc[0]) else len(g)

        ticket = jra_race_ticket(
            gap_1_2=gap_1_2, gap12_prob=gap12_prob, top2_t3_gap=top2_t3_gap,
            win_prob_rank1=win_prob_rank1, ranked_horses=ranked,
            sweet_horses=sweet, head_count=head_count, course_name=course_name,
        )
        if ticket is None or ticket["bet_type"] != "trifecta":
            continue
        tier = ticket["tier"]
        pays = trio.get(rid, [])
        if not pays:
            continue  # 払戻データ無し(取消等)はスキップ
        win_combo = pays[0][0]  # trio は的中1組のみ。その馬番集合
        win_odds = pays[0][1]
        # ticket_combos のうち的中(=win_combo と一致)があれば payout
        hit = any(frozenset(c) == win_combo for c in ticket["ticket_combos"])
        payout = win_odds if hit else 0.0
        rows[tier].append({"race_id": rid, "payout": payout, "points": ticket["points"]})
    return rows


def report(rows: dict, label: str, rng) -> None:
    print(f"\n--- {label} ---")
    print(f"  {'tier':<10}{'発生R':>6}{'総点数':>8}{'的中R':>7}{'的中率':>8}{'ROI':>8}{'drop1':>8}{'95%CI':>16}{'主張':>8}")
    claims = {"3F-2軸": 3.606, "3F-BOX": 4.660}
    for tier in ["3F-2軸", "3F-BOX"]:
        rs = rows.get(tier, [])
        if not rs:
            print(f"  {tier:<10}{0:>6}")
            continue
        pay = np.array([r["payout"] for r in rs])
        pts = np.array([r["points"] for r in rs], dtype=float)
        st = _roi_ci(pay, pts, rng)
        star = " ★" if st["lo"] > 1.0 else (" ◯" if st["roi"] > 1.0 else "")
        print(f"  {tier:<10}{st['n']:>6}{st['pts']:>8}{int((pay>0).sum()):>7}{st['hit']:>7.1f}%"
              f"{st['roi']:>8.3f}{st['drop1']:>8.3f}  [{st['lo']:.2f},{st['hi']:.2f}]{claims[tier]:>8.3f}{star}")


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--test-start", default="20250701")
    p.add_argument("--end", default="20260605")
    args = p.parse_args()

    rng = np.random.default_rng(12345)
    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    df = fetch_base(conn, args.start, args.end)
    ext = fetch_external(conn, args.start, args.end)
    logger.info("シグナル付与中...")
    df = annotate(df, ext)
    df["date"] = df["date"].astype(str)
    trio = fetch_trio(conn, df["race_id"].unique().tolist())
    conn.close()
    logger.info("trio払戻: %dレース分", len(trio))

    print("\n" + "#" * 80)
    print("# JRA jra_race_ticket 3連複(trio)戦略 OOS バックテスト")
    print("# ★=CI下限>1 / ◯=点推定>1 / 主張は buy_signal.py の roi_basis")
    print("#" * 80)
    full = backtest(df, trio, rng)
    report(full, f"FULL {args.start}-{args.end}", rng)
    test_df = df[df["date"] >= args.test_start]
    test = backtest(test_df, trio, rng)
    report(test, f"OOS test {args.test_start}-{args.end}", rng)
    print("#" * 80)


if __name__ == "__main__":
    main()
