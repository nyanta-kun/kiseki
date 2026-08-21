#!/usr/bin/env python3
"""7M1 の相手を「予測オッズ × 期待値」で選ぶ（2026-08-21・ユーザー提案）。

## ユーザーの見立て

> 下位から3点固定としているのがよくない。指数・ラインを考慮した相手3点が良い。
> **予想オッズ × 期待値で並べた上位3点**はどうか。

現行は軸2車を除く5車を3着内率の降順に並べ **下位3車を位置で固定**して買う
（`RANK_7M1_LEG_START = 2`）。払戻帯を作るための操作だが、
**どの相手が来やすいかを見ていない**。

    EV_i = 予測オッズ({軸1,軸2,i}) × P({軸1,軸2,i} が3着以内)

で並べ替えれば「市場が安く付けているのに来る」相手を選べる、という提案。
`P` は Plackett-Luce（`odds_prediction._pl_trio`）＝モデルの勝率から出す厳密値。

## 🔴 honest ガード

本番の三連複オッズモデルは `train_end = 2026-08-04` なので 2026年の評価に使うと
in-sample（[[keirin_n7_gami_cut_predicted_odds_2026_08_21]] で実際に踏んだ）。
本スクリプトは `KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816`
（train_end **2025-12-31**）を要求し、**2026-01-01 以降だけ**を評価する。

## 比べる腕

| 腕 | 相手の選び方 |
|---|---|
| `現行(下位3)` | 3着内率の降順で下位3車（位置固定・本番） |
| `EV上位` | **予測オッズ × PL確率** の降順 ← 提案 |
| `オッズ上位` | 予測オッズだけの降順（EV から確率を抜いた対照） |
| `確率上位` | PL確率だけの降順（＝ほぼ指数上位・低配当側の対照） |

🔴 **対照2本を必ず並べる。** EV は両者の積なので、どちらか片方で説明がつくなら
   EV という組み合わせに意味は無い。

## 見る指標

払戻比 = trio払戻 ÷ (100 × 点数)（単位金額は約分で消える）。
**ガミなし的中の件数/日**と**2倍以上の件数/日**を絶対数で見る。ROI は監視のみ。

DB は読み取りのみ。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src import odds_prediction as op  # noqa: E402

import argparse

RANKS = {
    # rank: (rule_version or None = 全世代, 現行の相手選択の説明)
    "RANK_7M1": ("5fc27f1982b0", "3着内率の降順で下位3車（位置固定）"),
    "RANK_7B": (None, "WT△を除外して3着内率の上位3車"),
}
D1, D2 = "2026-01-01", "2026-08-20"
COMBO_RE = re.compile(r"^\s*(\d+)\s*=\s*(\d+)\s*-\s*([\d,]+)")


def load_races(rank: str) -> list[dict]:
    rule = RANKS[rank][0]
    with get_connection() as conn:
        cur = conn.cursor()
        sql = ("SELECT split_part(race_key,'#',1) rk, race_date, pred_combo, trio_payout "
               "FROM picks_history WHERE rank=? AND bet_amount>0 AND trio_payout>0 "
               "  AND race_date BETWEEN ? AND ?")
        args: tuple = (rank, D1, D2)
        if rule:
            sql += " AND rule_version=?"
            args = (rank, D1, D2, rule)
        cur.execute(sql, args)
        picks = {}
        for r in cur:
            m = COMBO_RE.match(r["pred_combo"] or "")
            if m:
                picks[r["rk"]] = dict(
                    date=r["race_date"], a1=int(m.group(1)), a2=int(m.group(2)),
                    legs=[int(x) for x in m.group(3).split(",")],
                    trio=int(r["trio_payout"]))
        keys = list(picks)
        cur.execute("SELECT race_key, frame_no, pred_top3_pct, finish_order, "
                    "       prediction_mark "
                    "FROM wt_entries WHERE race_key = ANY(?)", (keys,))
        ent: dict[str, list[dict]] = defaultdict(list)
        for e in cur:
            ent[e["race_key"]].append(dict(e))

    out = []
    for rk, v in picks.items():
        es = ent.get(rk) or []
        top3 = {int(e["frame_no"]) for e in es if e["finish_order"] in (1, 2, 3)}
        if len(top3) != 3:
            continue
        p3 = {int(e["frame_no"]): float(e["pred_top3_pct"] or 0.0) for e in es}
        others = [f for f in p3 if f not in (v["a1"], v["a2"])]
        if len(others) < 5:
            continue
        ana = next((int(e["frame_no"]) for e in es
                    if e.get("prediction_mark") == 3), None)
        out.append({**v, "rk": rk, "top3": top3, "ana": ana,
                    "ranked": sorted(others, key=lambda f: (-p3[f], f))})
    return out


def bulk_inputs(keys: list[str]) -> dict[str, tuple]:
    """🔴 `load_race_inputs` はレース1本ごとに DB へ往復する（VPS RTT 24.9ms）。
    2,900本で10分を超えたので、**同じ列を1クエリで引いて同じ形に組み直す**。
    列と単位（pct → 0-1）は `odds_prediction._ENTRY_SQL` / `load_race_inputs` に合わせる。
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT race_key, frame_no, race_point, prediction_mark, player_class, "
            "       style, line_group, line_size, line_pos, is_line_leader, "
            "       first_rate, second_rate, third_rate, pred_win_pct, pred_top3_pct "
            "FROM wt_entries WHERE race_key = ANY(?)", (keys,))
        by: dict[str, list[dict]] = defaultdict(list)
        for e in cur:
            by[e["race_key"]].append(dict(e))
    out = {}
    for rk, es in by.items():
        cars, p3, pw, meta = [], {}, {}, {}
        bad = False
        for d in es:
            car = int(d["frame_no"])
            p3v, pwv, rp = d.get("pred_top3_pct"), d.get("pred_win_pct"), d.get("race_point")
            if p3v is None or pwv is None or rp is None:
                bad = True
                break
            cars.append(car)
            p3[car] = float(p3v) / 100.0
            pw[car] = float(pwv) / 100.0
            meta[car] = {
                "race_point": float(rp), "mark": d.get("prediction_mark"),
                "player_class": d.get("player_class"), "style": d.get("style"),
                "line_group": d.get("line_group"), "line_size": d.get("line_size"),
                "line_pos": d.get("line_pos"), "is_line_leader": d.get("is_line_leader"),
                "first_rate": d.get("first_rate"), "second_rate": d.get("second_rate"),
                "third_rate": d.get("third_rate"),
            }
        if not bad and len(cars) in op.SUPPORTED_N_CAR:
            out[rk] = (sorted(cars), p3, pw, meta)
    return out


def enrich(rows: list[dict]) -> list[dict]:
    """各レースに 予測オッズ / PL確率 / EV を付ける。揃わないレースは落とす。"""
    inputs = bulk_inputs([r["rk"] for r in rows])
    ok = []
    for r in rows:
        got = inputs.get(r["rk"])
        if not got:
            continue
        cars, p3, pw, meta = got
        try:
            board = op.predict_board(cars, p3, pw, meta)
            pl = op._pl_trio(pw, cars)
        except Exception:
            continue
        odds, prob, bad = {}, {}, False
        for f in r["ranked"]:
            k = frozenset({r["a1"], r["a2"], f})
            o, p = board.get(k), pl.get(k)
            if not o or o <= 0 or p is None:
                bad = True
                break
            odds[f], prob[f] = float(o), float(p)
        if bad:
            continue
        ok.append({**r, "odds": odds, "prob": prob,
                   "ev": {f: odds[f] * prob[f] for f in odds}})
    return ok


ARMS = {
    # 🔴 現行は**記録された買い目そのもの**を使う（再構成の誤差を持ち込まないため）。
    #    7M1=下位3車固定 / 7B=△除外して上位3車 とランクで規則が違うので、
    #    ここを共通の再構成式で書くと片方が別物になる。
    "現行(記録)": lambda r, n: r["legs"],
    "EV上位": lambda r, n: sorted(r["ranked"], key=lambda f: -r["ev"][f])[:n],
    "オッズ上位": lambda r, n: sorted(r["ranked"], key=lambda f: -r["odds"][f])[:n],
    "確率上位": lambda r, n: sorted(r["ranked"], key=lambda f: -r["prob"][f])[:n],
    # 🔴 7B の現行は「△除外 → 確率上位3」。EV 単独では △除外の働きを再現できて
    #    いなかったので、**EV順に△除外を重ねた**腕を並べる（両方の良いとこ取りが
    #    成立するか）。7M1 には △除外が無いので、この腕は 7B のためのもの。
    "EV上位(△除外)": lambda r, n: [f for f in
        sorted(r["ranked"], key=lambda f: -r["ev"][f]) if f != r.get("ana")][:n],
}


def run(rows: list[dict], pick, n: int) -> dict:
    hit = gami = okn = x2 = 0
    rs: list[float] = []
    ret = 0.0
    for r in rows:
        legs = pick(r, n)
        third = r["top3"] - {r["a1"], r["a2"]}
        if len(third) == 1 and r["a1"] in r["top3"] and r["a2"] in r["top3"] \
                and next(iter(third)) in legs:
            x = r["trio"] / (100.0 * len(legs))
            hit += 1
            rs.append(x)
            ret += x
            gami += x < 1.0
            okn += x >= 1.0
            x2 += x >= 2.0
    rs.sort()
    return dict(hit=hit, gami=gami, ok=okn, x2=x2,
                med=rs[len(rs) // 2] if rs else 0.0,
                roi=100.0 * ret / len(rows) if rows else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", default="RANK_7M1", choices=sorted(RANKS))
    args = ap.parse_args()
    if "odds_model_20260816" not in str(op.MODEL_DIR):
        raise SystemExit(
            "honest モデルを指定してください:\n"
            "  KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816 "
            "PYTHONPATH=. .venv/bin/python scripts/exp_7m1_ev_legs.py")
    op.assert_model_is_honest(D1, who="exp_7m1_ev_legs")

    rows = enrich(load_races(args.rank))
    print(f"{args.rank} {len(rows)}R（{D1}〜{D2}・予測盤面が揃ったもの）")
    print(f"現行の相手選択: {RANKS[args.rank][1]}")
    print(f"モデル: {op.MODEL_DIR}（train_end {op.model_train_end()}）\n")

    for tag, (s, e) in (("掃引 2026-01〜04", ("2026-01-01", "2026-04-30")),
                        ("確認 2026-05〜08", ("2026-05-01", D2))):
        sub = [r for r in rows if s <= r["date"] <= e]
        d = max(len({r["date"] for r in sub}), 1)
        print(f"=== {tag}  {len(sub)}R / {d}日 ===")
        print(f"{'腕':<13}{'点':>3}{'的中':>6}{'的中/日':>8}{'ガミ':>6}"
              f"{'ガミなし/日':>12}{'2倍+/日':>9}{'払戻中央':>9}{'ROI':>8}")
        for name, pick in ARMS.items():
            for n in ((3, 4, 5) if name == "EV上位" else (3,)):
                if name == "現行(記録)" and n != 3:
                    continue
                r = run(sub, pick, n)
                print(f"{name:<13}{n:>3}{r['hit']:>6}{r['hit']/d:>8.2f}"
                      f"{r['gami']:>6}{r['ok']/d:>12.2f}{r['x2']/d:>9.2f}"
                      f"{r['med']:>9.2f}{r['roi']:>7.1f}%")
        print("  --- 差の95%CI（件/日・レース単位ペア bootstrap）---")
        for lab, an, aa, bn, bb in (
                ("EV3 − 現行", "EV上位", 3, "現行(記録)", 3),
                ("EV3 − 確率3", "EV上位", 3, "確率上位", 3),
                ("確率3 − 現行", "確率上位", 3, "現行(記録)", 3),
                ("EV3△除外 − 現行", "EV上位(△除外)", 3, "現行(記録)", 3)):
            for thr, tl in ((1.0, "ガミなし"), (2.0, "2倍+")):
                pt, lo, hi = paired_ci(sub, ARMS[an], aa, ARMS[bn], bb, thr)
                sig = "🟢" if lo > 0 else ("🔴" if hi < 0 else "  ")
                print(f"    {lab:<14}{tl:<8}{pt:+.2f} [{lo:+.2f}, {hi:+.2f}] {sig}")
        print()


def paired_ci(rows, pa, na, pb, nb, thr, n_boot=4000, seed=0):
    """同じレース上の差なので**レース単位のペア復元抽出**で CI を出す。
    🔴 独立2標本の SE で比べてはいけない（同一レース・同一軸で相手だけ違う）。"""
    import random
    rng = random.Random(seed)
    pair = []
    for r in rows:
        third = r["top3"] - {r["a1"], r["a2"]}
        okk = len(third) == 1 and r["a1"] in r["top3"] and r["a2"] in r["top3"]
        t = next(iter(third)) if okk else None
        def val(pick, n):
            if t is None:
                return 0.0
            legs = pick(r, n)
            if t not in legs:
                return 0.0
            return 1.0 if r["trio"] / (100.0 * len(legs)) >= thr else 0.0
        pair.append((val(pa, na), val(pb, nb)))
    d = []
    for _ in range(n_boot):
        s2 = [pair[rng.randrange(len(pair))] for _ in range(len(pair))]
        d.append(sum(a for a, _ in s2) - sum(b for _, b in s2))
    d.sort()
    n_day = len({r["date"] for r in rows})
    return (sum(a - b for a, b in pair) / n_day,
            d[int(.025 * n_boot)] / n_day, d[int(.975 * n_boot)] / n_day)


if __name__ == "__main__":
    main()
