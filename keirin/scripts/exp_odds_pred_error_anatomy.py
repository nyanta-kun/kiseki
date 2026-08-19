#!/usr/bin/env python3
"""予測オッズの誤差はどこで大きいか（配分に効く形で分解する）・2026-08-19。

## なぜこの形で測るか

配分（`landing_weights`）は **1/オッズ に比例した重み**しか使わない。つまり
効くのは**レース内の相対値だけ**で、倍率の水準は一切効かない。
`logMAE` や `±2倍以内` は水準の指標なので、**配分の良し悪しを測れていない**。

そこで買う目の重みを正規化して比べる:

    w_pred = normalize(1/予測オッズ)   /   w_true = normalize(1/確定オッズ)
    誤差 = Σ|w_pred − w_true|          （0 = 完全一致・最大 2）

到達点は「実質的中 予測 43〜45% → オラクル 51〜54%」で残り約8pt
（`exp_board_vs_predicted_by_time.py`）。この誤差を減らせた分だけ縮む。

## 何を探すか

1. **系統的な偏り**（例: 人気薄の目を一律に過小評価）なら、
   **再学習しなくても単調補正で直せる**。まずこれを見る
2. 偏りが無く分散だけなら、モデルの作り直しが要る

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_odds_pred_error_anatomy.py \
        --from 2026-01-01 --to 2026-08-18
"""
from __future__ import annotations

import argparse
import pickle
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.odds_prediction import build_race_features, load_meta, predict_board  # noqa: E402
from src.p3_calibration import calibrated_p3_sum_top2  # noqa: E402
from src.stake_allocation import allocate_budget  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEGS_MIN, RANK_7C_P3_SUM_MIN, rank_7c_buy_plan,
    rank_7c_is_lowpay_pattern, rank_7c_select_axis, rank_7c_select_legs,
)


def _parse(s):
    return [int(x) for x in re.split(r"[-=>]+", str(s)) if x.strip().isdigit()]


def _norm(d):
    t = sum(d.values())
    return {k: v / t for k, v in d.items()} if t else d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2026-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-18")
    ap.add_argument("--cache", default=None,
                    help="行のキャッシュ（予測盤面の再計算が重いので使い回す）")
    ap.add_argument("--fit-until", default=None, metavar="YYYY-MM-DD",
                    help="この日までで補正を較正し、以降で検証する")
    # 🔴 **本番モデルは train_end=2026-08-04 で評価窓が無い**（2026-08-19 判明）。
    #    過去を採点すると in-sample なので、比較は必ず退避名の honest モデルで行う。
    ap.add_argument("--models", default=None, metavar="名前=接尾辞,…",
                    help="比較する予測モデル（例: level=_lvl2512,centered=_ctr2512）")
    a = ap.parse_args()

    cache = Path(a.cache) if a.cache else None
    if cache and cache.exists():
        rows = pickle.loads(cache.read_bytes())
        print(f"\n[cache] {cache} から {len(rows)}R 読み込み")
        return report(rows, a)

    with get_connection() as conn:
        cur = conn.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct, e.pred_win_pct, "
            "       e.finish_order, e.prediction_mark, e.line_group, "
            "       r.race_type, r.cup_grade, r.start_at, "
            "       e.race_point, e.player_class, e.style, e.line_size, "
            "       e.line_pos, e.is_line_leader, e.first_rate, e.second_rate, "
            "       e.third_rate "
            "FROM wt_entries e JOIN wt_races r USING(race_key) "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 "
            "  AND e.pred_top3_pct IS NOT NULL", (a.d1, a.d2))
        ent, meta = defaultdict(dict), {}
        for (rk, fn, p3, pw, fo, mk, lg, rt, g, sa, rp, pc, st_, ls, lp, ld,
             r1, r2, r3) in cur.fetchall():
            ent[rk][int(fn)] = dict(
                p3=float(p3) / 100.0,
                pw=float(pw) / 100.0 if pw is not None else None,
                fo=fo, mark=mk, lg=lg,
                # 予測盤面の特徴量（`odds_prediction.load_race_inputs` と同じ項目）。
                # 🔴 レースごとに DB を引くと remote RTT で桁違いに遅くなるので
                #    ここで一括取得し、純関数 `predict_board` を直接呼ぶ。
                fm=dict(race_point=rp, mark=mk, player_class=pc, style=st_,
                        line_group=lg, line_size=ls, line_pos=lp,
                        is_line_leader=ld, first_rate=r1, second_rate=r2,
                        third_rate=r3))
            meta[rk] = (rt, g, sa)
        cur = conn.execute(
            "SELECT o.race_key, o.combination, o.odds_value FROM wt_odds o "
            "JOIN wt_races r USING(race_key) WHERE r.race_date BETWEEN ? AND ? "
            "  AND r.n_entries=7 AND o.bet_type='trio' AND o.odds_value>0", (a.d1, a.d2))
        final = defaultdict(dict)
        for rk, cb, od in cur.fetchall():
            final[rk][frozenset(_parse(cb))] = float(od)

    rows = []
    for rk, cars in ent.items():
        if len(cars) != 7 or rk not in final:
            continue
        p3 = {f: v["p3"] for f, v in cars.items()}
        pw = ({f: v["pw"] for f, v in cars.items()}
              if all(v["pw"] is not None for v in cars.values()) else None)
        sel = rank_7c_select_axis(p3)
        if sel is None:
            continue
        a1, a2, _ = sel
        if calibrated_p3_sum_top2(p3, meta[rk][0], meta[rk][1]) < RANK_7C_P3_SUM_MIN:
            continue
        legs_all = rank_7c_select_legs(sorted(set(p3) - {a1, a2}), p3)
        if len(legs_all) < RANK_7C_LEGS_MIN:
            continue
        if rank_7c_is_lowpay_pattern(p3, {f: v["lg"] for f, v in cars.items()}):
            continue
        marks = {v["mark"]: f for f, v in cars.items() if v["mark"]}
        plan = rank_7c_buy_plan(p3, pw, a1, legs_all, wt_ana=marks.get(4))
        if plan is None or plan[0] != "trio":
            continue
        legs = plan[1]
        combos = {t: frozenset({a1, a2, t}) for t in legs}
        if not all(c in final[rk] for c in combos.values()):
            continue
        if any(v["fm"]["race_point"] is None for v in cars.values()):
            continue
        try:
            pb = predict_board(sorted(cars), p3,
                               {f: v["pw"] for f, v in cars.items()},
                               {f: dict(v["fm"], race_point=float(v["fm"]["race_point"]))
                                for f, v in cars.items()},
                               )
        except Exception:
            continue
        pred = {t: pb.get(c) for t, c in combos.items()}
        if not all(pred.values()):
            continue
        fin = {t: final[rk][c] for t, c in combos.items()}
        wp, wt = _norm({t: 1 / pred[t] for t in legs}), _norm({t: 1 / fin[t] for t in legs})
        win = frozenset(f for f, v in cars.items() if v["fo"] in (1, 2, 3))
        rows.append(dict(rk=rk, legs=legs, p3=p3, pred=pred, fin=fin,
                         a1=a1, a2=a2, cars=sorted(cars),
                         pw_all={f: v["pw"] for f, v in cars.items()},
                         meta_cars={f: dict(v["fm"],
                                            race_point=float(v["fm"]["race_point"]))
                                    for f, v in cars.items()},
                         winleg=next((t for t, c in combos.items() if c == win), None),
                         wp=wp, wt=wt, err=sum(abs(wp[t] - wt[t]) for t in legs),
                         meta=meta[rk], n_legs=len(legs),
                         med_pred=statistics.median(pred.values())))

    if cache:
        cache.write_bytes(pickle.dumps(rows))
        print(f"[cache] {cache} へ {len(rows)}R 保存")
    return report(rows, a)


def alt_boards(rows, suffix: str) -> dict[str, dict]:
    """退避名のモデルで予測盤面を作る（本番 meta の target_sum で整合化する）。"""
    import lightgbm as lgb
    import numpy as np

    from src.odds_prediction import FEATURE_NAMES, MODEL_DIR, target_sum

    booster = lgb.Booster(model_file=str(MODEL_DIR / f"odds_trio_n7{suffix}.txt"))
    tsum = target_sum(7)
    out = {}
    for r in rows:
        cars = sorted(r["cars"])
        # `build_race_features` は (組み合わせの並び, 行列) を返す。
        # 🔴 組み合わせの順序は関数側の並びをそのまま使うこと（自前で
        #    itertools.combinations を回して突き合わせると静かにずれる）。
        combos, X = build_race_features(cars, r["p3"], r["pw_all"], r["meta_cars"])
        pred = 10 ** booster.predict(X)
        # 整合化: レース内で Σ(1/o) を目標総和へ合わせる（本番と同じ）。
        pred = pred * (float((1 / pred).sum()) / tsum)
        out[r["rk"]] = {frozenset(c): float(o) for c, o in zip(combos, pred)}
    return out


def report(rows, a):
    print(f"\n7C（三連複・予測と確定が揃う）{len(rows)}R [{a.d1}〜{a.d2}]")
    e = sorted(r["err"] for r in rows)
    print(f"  相対重みの L1 誤差: 中央 {statistics.median(e):.3f} / "
          f"25%点 {e[len(e)//4]:.3f} / 75%点 {e[3*len(e)//4]:.3f} / "
          f"90%点 {e[int(.9*len(e))]:.3f}（0=完全一致・最大2）")

    # --- ① 系統的な偏り: 相手を p3 の強い順に並べ、順位ごとの pred/final 比 ---
    print("\n===== ① 系統的な偏りはあるか（相手を3着内率の強い順に並べる）=====")
    print(f"  {'相手の順位':12}{'R':>7}{'予測/確定 の中央':>17}{'重み比 中央':>14}"
          f"{'過小評価%':>11}")
    byrank = defaultdict(list)
    for r in rows:
        order = sorted(r["legs"], key=lambda t: -r["p3"][t])
        for i, t in enumerate(order):
            byrank[i].append((r["pred"][t] / r["fin"][t], r["wp"][t] / r["wt"][t]))
    for i in sorted(byrank):
        v = byrank[i]
        if len(v) < 50:
            continue
        ratio = statistics.median(x[0] for x in v)
        wratio = statistics.median(x[1] for x in v)
        under = 100 * sum(1 for x in v if x[1] < 1) / len(v)
        print(f"  {'第' + str(i+1) + '相手':12}{len(v):>7}{ratio:>17.3f}"
              f"{wratio:>14.3f}{under:>11.1f}")

    # --- ② 誤差が大きいのはどこか ---
    def seg(name, keyfn, order=None):
        print(f"\n  --- {name} ---")
        d = defaultdict(list)
        for r in rows:
            d[keyfn(r)].append(r["err"])
        keys = order or sorted(d, key=lambda k: (k is None, k))
        for k in keys:
            if k not in d or len(d[k]) < 40:
                continue
            print(f"    {str(k):22}{len(d[k]):>7}  L1中央 {statistics.median(d[k]):.3f}")

    print("\n===== ② 誤差が大きいのはどの区分か（L1 中央）=====")
    seg("買う点数", lambda r: f"{r['n_legs']}点")
    seg("予測オッズの中央値帯", lambda r: ("〜10倍" if r["med_pred"] < 10 else
                                  "10〜30倍" if r["med_pred"] < 30 else
                                  "30〜100倍" if r["med_pred"] < 100 else "100倍〜"))
    seg("開催グレード", lambda r: f"grade={r['meta'][1]}")
    seg("発走時間帯", lambda r: (
        "不明" if not r["meta"][2] else
        f"{(int(r['meta'][2]) + 9*3600) % 86400 // 3600 // 3 * 3:02d}時台〜"))
    seg("レース種別", lambda r: ("決勝系" if r["meta"][0] and "決勝" in str(r["meta"][0])
                             and "準" not in str(r["meta"][0]) else "その他"))

    # --- ③' honest モデル同士を配分の指標で比べる ---
    if a.models:
        specs = [x.split("=", 1) for x in a.models.split(",")]
        print(f"\n===== ③' 予測モデルの比較（配分の指標・{len(rows)}R）=====")
        print(f"    {'':22}{'R':>6}{'素の的中%':>10}{'実質的中%':>11}{'ROI%':>8}"
              f"{'L1中央':>10}{'logMAE':>9}")
        import math
        boards = {name: alt_boards(rows, suf) for name, suf in specs}
        for name, _suf in specs:
            b = boards[name]
            n = hit = net = bet = pay = 0
            errs, lmae = [], []
            for r in rows:
                pr = {t: b[r["rk"]].get(frozenset({r["a1"], r["a2"], t}))
                      for t in r["legs"]}
                if not all(pr.values()):
                    continue
                w = _norm({t: 1 / pr[t] for t in r["legs"]})
                st = allocate_budget({t: 1 / pr[t] for t in r["legs"]}, 10_000)
                bb = sum(st.values())
                n += 1; bet += bb
                errs.append(sum(abs(w[t] - r["wt"][t]) for t in r["legs"]))
                lmae += [abs(math.log10(pr[t] / r["fin"][t])) for t in r["legs"]]
                if r["winleg"] is not None:
                    pp = int(r["fin"][r["winleg"]] * st[r["winleg"]])
                    pay += pp; hit += 1
                    if pp >= bb:
                        net += 1
            print(f"    {name:22}{n:>6}{100*hit/n:>10.1f}{100*net/n:>11.1f}"
                  f"{100*pay/bet:>8.1f}{statistics.median(errs):>10.3f}"
                  f"{statistics.mean(lmae):>9.4f}")
        # 本番（in-sample）も参考に
        n = len(rows)
        e2 = [r["err"] for r in rows]
        print(f"    {'（参考）本番モデル':22}{n:>6}{'':>10}{'':>11}{'':>8}"
              f"{statistics.median(e2):>10.3f}   ※train_end=2026-08-04 で in-sample")
        return 0

    # --- ③ 単調補正を較正窓で作り、検証窓で一度きり検定する ---
    if not a.fit_until:
        print("\n（--fit-until を渡すと補正の検定まで行う）")
        return 0
    fit = [r for r in rows if r["rk"][:8] <= a.fit_until.replace("-", "")]
    test = [r for r in rows if r["rk"][:8] > a.fit_until.replace("-", "")]
    print(f"\n===== ③ 単調補正（較正 {len(fit)}R ≤{a.fit_until} / "
          f"検証 {len(test)}R）=====")
    if len(fit) < 200 or len(test) < 200:
        print("  窓が小さすぎる"); return 0

    # 較正: 相手の順位ごとに「重み比の中央値」を求め、その逆数を掛けて打ち消す
    byrank = defaultdict(list)
    for r in fit:
        order = sorted(r["legs"], key=lambda t: -r["p3"][t])
        for i, t in enumerate(order):
            byrank[i].append(r["wp"][t] / r["wt"][t])
    corr = {i: 1.0 / statistics.median(v) for i, v in byrank.items() if len(v) >= 50}
    print("  較正した補正係数（相手の順位 → 重みに掛ける倍率）: "
          + " / ".join(f"第{i+1} {c:.3f}" for i, c in sorted(corr.items())))

    def score(sub, wfn):
        n = hit = net = 0
        bet = pay = 0
        errs = []
        for r in sub:
            w = wfn(r)
            st = allocate_budget(w, 10_000)
            b = sum(st.values())
            n += 1; bet += b
            wn = _norm(w)
            errs.append(sum(abs(wn[t] - r["wt"][t]) for t in r["legs"]))
            wl = r["winleg"]
            if wl is not None:
                p = int(r["fin"][wl] * st[wl]); pay += p; hit += 1
                if p >= b: net += 1
        print(f"    {name_:22}{n:>6}{100*hit/n:>10.1f}{100*net/n:>11.1f}"
              f"{100*pay/bet:>8.1f}{statistics.median(errs):>10.3f}")

    print(f"    {'':22}{'R':>6}{'素の的中%':>10}{'実質的中%':>11}{'ROI%':>8}{'L1中央':>10}")
    for name_, fn in (
        ("予測オッズ（現行）", lambda r: {t: 1 / r["pred"][t] for t in r["legs"]}),
        ("＋順位の単調補正", lambda r: {
            t: (1 / r["pred"][t]) * corr.get(
                sorted(r["legs"], key=lambda x: -r["p3"][x]).index(t), 1.0)
            for t in r["legs"]}),
        ("確定オッズ（オラクル）", lambda r: {t: 1 / r["fin"][t] for t in r["legs"]}),
    ):
        score(test, fn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
