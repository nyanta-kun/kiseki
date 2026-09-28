"""地方「激走 / 見送り」の検証と前向き監視（docs/chihou_rebuild_2026_08.md 18章）。

2段ある。

  discovery : 市場なし walk-forward 指数（`chihou_darkhorse_wf_build.py --no-market`）×
              発走6分前オッズで、空き枠・候補の強さの効き方と閾値の格子を出す。
              **条件探索はここだけで行う**
  forward   : 前向き記録（`chihou.place_picks`・発走前スナップショット）に
              **本番の判定関数 `judge_gekisou` をそのまま**掛けて集計する。
              採否の確認と、以後の監視の両方に使う

⚠️ 2026-08-14〜09-27 の前向き記録は 2026-09-28 のルール確定の確認に消費済み。
   次の確認は 2026-09-28 以降だけで行うこと（同じ窓で条件を選び直さない）。

使い方:
  cd backend
  # 1. walk-forward 予測（約7分・最終四半期の終わりは前向き記録の開始前日に合わせる）
  .venv/bin/python scripts/chihou_darkhorse_wf_build.py --no-market --out /tmp/wf_nomkt.csv
  # 2. 探索
  .venv/bin/python scripts/chihou_gekisou_research.py discovery --wf /tmp/wf_nomkt.csv
  # 3. 前向き集計（監視）
  .venv/bin/python scripts/chihou_gekisou_research.py forward --start 20260928 --end 20261231
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
import warnings
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from src.indices.chihou_gekisou import (  # noqa: E402
    GEKISOU_FAV_TOP,
    GEKISOU_LONGSHOT_POP,
    GEKISOU_MIN_FIELD,
    GEKISOU_ROOM_MIN,
    STATUS_GEKISOU,
    STATUS_MIOKURI,
    harville_top_k,
    judge_gekisou,
)

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

DISCOVERY_START = "20260407"  # odds_history の開始
DISCOVERY_END = "20260813"  # 前向き記録の開始前日

PRE_ODDS_SQL = """
WITH r AS (
  SELECT id, (to_timestamp(date || post_time, 'YYYYMMDDHH24MI') - interval '9 hours') AS post_utc
  FROM chihou.races
  WHERE date BETWEEN %(s)s AND %(e)s AND course <> '83' AND post_time ~ '^[0-9]{4}$'
)
SELECT DISTINCT ON (o.race_id, o.combination)
       o.race_id, o.combination::int AS horse_number, o.odds::float AS pre_win
FROM r JOIN chihou.odds_history o
  ON o.race_id = r.id AND o.bet_type = 'win' AND o.combination ~ '^[0-9]+$'
 AND o.fetched_at <= r.post_utc - interval '6 minutes'
 AND o.fetched_at >= r.post_utc - interval '60 minutes'
ORDER BY o.race_id, o.combination, o.fetched_at DESC
"""

PAYOUT_SQL = """
SELECT p.race_id, p.combination::int AS horse_number, p.payout
FROM chihou.race_payouts p JOIN chihou.races r ON r.id = p.race_id
WHERE p.bet_type = 'place' AND r.date BETWEEN %(s)s AND %(e)s AND p.combination ~ '^[0-9]+$'
"""

FORWARD_SQL = """
SELECT r.date, r.race_id, p.horse_number, p.place_probability, p.pre_win_odds,
       p.finish_position, p.abnormality_code, p.pop_rank, p.index_rank,
       r.top3_share, p.is_picked
FROM chihou.place_pick_races r
JOIN chihou.place_picks p ON p.pick_race_id = r.id
WHERE r.date BETWEEN %(s)s AND %(e)s AND r.settled_at IS NOT NULL
"""


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def boot_ci(x: np.ndarray, groups: np.ndarray, b: int = 2000, seed: int = 0) -> tuple[float, float]:
    """開催日クラスタ bootstrap による平均の95%CI。"""
    x = np.asarray(x, float)
    g = pd.factorize(np.asarray(groups))[0]
    n_g = g.max() + 1
    s = np.bincount(g, weights=x, minlength=n_g)
    c = np.bincount(g, minlength=n_g)
    idx = np.random.default_rng(seed).integers(0, n_g, (b, n_g))
    m = s[idx].sum(1) / np.maximum(c[idx].sum(1), 1)
    lo, hi = np.percentile(m, [2.5, 97.5])
    return float(lo), float(hi)


def _race_table(df: pd.DataFrame) -> pd.DataFrame:
    """1行=1レース。空き枠・候補・結果。df は slots==3 の馬だけ。"""
    fav = df[df["pop"] <= GEKISOU_FAV_TOP]
    races = pd.DataFrame({"date": df.groupby("race_id")["date"].first()})
    races["room"] = 3 - ((fav.q + fav.mq) / 2).groupby(fav.race_id).sum()
    races["fav_minq"] = fav.groupby("race_id")["q"].min()
    lg = (
        df[df["pop"] >= GEKISOU_LONGSHOT_POP]
        .sort_values(["race_id", "q"], ascending=[True, False])
        .groupby("race_id")
        .head(1)
        .set_index("race_id")
    )
    races = races.join(lg[["q", "placed", "ret"]].add_prefix("c_"), how="inner")
    races["upset"] = df[df["pop"] >= GEKISOU_LONGSHOT_POP].groupby("race_id")["placed"].max()
    return races


def run_discovery(wf_path: str) -> None:
    """探索窓で空き枠・候補の強さの効き方と閾値の格子を出す。"""
    wf = pd.read_csv(wf_path, dtype={"date": str})
    wf = wf[(wf.date >= DISCOVERY_START) & (wf.date <= DISCOVERY_END)]
    conn = _connect()
    params = {"s": DISCOVERY_START, "e": DISCOVERY_END}
    pre = pd.read_sql(PRE_ODDS_SQL, conn, params=params)
    pay = pd.read_sql(PAYOUT_SQL, conn, params=params)
    conn.close()

    df = wf.merge(pre, on=["race_id", "horse_number"], how="left").merge(
        pay, on=["race_id", "horse_number"], how="left"
    )
    df = df[df.groupby("race_id")["pre_win"].transform(lambda s: s.notna().all())].copy()
    df["n"] = df.groupby("race_id")["horse_number"].transform("size")
    df = df[df.n >= GEKISOU_MIN_FIELD].copy()
    df["pop"] = df.groupby("race_id")["pre_win"].rank(method="first").astype(int)
    df["q"] = df.composite_wf * 3 / df.groupby("race_id")["composite_wf"].transform("sum")
    df["placed"] = (df.finish_position <= 3).astype(int)
    df["ret"] = np.where(df.placed == 1, df.payout.fillna(0) / 100.0, 0.0)
    mq = [
        pd.Series(harville_top_k(list(1 / g.pre_win.values), 3), index=g.index)
        for _rid, g in df.groupby("race_id", sort=False)
    ]
    df["mq"] = pd.concat(mq)

    races = _race_table(df)
    base = df[df["pop"] >= GEKISOU_LONGSHOT_POP]
    print(f"探索 {DISCOVERY_START}〜{DISCOVERY_END}: {len(races)}R")
    print(f"  人気薄(6番人気以下) 複勝率 {base.placed.mean():.3f} 回収 {base.ret.mean():.3f}")

    races["room_b"] = pd.qcut(races.room, 4)
    races["cq_b"] = pd.qcut(races.c_q, 4)
    for col, name in (("upset", "人気薄が来た率"), ("c_placed", "候補の複勝率"), ("c_ret", "候補の回収")):
        print(f"\n{name}（行=空き枠の四分位 / 列=候補 q の四分位）")
        print(races.pivot_table(index="room_b", columns="cq_b", values=col, aggfunc="mean", observed=True).round(3))

    rows = []
    for r0, q0, push in itertools.product([0, 1.1, 1.2, 1.3, 1.4], [0, 0.25, 0.3, 0.35, 0.4], [False, True]):
        m = (races.room >= r0) & (races.c_q >= q0) & ((races.c_q > races.fav_minq) | (not push))
        s = races[m]
        if len(s) < 80:
            continue
        lo, hi = boot_ci(s.c_placed.values, s.date.values)
        rows.append(
            dict(
                room=r0,
                q0=q0,
                push=push,
                n=len(s),
                cov=len(s) / len(races),
                hit=s.c_placed.mean(),
                hit_lo=lo,
                hit_hi=hi,
                roi=s.c_ret.mean(),
            )
        )
    print("\n閾値の格子")
    print(pd.DataFrame(rows).round(3).to_string(index=False))


def run_forward(start: str, end: str) -> None:
    """前向き記録に本番の判定関数を掛けて集計する。"""
    conn = _connect()
    f = pd.read_sql(FORWARD_SQL, conn, params={"s": start, "e": end})
    pay = pd.read_sql(PAYOUT_SQL, conn, params={"s": start, "e": end})
    conn.close()
    if f.empty:
        print("採点済みの記録がありません")
        return
    f = f.merge(pay, on=["race_id", "horse_number"], how="left")

    rows = []
    for rid, g in f.groupby("race_id"):
        runners = {
            int(h): (p, o)
            for h, p, o in zip(g.horse_number, g.place_probability, g.pre_win_odds, strict=True)
            if pd.notna(o)
        }
        v = judge_gekisou(runners)
        if v.room is None:
            continue
        pop = {int(h): r for h, r in zip(g.horse_number, g.pop_rank, strict=True)}
        longs = g[(g.pre_win_odds.notna()) & g.horse_number.map(pop).ge(GEKISOU_LONGSHOT_POP)]
        ok = longs.abnormality_code.fillna(0) == 0
        upset = bool((longs[ok].finish_position <= 3).any())
        rec = dict(race_id=rid, date=g.date.iloc[0], status=v.status, room=v.room, upset=upset)
        if v.status == STATUS_GEKISOU:
            h = g[g.horse_number == v.pick].iloc[0]
            if (h.abnormality_code or 0) != 0 or pd.isna(h.finish_position):
                rec["status"] = "void"
            else:
                rec["hit"] = int(h.finish_position <= 3)
                rec["prob"] = v.pick_place_prob
                rec["ret"] = (h.payout or 0) / 100.0 if h.finish_position <= 3 else 0.0
        rows.append(rec)
    r = pd.DataFrame(rows)

    base = f[(f.pop_rank >= GEKISOU_LONGSHOT_POP) & (f.abnormality_code.fillna(0) == 0) & f.finish_position.notna()]
    base = base[base.race_id.isin(r.race_id)]
    base_ret = np.where(base.finish_position <= 3, base.payout.fillna(0) / 100.0, 0.0)
    print(f"前向き {start}〜{end}: 判定 {len(r)}R")
    print(
        f"  人気薄(6番人気以下) 複勝率 {(base.finish_position <= 3).mean():.3f}"
        f" 回収 {base_ret.mean():.3f} (n={len(base)})"
    )
    g = r[r.status == STATUS_GEKISOU]
    if len(g):
        lo, hi = boot_ci(g.hit.values, g.date.values)
        rl, rh = boot_ci(g.ret.values, g.date.values)
        print(
            f"  激走   n={len(g)} ({len(g) / len(r):.1%})  複勝的中 {g.hit.mean():.3f}"
            f" [{lo:.3f},{hi:.3f}]  回収 {g.ret.mean():.3f} [{rl:.3f},{rh:.3f}]"
        )
        # 確率（較正済み）の当てはまり。予測と実際がずれてきたら較正のやり直しの合図
        g = g.assign(band=pd.cut(g.prob, [0, 0.2, 0.25, 0.3, 0.35, 1.0]))
        cal = g.groupby("band", observed=True).agg(
            n=("hit", "size"), 予測=("prob", "mean"), 実際=("hit", "mean"), 回収=("ret", "mean")
        )
        print(f"  確率の当てはまり（全体 予測 {g.prob.mean():.3f} ↔ 実際 {g.hit.mean():.3f}）")
        print(cal.round(3).to_string())
    for st, name in ((STATUS_GEKISOU, "激走"), (STATUS_MIOKURI, "見送り"), (None, "印なし")):
        s = r[r.status == st] if st is not None else r[r.status.isna()]
        if len(s):
            print(f"  {name:4s} {len(s):5d}R  人気薄が複勝圏に来た率 {s.upset.mean():.3f}")
    print(
        f"  (閾値: room≥{GEKISOU_ROOM_MIN} / 人気1〜{GEKISOU_FAV_TOP}番 / "
        f"{GEKISOU_LONGSHOT_POP}番人気以下 / {GEKISOU_MIN_FIELD}頭以上)"
    )


def main() -> None:
    """エントリポイント。"""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="stage", required=True)
    d = sub.add_parser("discovery")
    d.add_argument("--wf", required=True, help="chihou_darkhorse_wf_build.py --no-market の出力 CSV")
    fw = sub.add_parser("forward")
    fw.add_argument("--start", required=True)
    fw.add_argument("--end", required=True)
    a = p.parse_args()
    if a.stage == "discovery":
        run_discovery(a.wf)
    else:
        run_forward(a.start, a.end)


if __name__ == "__main__":
    main()
