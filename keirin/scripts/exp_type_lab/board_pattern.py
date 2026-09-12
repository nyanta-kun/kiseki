#!/usr/bin/env python3
"""盤面の組み合わせが「外れの型」を分けるか（2026-09-10）。

記述 → 残差 → 予測力。**単体の量はモデルに入っているので必ず残差で見る。**
残差の層別は2通り:
  ① `axis_sum` 十分位（親の指定）
  ② `p_both` 十分位＝**モデル自身が出す「軸2車そろい確率」**（より厳しい対照）
     `p_both = Σ_{perm に a1,a2 を含む} PROB`
"""
from __future__ import annotations

import itertools
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CANON = list(itertools.permutations(range(1, 8), 3))
PATVARS = ["line_sig", "n_lines", "n_solo", "max_line", "ax_same", "ax_rel",
           "a1_pos", "a1_size", "a2_pos", "a2_size", "m1_pos", "m1_size",
           "mk_same12", "mk_conc", "mk_nlines", "mk_top_is_a1",
           "n_nige", "n_oi", "a1lead_style", "n_nige_lead", "n_nige_solo",
           "n_oi_deputy", "n_dep_stronger", "a1_dep_stronger",
           "nl_top3", "nl_top5", "n35_in_axisline", "agree"]

_D = None


def data():
    global _D
    if _D is None:
        d = pickle.load(open("/tmp/board_pattern.pkl", "rb"))
        z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
        PROB = z["PROB"]
        P3 = z["P3"]
        order = np.argsort(-P3, axis=1) + 1
        a1, a2 = order[:, 0], order[:, 1]
        mask = np.zeros((len(CANON), 8, 8), dtype=bool)
        for t, p in enumerate(CANON):
            for x in p:
                for y in p:
                    if x != y:
                        mask[t, x, y] = True
        pb = np.empty(len(a1))
        for i in range(len(a1)):
            pb[i] = PROB[i][mask[:, a1[i], a2[i]]].sum()
        d["p_both_pl"] = pb
        # 🔴 **対照は本番の確率でなければならない。** 板の `PROB` は PL の周辺確率の積で、
        #    構造上ラインの依存を表現できない（`keirin_tf_line_pair_bonus_2026_08_26`）。
        #    本番が買い目に使うのは `rank_7t3_blend_probs`（λ2.0/μ1.5 の隣接ボーナス込み）
        #    なので、「同ライン」の残差を測る対照はこちらを使う。
        b = np.load("/tmp/p_both_blend.npz")
        d["p_both"] = b["p_both"]
        d["p_a1"] = b["p_a1"]
        r = np.load("/tmp/perm_rank.npz")
        # 決着の並びが、その3車の6並びのうち本番確率で何番目か（1..6）
        d["perm_rank"] = r["perm_rank"]
        # 決着の3車集合が、三連複35点の本番確率で何番目か（1..35）
        d["set_rank"] = r["set_rank"]
        d["order_top"] = (r["perm_rank"] == 1).astype(float)
        d["set_top8"] = (r["set_rank"] <= 8).astype(float)
        # 配当の読み: 決着の確定三連単が100倍以上か（看板の素）
        d["big100"] = (d["pay_tf"] >= 100.0).astype(float)
        _D = d
    return _D


def _dec(x, ref):
    q = np.quantile(ref, np.linspace(0, 1, 11)[1:-1])
    return np.digitize(x, q)


def resid(d, sel, var, strat, y):
    """(level -> (n, obs%, exp%, resid pt))。exp は層別直接標準化。"""
    lv, st, yy = d[var][sel], strat[sel], y[sel]
    base = {s: yy[st == s].mean() for s in np.unique(st)}
    out = {}
    for v in np.unique(lv):
        m = lv == v
        if m.sum() == 0:
            continue
        exp = np.mean([base[s] for s in st[m]])
        out[v] = (int(m.sum()), 100 * yy[m].mean(), 100 * exp,
                  100 * (yy[m].mean() - exp))
    return out


def desc(target: str = "both_in3", control: str = "p_both") -> None:
    d = data()
    ok = d["ok"]
    y = d[target].astype(float) if d[target].dtype == bool else d[target].astype(float)
    ex = ok & (d["win"] == "explore")
    cf = ok & (d["win"] == "confirm")
    ctl = _dec(d[control], d[control][ex])
    print(f"# target={target} control={control} (十分位で直接標準化)")
    print(f"  explore n={ex.sum()} 素の{target}={100*y[ex].mean():.2f}% / "
          f"confirm n={cf.sum()} {100*y[cf].mean():.2f}%\n")
    arms = surv = 0
    rows = []
    for var in PATVARS:
        re_ = resid(d, ex, var, ctl, y)
        rc = resid(d, cf, var, ctl, y)
        for v in sorted(set(re_) & set(rc), key=str):
            ne, oe, ee, de = re_[v]
            nc, oc, ec, dc = rc[v]
            if ne < 300 or nc < 300:
                continue
            arms += 1
            same = (de > 0) == (dc > 0)
            big = min(abs(de), abs(dc)) >= 1.5
            if same and big:
                surv += 1
            rows.append((var, str(v), ne, oe, de, nc, oc, dc, same and big))
    rows.sort(key=lambda r: -min(abs(r[4]), abs(r[7])))
    print(f"{'変数':<16}{'水準':<10}{'n探':>7}{'素%':>7}{'残差':>7}"
          f"{'n確':>7}{'素%':>7}{'残差':>7}  生存")
    for r in rows:
        print(f"{r[0]:<16}{r[1]:<10}{r[2]:>7}{r[3]:>7.2f}{r[4]:>+7.2f}"
              f"{r[5]:>7}{r[6]:>7.2f}{r[7]:>+7.2f}  {'*' if r[8] else ''}")
    print(f"\n腕(セル) {arms} 本中 両窓同符号かつ |残差|>=1.5pt: {surv} 本")





# ───────────────────────── 予測力 ─────────────────────────

CAT = ["line_sig", "ax_rel", "a1lead_style"]
NUM = ["n_lines", "n_solo", "max_line", "ax_same", "a1_pos", "a1_size",
       "a2_pos", "a2_size", "m1_pos", "m1_size", "mk_same12", "mk_conc",
       "mk_nlines", "mk_top_is_a1", "n_nige", "n_oi", "n_ryo", "n_nige_lead",
       "n_nige_solo", "n_oi_deputy", "n_dep_stronger", "a1_dep_stronger",
       "nl_top3", "nl_top5", "n35_in_axisline", "agree"]


def _mat(d, sel, cols, cats):
    xs = [d[c][sel].astype(float) for c in cols]
    for c in cats:
        v = d[c][sel]
        for lv in sorted(set(d[c].tolist())):
            xs.append((v == lv).astype(float))
    return np.column_stack(xs) if xs else np.zeros((int(sel.sum()), 0))


def _auc(y, s):
    o = np.argsort(s)
    y = y[o]
    r = np.empty(len(y))
    i = 0
    while i < len(y):
        j = i
        while j + 1 < len(y) and s[o][j + 1] == s[o][i]:
            j += 1
        r[i:j + 1] = (i + j) / 2 + 1
        i = j + 1
    n1 = y.sum()
    n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def auc(target: str = "both_in3", extra: str = "") -> None:
    import lightgbm as lgb
    d = data()
    ok = d["ok"]
    if target in ("order_top", "third_ok", "set_top8"):
        pass
    if target == "third_ok":          # 軸2そろい時に3着が p3 4位以内か（相手側）
        ok = ok & d["both_in3"]
        y = (d["third_rank"] <= 4).astype(int)
    else:
        y = d[target].astype(int)
    ex = ok & (d["win"] == "explore")
    cf = ok & (d["win"] == "confirm")
    base = ["p_both"] if extra != "axis" else ["axis_sum"]
    print(f"# target={target} n_explore={ex.sum()} n_confirm={cf.sum()} "
          f"base_rate {100*y[ex].mean():.2f}/{100*y[cf].mean():.2f}%")
    arms = [("axis_sum 単体", ["axis_sum"], []),
            ("p_both 単体（本番確率）", ["p_both"], []),
            ("rp_sd 単体", ["rp_sd"], []),
            ("盤面パターンのみ", NUM, CAT),
            ("p_both + 盤面パターン", base + NUM, CAT)]
    for name, cols, cats in arms:
        Xe, Xc = _mat(d, ex, cols, cats), _mat(d, cf, cols, cats)
        if Xe.shape[1] == 1:
            se, sc = Xe[:, 0], Xc[:, 0]
        else:
            m = lgb.train(dict(objective="binary", learning_rate=0.05,
                               num_leaves=15, min_data_in_leaf=200,
                               feature_fraction=0.8, bagging_fraction=0.8,
                               bagging_freq=1, verbose=-1, seed=7),
                          lgb.Dataset(Xe, label=y[ex]), num_boost_round=300)
            se, sc = m.predict(Xe), m.predict(Xc)
        print(f"  {name:<28} AUC 探索(in-sample) {_auc(y[ex], se):.4f}"
              f"   確認(OOS) {_auc(y[cf], sc):.4f}")





# ───────────────────────── 交差（組み合わせの組み合わせ）─────────────────────────

XVARS = ["ax_same", "a1_pos", "a1_size", "a2_pos", "mk_conc", "mk_top_is_a1",
         "n_nige", "n_dep_stronger", "nl_top3", "n35_in_axisline",
         "line_sig", "a1lead_style", "n_oi_deputy", "n_nige_lead", "agree"]


def cross(target: str = "both_in3", control: str = "p_both",
          thr: float = 3.0) -> None:
    """2変数の交差セルの残差。**n>=300 を両窓で満たすセルだけ**を腕として数える。"""
    d = data()
    ok = d["ok"]
    if target == "third_ok":
        ok = ok & d["both_in3"]
        y = (d["third_rank"] <= 4).astype(float)
    else:
        y = d[target].astype(float)
    ex, cf = ok & (d["win"] == "explore"), ok & (d["win"] == "confirm")
    ctl = _dec(d[control], d[control][ex])
    base_e = {s: y[ex & (ctl == s)].mean() for s in np.unique(ctl)}
    base_c = {s: y[cf & (ctl == s)].mean() for s in np.unique(ctl)}
    rows, arms, surv = [], 0, 0
    for i in range(len(XVARS)):
        for j in range(i + 1, len(XVARS)):
            a, b = XVARS[i], XVARS[j]
            lab = np.char.add(np.char.add(d[a].astype(str), "|"),
                              d[b].astype(str))
            for v in np.unique(lab):
                me, mc = ex & (lab == v), cf & (lab == v)
                ne, nc = int(me.sum()), int(mc.sum())
                if ne < 300 or nc < 300:
                    continue
                arms += 1
                de = 100 * (y[me].mean() - np.mean([base_e[s] for s in ctl[me]]))
                dc = 100 * (y[mc].mean() - np.mean([base_c[s] for s in ctl[mc]]))
                if (de > 0) == (dc > 0) and min(abs(de), abs(dc)) >= thr:
                    surv += 1
                    rows.append((f"{a}×{b}", v, ne, 100 * y[me].mean(), de,
                                 nc, 100 * y[mc].mean(), dc))
    rows.sort(key=lambda r: -min(abs(r[4]), abs(r[7])))
    print(f"# 交差 target={target} control={control} (|残差|>={thr}pt・両窓同符号)")
    print(f"{'交差':<34}{'セル':<18}{'n探':>6}{'素%':>7}{'残差':>7}"
          f"{'n確':>6}{'素%':>7}{'残差':>7}")
    for r in rows[:30]:
        print(f"{r[0]:<34}{r[1]:<18}{r[2]:>6}{r[3]:>7.2f}{r[4]:>+7.2f}"
              f"{r[5]:>6}{r[6]:>7.2f}{r[7]:>+7.2f}")
    print(f"\n交差セル(腕) {arms} 本中 生存 {surv} 本")


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else "desc"
    if cmd == "desc":
        desc(a[1] if len(a) > 1 else "both_in3",
             a[2] if len(a) > 2 else "p_both")
    elif cmd == "cross":
        cross(a[1] if len(a) > 1 else "both_in3",
              a[2] if len(a) > 2 else "p_both",
              float(a[3]) if len(a) > 3 else 3.0)
    elif cmd == "auc":
        auc(a[1] if len(a) > 1 else "both_in3", a[2] if len(a) > 2 else "")