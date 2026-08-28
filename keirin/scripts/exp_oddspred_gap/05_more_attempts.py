"""予測オッズの精度を上げる案（第2弾・2026-08-29）。**4案とも不採用**。

    KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816 \
      PYTHONPATH=. .venv/bin/python scripts/exp_oddspred_gap/05_more_attempts.py <案>

案:
  shape   レース内の「形」がずれていないか（単調な γ 補正で直せるか）
  pl      車ごとの強さ（Plackett-Luce）へ作り直せるか＝**確定板をPLで再現できるか**
  player  選手ごとの「人気の癖」を特徴に足せるか（前半で推定→後半で検証）
  tilt    点推定でなく**下振れ分位**で配分する（最低払戻を守りに行く）

前提は 03b_build_resid.py が作る残差キャッシュ。
🔴 03（第1弾・板/メタ/再学習/脚別較正/分類器）と合わせて **9案が不採用**。
   同じ案を再び測る前に `docs/oddspred_gap_2026_08_29.md` を読むこと。
"""
from __future__ import annotations

import itertools
import re
import sys

import numpy as np
import pandas as pd

from _common import CACHE, q  # noqa: E402

TRIO = CACHE / "oddspred_resid_trio_2026.pkl"
SPLIT = "20260501"          # 前半=推定 / 後半=検証


def _load():
    if not TRIO.exists():
        raise SystemExit(f"{TRIO} がありません（03b_build_resid.py trio を先に実行）")
    d = pd.read_pickle(TRIO)
    d["r"] = d.final / d.pred
    d["q"] = d.groupby("rk").pred.rank(method="first").astype(int)
    return d


# ---------------------------------------------------------------- shape
def run_shape() -> None:
    """レース内でデミーンした log 同士の回帰の傾き。1 なら形のずれは無い。"""
    d = _load()
    x = np.log10(d.pred) - d.groupby("rk").pred.transform(lambda s: np.log10(s).mean())
    y = np.log10(d.final) - d.groupby("rk").final.transform(lambda s: np.log10(s).mean())
    b = float((x * y).sum() / (x * x).sum())
    print(f"レース内デミーン回帰の傾き b={b:.4f}（1なら形は合っている・相関 {np.corrcoef(x,y)[0,1]:.3f}）")
    print("→ 予測の広がりが 1〜2% 広いだけ（プラン5点だけで測ると 0.984）。単調補正で取り返せる余地は無い。")
    print("\nレース内順位ごとの 確定/予測 の分位")
    print(" 順位      n    p10    p25    中央")
    for k in range(1, 11):
        g = d[d.q == k].r
        print(f" {k:>3} {len(g):>8}  {g.quantile(.10):.3f}  {g.quantile(.25):.3f}  {g.median():.3f}")


# ---------------------------------------------------------------- pl
def run_pl(n_races: int = 400) -> None:
    """**確定**の三連複板を PL（車ごとの強さ7つ）で再現できるか＝作り直しの天井。"""
    from scipy.optimize import minimize
    keys = [r["race_key"] for r in q(
        "SELECT race_key FROM keirin.wt_races WHERE race_date BETWEEN '20260801' AND '20260828' "
        "AND n_entries=7 ORDER BY race_key")][:n_races]
    board: dict = {}
    for i in range(0, len(keys), 300):
        for r in q("SELECT race_key, combination, odds_value FROM keirin.wt_odds "
                   "WHERE bet_type='trio' AND race_key = ANY(%s)", (keys[i:i + 300],)):
            if r["odds_value"]:
                board.setdefault(r["race_key"], {})[
                    tuple(sorted(int(v) for v in re.split(r"[-=]", r["combination"])))] = float(r["odds_value"])
    COMBOS = [tuple(np.array(c) + 1) for c in itertools.combinations(range(7), 3)]
    PERM = np.array([p for c in itertools.combinations(range(7), 3) for p in itertools.permutations(c)])

    def pl(s):
        t = s.sum()
        a, b, c = s[PERM[:, 0]], s[PERM[:, 1]], s[PERM[:, 2]]
        return ((a / t) * (b / (t - a)) * (c / (t - a - b))).reshape(35, 6).sum(1)

    errs = []
    for bd in board.values():
        if len(bd) != 35:
            continue
        y = np.array([1 / bd[c] for c in COMBOS])
        y /= y.sum()
        ly = np.log(y)

        def loss(z):
            p = np.maximum(pl(np.exp(z)), 1e-15)
            p /= p.sum()
            return float(((np.log(p) - ly) ** 2).sum())

        best = minimize(loss, np.zeros(7), method="L-BFGS-B", options=dict(maxiter=800))
        p = np.maximum(pl(np.exp(best.x)), 1e-15)
        p /= p.sum()
        errs.append(np.abs(np.log10(p) - np.log10(y)))
    e = np.concatenate(errs)
    print(f"確定板を PL で再現: n_races={len(errs)}  logMAE {e.mean():.4f}  "
          f"±2倍 {100*(e<np.log10(2)).mean():.1f}%")
    print("（比較）現行の組み合わせ回帰 honest logMAE 0.137 / ±2倍 91.6%")
    print("→ 市場の板は PL で表せない。車ごとに強さを予測して展開する作りは現行より悪くなる。")


# ---------------------------------------------------------------- player
def run_player() -> None:
    """選手ごとの人気の癖。前半で推定 → 後半で検証（配分の指標まで見る）。"""
    d = _load()
    A, B = d[d.date < SPLIT], d[d.date >= SPLIT]
    long = pd.concat([A[[c, "resid"]].rename(columns={c: "p"}) for c in ("p1", "p2", "p3")])
    g = long.groupby("p").resid.agg(["mean", "size"])
    lb = pd.concat([B[[c, "resid"]].rename(columns={c: "p"}) for c in ("p1", "p2", "p3")])
    gb = lb.groupby("p").resid.agg(["mean", "size"])
    m = g.join(gb, lsuffix="_a", rsuffix="_b", how="inner")
    s = m[(m["size_a"] >= 100) & (m["size_b"] >= 100)]
    print(f"出現100点以上の選手 {len(s)}人: 前後半の平均残差の相関 "
          f"r={np.corrcoef(s['mean_a'], s['mean_b'])[0,1]:+.3f}（癖は実在する）")
    sh = {p: g["mean"][p] * g["size"][p] / (g["size"][p] + 200.0) for p in g.index}
    pred = sum(B[c].map(sh).fillna(0) for c in ("p1", "p2", "p3")).to_numpy()
    y = B.resid.to_numpy()
    print(f"後半での説明力: 残差MAE {np.abs(y).mean():.4f} → {np.abs(y-pred).mean():.4f}  "
          f"相関 {np.corrcoef(pred,y)[0,1]:+.4f}（分散の {100*np.corrcoef(pred,y)[0,1]**2:.1f}%）")
    B2 = B.assign(adj=B.pred * (10 ** pred))
    for col in ("pred", "adj"):
        sp, fl = [], []
        for _, gg in B2.groupby("rk"):
            h = gg.nsmallest(5, "pred")
            w = 1 / h[col].to_numpy()
            w /= w.sum()
            pay = w * 10000 * h.final.to_numpy()
            sp.append(pay.max() / pay.min())
            fl.append(pay.min() / 10000)
        print(f"  配分 {col}: 確定払戻 Max/Min 中央 {np.median(sp):.3f}  最低/予算 中央 {np.median(fl):.3f}")
    print("→ 癖は本物だが小さすぎて配分は動かない。")


# ---------------------------------------------------------------- tilt
def run_tilt() -> None:
    """配分を「点推定 1/o」から「下振れ分位 1/(o×c_順位)」へ替える。"""
    d = _load()
    A, B = d[d.date < SPLIT], d[d.date >= SPLIT]
    cq = A.groupby("q").r.quantile(.25).to_dict()
    win: dict = {}
    keys = sorted(B.rk.unique())
    tmp: dict = {}
    for i in range(0, len(keys), 800):
        for r in q("SELECT race_key, frame_no FROM keirin.wt_entries WHERE race_key = ANY(%s) "
                   "AND finish_order BETWEEN 1 AND 3", (keys[i:i + 800],)):
            tmp.setdefault(r["race_key"], set()).add(int(r["frame_no"]))
    win = {k: frozenset(v) for k, v in tmp.items() if len(v) == 3}
    B = B.assign(cb=[frozenset(x) for x in zip(B.c1, B.c2, B.c3)])
    rows = []
    for rk, g in B.groupby("rk"):
        if rk not in win:
            continue
        h = g.nsmallest(5, "pred")
        hit = h.cb.map(lambda x: x == win[rk]).to_numpy()
        fo = h.final.to_numpy()
        for kind in ("現行 1/予測", "下限配分 1/(予測×c_順位)"):
            o = h.pred.to_numpy()
            if kind.startswith("下限"):
                o = o * np.array([cq.get(int(x), .85) for x in h["q"]])
            w = 1 / o
            w /= w.sum()
            st = np.round(w * 100).astype(int) * 100
            st[st.argmax()] += 10000 - st.sum()
            pay = float((st * fo * hit).sum())
            rows.append((kind, pay, int(hit.any()), int(pay > 10000)))
    R = pd.DataFrame(rows, columns=["kind", "pay", "hit", "disp"])
    print(f"後半 {len(R)//2} レース・レース内で安い5点・予算1万円")
    for k, g in R.groupby("kind", sort=False):
        print(f"  {k:<24} 生の的中 {100*g.hit.mean():.2f}%  表示的中 {100*g.disp.mean():.2f}%  "
              f"ROI {100*g.pay.sum()/(10000*len(g)):.1f}%")
    print("→ 最低払戻の中央は上がるが**表示的中は落ちる**（当たりやすい点から金を外すため）。不採用。")


if __name__ == "__main__":
    fn = {"shape": run_shape, "pl": run_pl, "player": run_player, "tilt": run_tilt}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        raise SystemExit(f"案を指定してください: {' / '.join(fn)}")
    fn[sys.argv[1]]()
