"""保守倍率を「点数別」にする案の深掘り検証（2026-08-29）。

    KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816 \
      PYTHONPATH=. .venv/bin/python scripts/exp_oddspred_gap/07_floor_ck_verify.py <小題>

小題:
  label   画面が「下側25%分位」と称している数字は実際は何分位か
  shape   c_k はプランの形（安い順 / 軸2車総流し）に依らないか
  month   c_k は月をまたいで安定か
  car     7車 / 9車 / 三連単でどう違うか
  impact  実入稿へ当てたら何件落ちるか・落ちる側は本当に悪いか

前提: 03b_build_resid.py が作る残差キャッシュ（trio 7 / trio 9 / tf）。

## 測っている量

本番は `_conservative_trio_board` が **点数によらず** c=`conservative[p25]`（7車 0.8428）
を掛け、`min(賭け金 × odds_low)` を「下振れしても割らない最低払戻」として
**判定（1.5倍ゲート）・表示（レビュー画面の「下振れ」）・厳選の候補判定**に使う。

`ratio_k = 確定の最低払戻 ÷ 計画の最低払戻` の分布が k で動くので、
**per-point の分位を min-of-k の約束に流用すると k が増えるほど甘くなる**。
本スクリプトはその「甘さ」を分位で言い直し、置き換え候補 c_k を出す。

🔴 **窓の違いと k の効果を混ぜないこと。** 本番の c は**学習窓**で較正されている
   （0.8428）が、ここで測れるのは honest な 2026 だけ（同じ窓の per-point p25 は
   0.8048）。そこで **s_k = p25(min-of-k) / p25(per-point)** という**比**を出し、
   本番定数へ掛ける形で提案する。比なら窓の違いが分子分母で相殺する。
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

from _common import CACHE, q  # noqa: E402
from src.odds_prediction import conservative_multiplier  # noqa: E402
from src.stake_allocation import allocate_budget  # noqa: E402

PATHS = {7: CACHE / "oddspred_resid_trio_2026.pkl",
         9: CACHE / "oddspred_resid_trio9_2026.pkl",
         "tf": CACHE / "oddspred_resid_tf_2026.pkl"}
BUDGET, UNIT = 10_000, 100
KS = (2, 3, 4, 5, 6, 7, 8)


def _load(key=7):
    p = PATHS[key]
    if not p.exists():
        raise SystemExit(f"{p} がありません（03b_build_resid.py を先に実行）")
    d = pd.read_pickle(p)
    d["r"] = d.final / d.pred
    return d


def _ratios(d: pd.DataFrame, k: int, shape: str = "cheap") -> np.ndarray:
    """プランを組んで ratio_k（確定の最低払戻 ÷ 計画の最低払戻）を返す。

    shape:
      cheap    レース内で予測オッズが安い順に k点（形を仮定しない台）
      nagashi  軸=p3上位2車の総流しから、相手を **p3の上位** k人
      nagashi_cheap  同じ総流しから、**予測オッズが安い** k点
    """
    if shape != "cheap":
        srt = np.sort(d[["k1", "k2", "k3"]].to_numpy(), axis=1)
        d = d[(srt[:, 0] == 0) & (srt[:, 1] == 1)].copy()
        d["leg"] = srt[(srt[:, 0] == 0) & (srt[:, 1] == 1)][:, 2]
    out = []
    for _, g in d.groupby("rk", sort=False):
        if shape == "nagashi":
            h = g.nsmallest(k, "leg")
        else:
            h = g.nsmallest(k, "pred")
        if len(h) < k:
            continue
        pred, fin = h.pred.to_numpy(), h.final.to_numpy()
        st = allocate_budget({i: 1.0 / v for i, v in enumerate(pred)}, BUDGET, UNIT)
        s = np.array([st[i] for i in range(k)], float)
        out.append((s * fin).min() / (s * pred).min())
    return np.array(out)


# ---------------------------------------------------------------- label
def run_label() -> None:
    print("画面の「下振れ（下側25%分位）」＝ min(賭け金 × 予測×c) は実際には何分位か\n")
    for key, name in ((7, "三連複7車"), (9, "三連複9車"), ("tf", "三連単7車")):
        d = _load(key)
        c = conservative_multiplier(7 if key == "tf" else key, "p25")
        print(f"  {name}（本番 c={c:.4f}・レース {d.rk.nunique():,}）")
        print("    k   実際が表示を割る確率   ← 25% なら看板どおり")
        for k in KS:
            r = _ratios(d, k)
            print(f"   {k:>2}          {100*(r < c).mean():5.1f}%")


# ---------------------------------------------------------------- shape
def run_shape() -> None:
    d = _load(7)
    base = _ratios(d, 1)
    pp25 = np.quantile(base, .25)
    print(f"三連複7車・レース {d.rk.nunique():,}")
    print(f"1点あたりの p25 = {pp25:.4f}（本番の較正窓での同じ量は {conservative_multiplier(7,'p25'):.4f}）\n")
    print("  k |        安い順k点         |    軸2車総流し(p3上位k)   |  軸2車総流し(安い順k)")
    print("    |  中央    p25    s_k      |  中央    p25    s_k      |  中央    p25    s_k")
    for k in KS:
        cells = []
        for shape in ("cheap", "nagashi", "nagashi_cheap"):
            r = _ratios(d, k, shape)
            if len(r) == 0:
                cells.append("      -      -      -")
                continue
            p25 = np.quantile(r, .25)
            cells.append(f"  {np.median(r):.3f}  {p25:.3f}  {p25/pp25:.3f}")
        print(f" {k:>2} |{cells[0]}   |{cells[1]}   |{cells[2]}")
    print("\ns_k = p25(min-of-k) / p25(1点)。本番定数へ掛ける倍率の候補。")


# ---------------------------------------------------------------- month
def run_month() -> None:
    d = _load(7)
    print("s_k の月次（三連複7車・軸2車総流しの安い順k点）")
    print("  月       n     k=3     k=4     k=5     k=6")
    for m, g in d.groupby(d.date.str[:6]):
        base = _ratios(g, 1)
        if len(base) < 200:
            continue
        pp = np.quantile(base, .25)
        row = []
        for k in (3, 4, 5, 6):
            r = _ratios(g, k, "nagashi_cheap")
            row.append(f"{np.quantile(r,.25)/pp:.3f}" if len(r) else "  -  ")
        print(f"  {m}  {len(base):>5}   " + "   ".join(row))


# ---------------------------------------------------------------- car
def run_car() -> None:
    for key, name in ((7, "三連複7車"), (9, "三連複9車"), ("tf", "三連単7車")):
        d = _load(key)
        base = _ratios(d, 1)
        pp = np.quantile(base, .25)
        print(f"\n{name}（レース {d.rk.nunique():,}・1点p25 {pp:.4f}）")
        print("   k   中央    p25    s_k")
        for k in KS:
            r = _ratios(d, k)
            print(f"  {k:>2}  {np.median(r):.3f}  {np.quantile(r,.25):.3f}  {np.quantile(r,.25)/pp:.3f}")


# ---------------------------------------------------------------- impact
def run_impact(date_from: str = "20260805") -> None:
    """実入稿へ当てる。落ちる商品が本当に「約束を守れない側」かまで見る。

    c_k は **honest 窓で直接測った p25(ratio_k)**。本番の c は学習窓で較正されて
    いる（＝in-sample・同じ量が honest 窓では 0.8428→0.8048）ので、
    置き換えるなら窓も honest 側へ揃えるのが筋。
    """
    # ratio_k は1回だけ作る（分位を変えるたびに作り直すと数十分かかる）
    R = {key: {k: _ratios(_load(key), k, "nagashi_cheap" if k <= 5 else "cheap")
               for k in (1,) + KS} for key in (7, 9)}
    CK = {key: {k: float(np.quantile(v, .25)) for k, v in R[key].items()} for key in R}
    for key in R:
        print(f"c_k({key}車):", {k: round(v, 3) for k, v in CK[key].items()})
    subs = q("""SELECT race_key, rank_key, bet_detail FROM keirin.netkeirin_submissions
                WHERE bet_detail IS NOT NULL AND race_key >= %s ORDER BY race_key""", (date_from,))
    keys = sorted({s["race_key"] for s in subs})
    fin: dict = defaultdict(dict)
    n_car: dict = {}
    for r in q("SELECT race_key, n_entries FROM keirin.wt_races WHERE race_key = ANY(%s)", (keys,)):
        n_car[r["race_key"]] = int(r["n_entries"])
    for i in range(0, len(keys), 400):
        for r in q("SELECT race_key, combination, odds_value FROM keirin.wt_odds "
                   "WHERE bet_type='trio' AND race_key = ANY(%s)", (keys[i:i + 400],)):
            fin[r["race_key"]][frozenset(int(v) for v in re.split(r"[-=]", r["combination"]))] = \
                float(r["odds_value"] or 0)
    rows = []
    for s in subs:
        d = json.loads(s["bet_detail"])
        lines = [x for x in (d.get("lines") or []) if x.get("bet_type") == "3連複"]
        if not lines or lines[0].get("odds_source") != "predicted":
            continue
        board = fin.get(s["race_key"])
        if not board:
            continue
        pts = []
        for ln in lines:
            o, v = ln.get("odds"), board.get(frozenset(
                int(x) for x in re.split(r"[-=]", str(ln["combo"]))))
            if not o or not v:
                pts = []
                break
            pts.append((int(ln["stake"]), float(o), float(v)))
        if not pts:
            continue
        b = int(d.get("total") or sum(x[0] for x in pts))
        rows.append(dict(rank=s["rank_key"], k=len(pts), n_car=n_car.get(s["race_key"], 7),
                         fp=min(p * st for st, p, _ in pts) / b,
                         ff=min(v * st for st, _, v in pts) / b))
    print(f"\n実入稿（{date_from}〜・予測ソースの三連複）{len(rows)}件")

    # (a) 現行ルールは k ごとに約束の強さがバラバラ
    print("\n  (a) 現行の一定倍率で通した商品の「実際に1.5倍以上だった割合」を点数別に")
    byk = defaultdict(list)
    for r in rows:
        if r["fp"] * conservative_multiplier(r["n_car"], "p25") >= 1.5:
            byk[r["k"]].append(r["ff"] >= 1.5)
    for k in sorted(byk):
        if len(byk[k]) >= 5:
            print(f"     {k}点  通す {len(byk[k]):>3}件  達成 {100*np.mean(byk[k]):5.1f}%")
    print("     → 点数が増えるほど約束が守れなくなる＝**同じ看板で強さが違う**")

    # (b) 操作点の一覧（c_k をどの分位で取るか）
    print("\n  (b) c_k をどの分位で取るか（強くするほど通す件数が減る）")
    print("     方式            通す   /日   達成    切った中で実は達成")
    schemes = [("現行 一定 c", None)]
    for qq in (.50, .35, .25, .10):
        schemes.append((f"点数別 c_k(p{int(qq*100):02d})", qq))
    for name, qq in schemes:
        if qq is None:
            cf = lambda r: conservative_multiplier(r["n_car"], "p25")  # noqa: E731
        else:
            tbl = {key: {k: float(np.quantile(v, qq)) for k, v in R[key].items()} for key in R}
            cf = lambda r, t=tbl: t.get(r["n_car"], t[7]).get(r["k"], 0.5)  # noqa: E731
        ps = [r for r in rows if r["fp"] * cf(r) >= 1.5]
        cs = [r for r in rows if r["fp"] * cf(r) < 1.5]
        print(f"     {name:<15} {len(ps):>3}件  {len(ps)/24:4.1f}  "
              f"{100*np.mean([r['ff']>=1.5 for r in ps]) if ps else 0:5.1f}%  "
              f"{100*np.mean([r['ff']>=1.5 for r in cs]) if cs else 0:11.1f}%")

    def _ck(r):
        return CK.get(r["n_car"], CK[7]).get(r["k"], 0.55)

    cur = {id(r) for r in rows if r["fp"] * conservative_multiplier(r["n_car"], "p25") >= 1.5}
    new = {id(r) for r in rows if r["fp"] * _ck(r) >= 1.5}
    drop = [r for r in rows if id(r) in cur - new]
    print(f"\n  (c) c_k(p25) で新たに落ちる {len(drop)}件（{len(drop)/24:.1f}件/日）: "
          f"実際に1.5倍以上だったのは {100*np.mean([r['ff']>=1.5 for r in drop]):.1f}%"
          f"（残る側 {100*np.mean([r['ff']>=1.5 for r in rows if id(r) in new]):.1f}% / "
          f"元から切っていた側 {100*np.mean([r['ff']>=1.5 for r in rows if id(r) not in cur]):.1f}%）")
    by = defaultdict(int)
    for r in drop:
        by[(r["rank"], r["k"])] += 1
    print("     内訳:", dict(sorted(by.items())))


if __name__ == "__main__":
    fn = {"label": run_label, "shape": run_shape, "month": run_month,
          "car": run_car, "impact": run_impact}
    if len(sys.argv) < 2 or sys.argv[1] not in fn:
        raise SystemExit(f"小題を指定してください: {' / '.join(fn)}")
    fn[sys.argv[1]]()
