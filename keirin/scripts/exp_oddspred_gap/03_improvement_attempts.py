"""予測オッズの精度を上げる案を測る（2026-08-26・**5案とも不採用**）。

    KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816 \
      PYTHONPATH=. .venv/bin/python scripts/exp_oddspred_gap/03_improvement_attempts.py <案>

案（すべて 2026 を前半/後半へ割って**両向き**で確認する）:
  board  入稿時点で取れている板を配分に使う（現行は予測オッズ単独）
  alloc  配分の重みを「脚ごとの下振れ分位」で較正する
  meta   開催メタ（種別・級・開催日目・場・バンク）と地元フラグを特徴に足す
  fresh  学習終端を新しくする（再学習の頻度に価値があるか）
  clf    足切りを「実際にそうなるか」の分類器へ替える

🔴 clf では **確定オッズ由来の列を特徴に混ぜないこと**。一度 `mof`（確定の最小オッズ）
   を紛れ込ませて「AUC 0.874→0.929・適合率 +7pt」という嘘の改善を出した。
   特徴はホワイトリストで書く。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from _common import CACHE, q  # noqa: E402
from src.odds_prediction import FEATURE_NAMES, target_sum  # noqa: E402
from src.stake_allocation import allocate_budget  # noqa: E402

DS = CACHE / "odds_trio_dataset_n7_db.pkl"
PLAN = CACHE / "oddspred_gap_plan5.pkl"      # 02 が作る
PARAMS = dict(objective="regression", metric="l1", learning_rate=0.05, num_leaves=127,
              min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8,
              bagging_freq=1, verbose=-1, num_threads=8)


def _scored() -> pd.DataFrame:
    """vintage モデルで採点した 2026 の全目（02 と同じ手順）。"""
    import lightgbm as lgb
    b = lgb.Booster(model_file=os.environ["KEIRIN_ODDS_MODEL_DIR"] + "/odds_trio_n7.txt")
    d = pd.read_pickle(DS)
    d = d[d.date >= "2026-01-01"].reset_index(drop=True)
    raw = 10 ** b.predict(d[list(FEATURE_NAMES)])
    d["pred"] = raw * (pd.Series(1 / raw).groupby(d.rk.values).transform("sum").to_numpy()
                       / target_sum(7))
    return d


# ---------------------------------------------------------------- board
def run_board() -> None:
    import re
    from collections import defaultdict
    d = _scored()
    d = d[d.date >= "2026-06-01"]
    plan = d[(d.rk1 == 0) & (d.rk2 == 1)].copy()
    keys = sorted(plan.rk.unique())
    ent = []
    for i in range(0, len(keys), 2000):
        ent += q("SELECT race_key rk, frame_no, pred_top3_pct FROM keirin.wt_entries "
                 "WHERE race_key = ANY(%s)", (keys[i:i + 2000],))
    e = pd.DataFrame([dict(x) for x in ent])
    e["pred_top3_pct"] = pd.to_numeric(e.pred_top3_pct, errors="coerce")
    e = e.dropna(subset=["pred_top3_pct"])
    e["p3rank"] = e.groupby("rk").pred_top3_pct.rank(ascending=False, method="first").astype(int) - 1
    car = {(r.rk, r.p3rank): int(r.frame_no) for r in e.itertuples()}
    plan["combo"] = [frozenset({car.get((r.rk, 0)), car.get((r.rk, 1)), car.get((r.rk, int(r.rk3_)))})
                     for r in plan.itertuples()]
    plan = plan[[len(c) == 3 and None not in c for c in plan.combo]]
    rows = []
    for i in range(0, len(keys), 2000):
        rows += q("SELECT race_key rk, start_at FROM keirin.wt_races WHERE race_key = ANY(%s)",
                  (keys[i:i + 2000],))
    rr = pd.DataFrame([dict(x) for x in rows])
    rr["hour"] = pd.to_datetime(pd.to_numeric(rr.start_at, errors="coerce"), unit="s", utc=True) \
        .dt.tz_convert("Asia/Tokyo").dt.hour
    rr["meeting"] = rr.rk.str[:8] + "_" + rr.rk.str[9:11]
    rr = rr.join(rr.groupby("meeting").hour.min().rename("h1"), on="meeting")
    rr["wave"] = np.where(rr.h1 >= 18, "night（ミッドナイト・18時入稿）",
                          np.where(rr.h1 >= 12, "noon（ナイター・13時入稿）", "morning（7時入稿）"))
    plan = plan.merge(rr[["rk", "wave"]], on="rk", how="left")
    snap = defaultdict(dict)
    SNAP = ("morning", "h10", "h12", "h14", "h18")
    for i in range(0, len(keys), 400):
        for r in q("SELECT race_key, snapshot_type, combination, odds_value "
                   "FROM keirin.wt_odds_snapshot WHERE bet_type='trio' "
                   "AND snapshot_type = ANY(%s) AND race_key = ANY(%s)",
                   (list(SNAP), keys[i:i + 400])):
            c = frozenset(int(x) for x in re.split(r"[-=]", r["combination"]))
            if len(c) == 3:
                snap[(r["snapshot_type"], r["race_key"])][c] = float(r["odds_value"])

    def norm(v):
        w = 1 / np.asarray(v, float)
        return w / w.sum()

    print("入稿時点の板 vs 予測オッズ（配分に効くのは相対値なので重みのL1で見る）")
    for wave, st_ in (("morning（7時入稿）", "morning"), ("noon（ナイター・13時入稿）", "h12"),
                      ("night（ミッドナイト・18時入稿）", "h14"),
                      ("night（ミッドナイト・18時入稿）", "h18")):
        sub = plan[plan.wave == wave]
        rec = []
        for rk, g in sub.groupby("rk", sort=False):
            if len(g) != 5:
                continue
            b = snap.get((st_, rk))
            combos = list(g.combo)
            if not b or any((b.get(c) or 0) <= 0 or b.get(c, 1e9) >= 9000 for c in combos):
                rec.append((False, 0, 0))
                continue
            wf = norm(g.odds.to_numpy())
            rec.append((True, np.abs(norm(g.pred.to_numpy()) - wf).sum(),
                        np.abs(norm([b[c] for c in combos]) - wf).sum()))
        D = pd.DataFrame(rec, columns=["have", "l1_pred", "l1_board"])
        H = D[D.have]
        if len(H) < 30:
            continue
        print(f"  {wave} / {st_} 板  対象 {len(D)}R  5点そろう {100 * D.have.mean():.1f}%"
              f"  → 配分L1 予測 {H.l1_pred.median():.3f} vs 板 {H.l1_board.median():.3f}"
              f"  板が勝つレース {100 * (H.l1_board < H.l1_pred).mean():.1f}%")


# ---------------------------------------------------------------- alloc
def run_alloc() -> None:
    d = _scored()
    plan = d[(d.rk1 == 0) & (d.rk2 == 1)].copy()
    keys = sorted(plan.rk.unique())
    ent = []
    for i in range(0, len(keys), 2000):
        ent += q("SELECT race_key rk, frame_no, pred_top3_pct, finish_order "
                 "FROM keirin.wt_entries WHERE race_key = ANY(%s)", (keys[i:i + 2000],))
    e = pd.DataFrame([dict(x) for x in ent])
    e["pred_top3_pct"] = pd.to_numeric(e.pred_top3_pct, errors="coerce")
    e = e.dropna(subset=["pred_top3_pct"])
    e["p3rank"] = e.groupby("rk").pred_top3_pct.rank(ascending=False, method="first").astype(int) - 1
    car = {(r.rk, r.p3rank): int(r.frame_no) for r in e.itertuples()}
    win = {}
    for rk, g in e.groupby("rk"):
        top = g[g.finish_order.isin([1, 2, 3])]
        if len(top) == 3:
            win[rk] = frozenset(int(x) for x in top.frame_no)
    recs = []
    for rk, g in plan.groupby("rk", sort=False):
        if len(g) != 5 or rk not in win:
            continue
        g = g.sort_values("pred")
        combos = [frozenset({car.get((rk, 0)), car.get((rk, 1)), car.get((rk, int(r)))})
                  for r in g.rk3_]
        if any(len(c) != 3 or None in c for c in combos):
            continue
        recs.append(dict(date=g.date.iloc[0], pred=g.pred.to_numpy(), fin=g.odds.to_numpy(),
                         hit=np.array([c == win[rk] for c in combos])))
    R = pd.DataFrame(recs)
    R["month"] = R.date.str[:7]
    print(f"対象 {len(R)}R（確定着順あり）")

    def run(sub, qvec, tag, budget=10000):
        inv = pay = 0.0
        nhit = disp = big = 0
        fl = []
        for r in sub.itertuples():
            s_ = allocate_budget({i: 1.0 / (r.pred[i] * qvec[i]) for i in range(5)}, budget, 100)
            s = np.array([s_[i] for i in range(5)], float)
            p = float((r.fin * s)[r.hit].sum()) if r.hit.any() else 0.0
            inv += budget
            pay += p
            fl.append((r.fin * s).min() / budget)
            if r.hit.any():
                nhit += 1
                disp += p > budget
                big += p >= 20000
        n = len(sub)
        print(f"  {tag:30s} ROI {100 * pay / inv:5.1f}%  的中 {100 * nhit / n:5.2f}%"
              f"  表示的中 {100 * disp / n:5.2f}%  2万+ {100 * big / n:4.2f}%"
              f"  実現下限中央 {np.median(fl):.2f}")

    for a, b in (("H1", "H2"), ("H2", "H1")):
        fit = R[R.month <= "2026-04"] if a == "H1" else R[R.month > "2026-04"]
        tst = R[R.month <= "2026-04"] if b == "H1" else R[R.month > "2026-04"]
        arr = np.array([r.fin / r.pred for r in fit.itertuples()])
        print(f"\n=== 較正 {a}({len(fit)}R) → 確認 {b}({len(tst)}R) ===")
        run(tst, np.ones(5), "現行（1/予測オッズ）")
        run(tst, np.quantile(arr, 0.5, axis=0), "脚別 中央値較正")
        run(tst, np.quantile(arr, 0.25, axis=0), "脚別 25%点較正")
        run(tst, np.quantile(arr, 0.10, axis=0), "脚別 10%点較正")
        inv = pay = 0.0
        nhit = disp = big = 0
        fl = []
        for r in tst.itertuples():
            s_ = allocate_budget({i: 1.0 / r.fin[i] for i in range(5)}, 10000, 100)
            s = np.array([s_[i] for i in range(5)], float)
            p = float((r.fin * s)[r.hit].sum()) if r.hit.any() else 0.0
            inv += 10000
            pay += p
            fl.append((r.fin * s).min() / 10000)
            if r.hit.any():
                nhit += 1
                disp += p > 10000
                big += p >= 20000
        print(f"  {'オラクル（確定オッズを知る）':30s} ROI {100 * pay / inv:5.1f}%"
              f"  的中 {100 * nhit / len(tst):5.2f}%  表示的中 {100 * disp / len(tst):5.2f}%"
              f"  2万+ {100 * big / len(tst):4.2f}%  実現下限中央 {np.median(fl):.2f}")


# ---------------------------------------------------------------- meta / fresh / clf
def _meta_join(P: pd.DataFrame) -> pd.DataFrame:
    keys = sorted(P.rk.unique())
    rows = []
    for i in range(0, len(keys), 2000):
        rows += q("""SELECT r.race_key rk, r.venue_id, r.grade, r.race_type, r.cup_grade,
                     r.day_index, v.prefecture ven_pref, v.bank_length, v.is_indoor
                     FROM keirin.wt_races r LEFT JOIN keirin.venue_info v
                       ON v.venue_code = r.venue_id WHERE r.race_key = ANY(%s)""",
                  (keys[i:i + 2000],))
    m = pd.DataFrame([dict(x) for x in rows]).set_index("rk")
    ent = []
    for i in range(0, len(keys), 2000):
        ent += q("SELECT race_key rk, frame_no, prefecture, pred_top3_pct "
                 "FROM keirin.wt_entries WHERE race_key = ANY(%s)", (keys[i:i + 2000],))
    e = pd.DataFrame([dict(x) for x in ent])
    e["pred_top3_pct"] = pd.to_numeric(e.pred_top3_pct, errors="coerce")
    e = e.dropna(subset=["pred_top3_pct"])
    e["p3rank"] = e.groupby("rk").pred_top3_pct.rank(ascending=False, method="first").astype(int) - 1
    e = e.join(m[["ven_pref"]], on="rk")
    e["is_local"] = (e.prefecture == e.ven_pref).astype(int)
    loc = e.pivot_table(index="rk", columns="p3rank", values="is_local", aggfunc="first")
    loc.columns = [f"__loc{c}" for c in loc.columns]
    P = P.join(m.drop(columns=["ven_pref"]), on="rk") \
         .join(loc, on="rk").join(e.groupby("rk").is_local.sum().rename("n_local"), on="rk")
    if {"rk1", "rk2", "rk3_"} <= set(P.columns):
        lm = np.nan_to_num(P[[c for c in P.columns if c.startswith("__loc")]].to_numpy())
        idx = P[["rk1", "rk2", "rk3_"]].to_numpy().astype(int)
        take = np.take_along_axis(lm, idx, axis=1)
        P["combo_local"], P["axis_local"] = take.sum(axis=1), take[:, 0]
    P = P.drop(columns=[c for c in P.columns if c.startswith("__loc")])
    for c in ("venue_id", "grade", "race_type"):
        P[c] = P[c].astype("category")
    for c in ("cup_grade", "day_index", "bank_length", "is_indoor"):
        P[c] = pd.to_numeric(P[c], errors="coerce")
    P["n_local"] = P.n_local.fillna(0)
    return P


EXTRA = ["venue_id", "grade", "race_type", "cup_grade", "day_index", "bank_length",
         "is_indoor", "n_local", "combo_local", "axis_local"]


def _plan_metrics(te, pred, ts, tag):
    x = te[["rk", "odds", "rk1", "rk2"]].copy()
    x["pred"] = pred * (pd.Series(1 / pred).groupby(te.rk.values).transform("sum").to_numpy() / ts)
    err = np.log10(x.pred / x.odds)
    plan = x[(x.rk1 == 0) & (x.rk2 == 1)]
    rec = []
    for rk, g in plan.groupby("rk", sort=False):
        if len(g) != 5:
            continue
        p, f = g.pred.to_numpy(), g.odds.to_numpy()
        s_ = allocate_budget({i: 1 / v for i, v in enumerate(p)}, 10000, 100)
        s = np.array([s_[i] for i in range(5)], float)
        wp, wf = (1 / p) / (1 / p).sum(), (1 / f) / (1 / f).sum()
        rec.append(((p * s).mean(), (f * s).mean(), (p * s).min(), (f * s).min(),
                    np.abs(wp - wf).sum()))
    P = pd.DataFrame(rec, columns=["mp", "mf", "flp", "flf", "l1"])
    print(f"  [{tag}] logMAE {err.abs().mean():.4f}  ±2倍 {(err.abs() < np.log10(2)).mean() * 100:.1f}%"
          f"  配分L1 中央 {P.l1.median():.4f}  平均払戻比 {(P.mf / P.mp).median():.3f}"
          f"  最低払戻比 {(P.flf / P.flp).median():.3f}")


def run_meta() -> None:
    import lightgbm as lgb
    d = _meta_join(pd.read_pickle(DS))
    d["y"] = np.log10(d.odds)
    tr, te = d[d.date <= "2025-12-31"], d[d.date > "2025-12-31"]
    ts = float(tr.groupby("rk").odds.apply(lambda s: (1 / s).sum()).mean())
    print(f"学習 {tr.rk.nunique():,}R / 評価 {te.rk.nunique():,}R  target_sum {ts:.4f}")
    for cols, tag in ((list(FEATURE_NAMES), "現行52特徴"),
                      (list(FEATURE_NAMES) + EXTRA, "+開催メタ・地元（62特徴）")):
        b = lgb.train(PARAMS, lgb.Dataset(tr[cols], tr.y), num_boost_round=700)
        _plan_metrics(te, 10 ** b.predict(te[cols]), ts, tag)
        if len(cols) > 52:
            imp = pd.Series(b.feature_importance("gain"), index=cols)
            print("    追加特徴の gain 合計 "
                  f"{100 * imp[EXTRA].sum() / imp.sum():.2f}%")


def run_fresh() -> None:
    import lightgbm as lgb
    d = pd.read_pickle(DS)
    d["y"] = np.log10(d.odds)
    cols = list(FEATURE_NAMES)
    for te_from, ends in (("2026-05-01", ["2025-12-31", "2026-04-30"]),
                          ("2026-07-01", ["2025-12-31", "2026-04-30", "2026-06-30"])):
        te = d[(d.date >= te_from) & (d.date <= "2026-08-20")]
        print(f"\n=== 評価窓 {te_from}〜2026-08-20  {te.rk.nunique():,}R ===")
        for end in ends:
            tr = d[d.date <= end]
            ts = float(tr.groupby("rk").odds.apply(lambda s: (1 / s).sum()).mean())
            b = lgb.train(PARAMS, lgb.Dataset(tr[cols], tr.y), num_boost_round=700)
            _plan_metrics(te, 10 ** b.predict(te[cols]), ts, f"学習終端 {end}（{tr.rk.nunique():,}R）")


def run_clf() -> None:
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    P = pd.read_pickle(PLAN)[["rk", "date", "mp", "mf", "flp", "flf", "spp"]]
    d = _scored().groupby("rk").first()[
        ["rp_mean", "rp_std", "ent_p3", "ent_pw", "pw_max", "p3_max", "p3_sum2",
         "n_lines", "n_solo", "max_line", "rp_gap12", "rp_range"]]
    P = _meta_join(P.join(d, on="rk"))
    # 🔴 予測側だけをホワイトリストで書く（確定オッズ由来の mf / flf を絶対に入れない）
    COLS = ["mp", "flp", "spp", "rp_mean", "rp_std", "ent_p3", "ent_pw", "pw_max", "p3_max",
            "p3_sum2", "n_lines", "n_solo", "max_line", "rp_gap12", "rp_range",
            "venue_id", "grade", "race_type", "cup_grade", "day_index", "bank_length",
            "is_indoor", "n_local"]
    assert not ({"mf", "flf"} & set(COLS))
    P["month"] = P.date.str[:7]
    P["y15"], P["y10"] = (P.flf >= 1.5).astype(int), (P.flf >= 1.0).astype(int)
    P["y2m"] = (P.mf > 20000).astype(int)
    par = dict(objective="binary", metric="auc", learning_rate=0.05, num_leaves=31,
               min_data_in_leaf=100, feature_fraction=0.8, bagging_fraction=0.8,
               bagging_freq=1, verbose=-1, num_threads=8)
    for a, b in (("H1", "H2"), ("H2", "H1")):
        fit = P[P.month <= "2026-04"] if a == "H1" else P[P.month > "2026-04"]
        tst = P[P.month <= "2026-04"] if b == "H1" else P[P.month > "2026-04"]
        print(f"\n=== 学習 {a}({len(fit)}) → 確認 {b}({len(tst)}) ===")
        for tgt, base, label, kind in (("y15", "flp", "下限1.5倍以上になるか", "floor"),
                                       ("y10", "flp", "下限1.0倍以上（ガミ回避）", "floor"),
                                       ("y2m", "mp", "平均払戻2万円超になるか", "mean")):
            m = lgb.train(par, lgb.Dataset(fit[COLS], fit[tgt],
                                           categorical_feature=["venue_id", "grade", "race_type"]), 300)
            p = m.predict(tst[COLS])
            k = int((tst.flp >= 1.5).sum()) if kind == "floor" else int((tst.mp > 20000).sum())
            y = tst[tgt].to_numpy()
            print(f"  {label:24s} AUC 現行 {roc_auc_score(y, tst[base]):.3f} → "
                  f"モデル {roc_auc_score(y, p):.3f}   同件数({k})の適合率 "
                  f"{100 * y[np.argsort(-tst[base].to_numpy())[:k]].mean():.1f}% → "
                  f"{100 * y[np.argsort(-p)[:k]].mean():.1f}%")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"board": run_board, "alloc": run_alloc, "meta": run_meta,
          "fresh": run_fresh, "clf": run_clf}.get(which)
    if not fn:
        raise SystemExit(__doc__)
    fn()
