#!/usr/bin/env python3
"""相手（3着目）モデルの学習・評価（キャッシュ台を使う高速版・2026-09-14）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/pc_fast.py <section>

section: tune | fit | imp | why | export

前提: `partner_ceiling.py build` で /tmp/pc_feats.npz を作ってあること。
🔴 モデル選択（ハイパラ・目的関数）は **fold A（探索窓の内側）だけ**で行い、
   fold B（確認窓）は最後に1回だけ評価する。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CACHE = "/tmp/pc_feats.npz"
ROWS_PKL = "/tmp/ratebranch/rows.pkl"
FOLD_A_TRAIN_END = "2025-06-30"
Z = None
ROWS = None


def load():
    global Z, ROWS
    if Z is None:
        z = np.load(CACHE, allow_pickle=False)
        Z = dict(X=z["X"].astype(np.float32), C=z["C"], y=z["y"],
                 win=np.array([str(v) for v in z["win"]]),
                 date=np.array([str(v) for v in z["date"]]),
                 names=[str(v) for v in z["names"]])
    if ROWS is None:
        with open(ROWS_PKL, "rb") as f:
            ROWS = pickle.load(f)
    return Z, ROWS


def masks():
    """🔴 母集団は **軸2車がそろった回だけ**（`both_in3`）。

    `third_car` は「決着3車のうち軸でない先頭の車」で **both_in3 でなくても値が入る**
    ので、`y >= 0` で絞ると軸が崩れた回まで混ざる（実測 6,549 → 12,782 行・
    p3 の当て率が 42.3% → 39.0% に落ちる）。ここを取り違えると別の問題を測ることになる。
    """
    z, rows = load()
    ex = z["win"] == "explore"
    cf = z["win"] == "confirm"
    early = z["date"] <= FOLD_A_TRAIN_END
    both = np.array([bool(r["both_in3"]) for r in rows])
    ok = (z["y"] >= 0) & both
    return {"A": (ex & early & ok, ex & ~early & ok),
            "B": (ex & ok, cf & ok)}


def fit_model(tr_mask, params=None, rounds=300, seed=0):
    import lightgbm as lgb
    z, _ = load()
    X = z["X"][tr_mask].reshape(-1, z["X"].shape[2])
    lab = np.zeros(X.shape[0], np.int8)
    lab[np.arange(tr_mask.sum()) * 5 + z["y"][tr_mask]] = 1
    p = dict(objective="lambdarank", metric="ndcg", ndcg_eval_at=[1],
             learning_rate=0.05, num_leaves=31, min_data_in_leaf=100,
             feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
             lambdarank_truncation_level=5, verbosity=-1, seed=seed)
    p.update(params or {})
    ds = lgb.Dataset(X, label=lab, group=np.full(tr_mask.sum(), 5),
                     feature_name=z["names"])
    return lgb.train(p, ds, num_boost_round=rounds)


def pred(bst, mask):
    z, _ = load()
    X = z["X"][mask].reshape(-1, z["X"].shape[2])
    return bst.predict(X).reshape(-1, 5)


def topm(score, y, m):
    """score: (n,5)。y: 正解の候補 index。上位 m 本に入る率(%)。

    🔴 同値は **p3 順**（＝候補列の並び順）で割る。安定ソートを使うこと。
       市場（三連単の最安）は「最安の目に含まれる3車が同値」になるので、
       ここを不安定ソートにすると値が 1pt ずれる。
    """
    o = np.argsort(-score, axis=1, kind="stable")
    return float((o[:, :m] == y[:, None]).any(1).mean() * 100)


def base_scores(mask):
    """基準線（候補順の (n,5) スコア）。列は cache の並び＝p3 降順の候補。"""
    z, rows = load()
    idx = np.where(mask)[0]
    n = len(idx)
    out = {}
    nm = z["names"]
    col = {k: nm.index(k) for k in ("p3", "same_line_any", "mkt_tf", "mkt_t3_share",
                                    "pr_t3_share", "rp", "rate3")}
    X = z["X"][mask].astype(np.float64)   # 🔴 float32 だと同ラインの tie-break が丸めで消える
    out["モデル p3（現行の相手順）"] = X[:, :, col["p3"]]
    out["同ライン優先"] = 10.0 * X[:, :, col["same_line_any"]] + X[:, :, col["p3"]]
    out["市場（三連単の最安）"] = X[:, :, col["mkt_tf"]]
    out["市場（三連複 {a1,a2,c}）"] = np.nan_to_num(X[:, :, col["mkt_t3_share"]])
    out["選抜確率 pr_t3（λ/μ込み）"] = np.nan_to_num(X[:, :, col["pr_t3_share"]])
    out["競走得点"] = X[:, :, col["rp"]]
    out["3着内率"] = X[:, :, col["rate3"]]
    return out, n


def tune():
    z, _ = load()
    M = masks()
    tr, ev = M["A"]
    y = z["y"][ev]
    print(f"§tune  fold A のみで選ぶ（train {tr.sum():,} / eval {ev.sum():,}）\n")
    bs, _ = base_scores(ev)
    for k, v in bs.items():
        print(f"  基準 {k:26s}{topm(v, y, 1):7.2f}%")
    print()
    grid = [
        ("lambdarank lr.05 leaves31 min100 r300", {}, 300),
        ("lambdarank lr.05 leaves63 min50 r600",
         dict(num_leaves=63, min_data_in_leaf=50), 600),
        ("lambdarank lr.02 leaves31 min50 r1200",
         dict(learning_rate=0.02, min_data_in_leaf=50), 1200),
        ("lambdarank lr.05 leaves15 min200 r200",
         dict(num_leaves=15, min_data_in_leaf=200), 200),
        ("binary lr.05 leaves31 min100 r300",
         dict(objective="binary", metric="binary_logloss"), 300),
        ("binary lr.02 leaves63 min50 r800",
         dict(objective="binary", metric="binary_logloss",
              learning_rate=0.02, num_leaves=63, min_data_in_leaf=50), 800),
    ]
    for name, prm, rd in grid:
        acc = []
        for seed in (0, 1, 2):
            b = fit_model(tr, prm, rd, seed)
            acc.append(topm(pred(b, ev), y, 1))
        print(f"  学習 {name:34s}{np.mean(acc):7.2f}%  (seed {min(acc):.2f}-{max(acc):.2f})")


#: fold A だけで選んだ構成（binary lr.05 leaves31 min100 r300 が最良）。
BEST = (dict(objective="binary", metric="binary_logloss"), 300)


def fit():
    z, _ = load()
    M = masks()
    print("§3 3着目を直接学習する（LightGBM・group=5・両窓とも学習は過去だけ）\n")
    out = {}
    for f in ("A", "B"):
        tr, ev = M[f]
        y = z["y"][ev]
        b = fit_model(tr, *BEST)
        out[f] = b
        bs, n = base_scores(ev)
        bs["**学習モデル**"] = pred(b, ev)
        lab = ("A (train<=2025-06 / eval 2025-07-12)" if f == "A"
               else "B (train=explore / eval=confirm 2026)")
        print(f"--- fold {lab}  train {tr.sum():,} / eval {n:,} ---")
        print(f"{'選び方':30s}{'m=1':>8}{'m=2':>8}{'m=3':>8}")
        for k, v in bs.items():
            print(f"{k:30s}" + "".join(f"{topm(v, y, m):7.2f}%" for m in (1, 2, 3)))
        print()
    with open("/tmp/pc_best.pkl", "wb") as fp:
        pickle.dump({k: v.model_to_string() for k, v in out.items()}, fp)
    print("モデル保存 /tmp/pc_best.pkl")


def imp():
    z, _ = load()
    M = masks()
    groups = {
        "モデル出力 p3/pw/pr_t3": ("p3", "p3_rank", "p3_share", "p3_gap_top", "pw",
                              "pw_rank", "pr_t3_share"),
        "市場（予測オッズ）": ("mkt_t3_share", "mkt_t3", "mkt_tf", "mkt_rank"),
        "ライン内部構造": ("same_line_a1", "same_line_a2", "same_line_any", "lpos",
                    "lsize", "llead", "lpos_minus_a1", "is_solo", "axis_line_size"),
        "ライン間関係": ("n_cand_in_axis_line", "axes_same_line", "nlines"),
        "脚質": ("style", "style_a1"),
        "素の力量 rp/rate": ("rp", "rp_z", "rate1", "rate2", "rate3", "rate3_z",
                        "rate3_minus_axis"),
        "レース文脈": ("axis_sum", "gap", "arare", "pw_ent", "rp_sd", "agree", "type"),
    }
    for f in ("A", "B"):
        tr, _ = M[f]
        b = fit_model(tr, *BEST)
        g = b.feature_importance("gain")
        s = g.sum() or 1.0
        nm = z["names"]
        print(f"--- fold {f}（gain 上位18）---")
        for j in np.argsort(-g)[:18]:
            print(f"  {nm[j]:22s}{g[j]/s*100:7.2f}%")
        print("  ── 方向別合計")
        for gn, cols in groups.items():
            t = sum(g[nm.index(c)] for c in cols if c in nm)
            print(f"  {gn:22s}{t/s*100:7.2f}%")
        print()


GROUPS = {
    "市場（予測オッズ）": ("mkt_t3_share", "mkt_t3", "mkt_tf", "mkt_rank"),
    "ライン（内部構造＋ライン間）": ("same_line_a1", "same_line_a2", "same_line_any",
                          "lpos", "lsize", "llead", "lpos_minus_a1", "is_solo",
                          "axis_line_size", "n_cand_in_axis_line", "axes_same_line",
                          "nlines"),
    "脚質": ("style", "style_a1"),
    "素の力量 rp/rate": ("rp", "rp_z", "rate1", "rate2", "rate3", "rate3_z",
                    "rate3_minus_axis"),
    "モデル出力 p3/pw/pr_t3": ("p3", "p3_rank", "p3_share", "p3_gap_top", "pw",
                          "pw_rank", "pr_t3_share"),
    "レース文脈": ("axis_sum", "gap", "arare", "pw_ent", "rp_sd", "agree", "type"),
}


def abl():
    """特徴群を落として当て率がどれだけ落ちるか（＝その群にしか無い情報の量）。

    🔴 gain の割合は相関で歪む（予測オッズがライン情報を既に含む）。
       「その群を落としたら何 pt 落ちるか」のほうが「残っているか」の答えに近い。
    """
    z, _ = load()
    M = masks()
    nm = z["names"]
    keep_all = list(range(len(nm)))

    def run(cols, tag):
        line = f"{tag:34s}"
        for f in ("A", "B"):
            tr, ev = M[f]
            y = z["y"][ev]
            acc = []
            for seed in (0, 1, 2):
                import lightgbm as lgb
                X = z["X"][tr][:, :, cols].reshape(-1, len(cols))
                lab = np.zeros(X.shape[0], np.int8)
                lab[np.arange(tr.sum()) * 5 + z["y"][tr]] = 1
                p = dict(objective="binary", metric="binary_logloss",
                         learning_rate=0.05, num_leaves=31, min_data_in_leaf=100,
                         feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                         verbosity=-1, seed=seed)
                ds = lgb.Dataset(X, label=lab, feature_name=[nm[c] for c in cols])
                b = lgb.train(p, ds, num_boost_round=300)
                sc = b.predict(z["X"][ev][:, :, cols].reshape(-1, len(cols))).reshape(-1, 5)
                acc.append(topm(sc, y, 1))
            line += f"{np.mean(acc):8.2f}%"
        print(line)

    print("§6 特徴群の抜き差し（3着目を1車目で当てる率・確認 / 探索後半）\n")
    print(f"{'腕':34s}{'fold A':>9}{'fold B':>9}")
    run(keep_all, "全特徴（本稿の学習モデル）")
    for gn, cols in GROUPS.items():
        drop = {nm.index(c) for c in cols if c in nm}
        run([c for c in keep_all if c not in drop], f"− {gn} を落とす")
    # 単独群だけ
    for gn in ("市場（予測オッズ）", "ライン（内部構造＋ライン間）"):
        cols = [nm.index(c) for c in GROUPS[gn] if c in nm]
        run(cols, f"{gn} だけ")
    pm = [nm.index(c) for c in GROUPS["モデル出力 p3/pw/pr_t3"] if c in nm]
    for gn in ("ライン（内部構造＋ライン間）", "脚質", "素の力量 rp/rate", "市場（予測オッズ）"):
        cols = pm + [nm.index(c) for c in GROUPS[gn] if c in nm]
        run(cols, f"モデル出力 ＋ {gn}")
    run(pm, "モデル出力だけ")


def why():
    """相手外しのとき、正解の3着目が候補5車の何位にいるか。"""
    z, rows = load()
    M = masks()
    set_hit = np.array([bool(r["set_hit"]) for r in rows])
    for f in ("A", "B"):
        tr, ev = M[f]
        b = fit_model(tr, *BEST)
        miss = ev & ~set_hit
        y_all = z["y"]
        bs, _ = base_scores(miss)
        bs["**学習モデル**"] = pred(b, miss)
        y = y_all[miss]
        print(f"--- fold {f}  相手外し n={miss.sum():,}"
              f"（軸2車そろい {ev.sum():,} の {miss.sum()/ev.sum()*100:.1f}%）---")
        print(f"{'選び方':30s}{'1位':>7}{'2位':>7}{'3位':>7}{'4位':>7}{'5位':>7}"
              f"    1位に置けた")
        for k, v in bs.items():
            o = np.argsort(-v, axis=1)
            pos = np.argmax(o == y[:, None], axis=1)
            cnt = np.bincount(pos, minlength=5)
            print(f"{k:30s}" + "".join(f"{c:7d}" for c in cnt)
                  + f"{cnt[0]/len(y)*100:10.2f}%")
        print()


def export():
    """全行ぶんの学習スコア（候補順 5列）を保存。商品KPI で使う。"""
    z, rows = load()
    M = masks()
    S = np.zeros((len(rows), 5), np.float32)
    ex = z["win"] == "explore"
    cf = z["win"] == "confirm"
    early = z["date"] <= FOLD_A_TRAIN_END
    for f, tgt in (("A", ex & ~early), ("B", cf)):
        tr, _ = M[f]
        b = fit_model(tr, *BEST)
        S[tgt] = pred(b, tgt)
    np.save("/tmp/pc_scores.npy", S)
    print("保存 /tmp/pc_scores.npy（fold A のモデル→探索窓後半 / fold B→確認窓）")


# ─────────────────────────── 商品KPI ───────────────────────────

def _kpi(recs):
    inv = np.array([a for a, _ in recs], float)
    pay = np.array([b for _, b in recs], float)
    h = pay[pay > 0]
    return dict(n=len(recs), shown=float((pay >= inv).mean() * 100),
                roi=float(pay.sum() / inv.sum() * 100),
                med=float(np.median(h)) if len(h) else 0.0,
                big=int((pay >= 100_000).sum()))


def _ci(a, b, n=2000, seed=0):
    a = np.asarray(a, float); b = np.asarray(b, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    d = (b[idx].mean(1) - a[idx].mean(1)) * 100
    return float((b.mean() - a.mean()) * 100), float(np.percentile(d, 2.5)), \
        float(np.percentile(d, 97.5))


def product():
    """学習した相手確率を商品の選抜へ載せる（点数・帯・配分・ゲートは現行のまま）。"""
    import random
    from partner_ceiling import base_bet, payout, shape_of
    from src.type_lab import (PLANS, RaceShape, allocate, build_legs,
                              mean_expected_payout)
    MINM, MINP = 20_000, 2.0
    z, rows = load()
    S = np.load("/tmp/pc_scores.npy")
    C, X = z["C"], z["X"]
    p3col = z["names"].index("p3")
    ex = z["win"] == "explore"; cf = z["win"] == "confirm"
    early = z["date"] <= FOLD_A_TRAIN_END
    EV = {"A (train<=2025-06 / eval 2025-07-12)": ex & ~early,
          "B (train=explore / eval=confirm 2026)": cf}

    def wvec(j):
        s = S[j].astype(float)
        s = np.exp(s - s.max()); s /= s.sum()
        q = X[j, :, p3col].astype(float); q = q / max(q.sum(), 1e-9)
        return s / np.maximum(q, 1e-6), s

    def run(idxs, alpha, seed=None, mode="mult"):
        rng = random.Random(seed) if seed is not None else None
        recs, sh = [], []
        for j in idxs:
            r = rows[j]
            cs = [int(c) for c in C[j]]
            w, sp = wvec(j)
            if rng is not None:
                perm = list(range(5)); rng.shuffle(perm)
                w = w[perm]; sp = sp[perm]
            if alpha == 0.0:
                st = base_bet(r)
            elif mode == "order":
                idx = {c: k for k, c in enumerate(cs)}
                order = (r["a1"], r["a2"]) + tuple(sorted(cs, key=lambda c: -sp[idx[c]]))
                plan = PLANS[r["plan"]]
                pod, prb = ((r["po_t3"], r["pr_t3"]) if r["trio"]
                            else (r["po_tf"], r["pr_tf"]))
                s0 = shape_of(r)
                s1 = RaceShape(s0.type_label, s0.axis_sum, s0.arare, s0.gap,
                               s0.firm, order, s0.pw_ent)
                legs = build_legs(s1, plan, pod, prb)
                st = allocate(legs, pod, prb, plan) if legs else None
                if st and (mean_expected_payout(st, pod) <= MINM
                           or min(float(pod[c]) for c in st) < MINP):
                    st = None
            else:
                pod, prb0 = ((r["po_t3"], r["pr_t3"]) if r["trio"]
                             else (r["po_tf"], r["pr_tf"]))
                W = dict(zip(cs, w))
                ax = {r["a1"], r["a2"]}
                prb = {}
                for c, v in prb0.items():
                    cars = set(c if not r["trio"] else tuple(c))
                    f = 1.0
                    for car in cars - ax:
                        f *= W.get(car, 1.0) ** alpha
                    prb[c] = v * f
                st = base_bet(r, prb)
            if st is None:
                recs.append(None); sh.append(None); continue
            iv, py = payout(r, st)
            recs.append((iv, py)); sh.append(py >= iv)
        return recs, sh

    def table(title, idxs, arms, nseed=20):
        days = len({rows[j]["date"] for j in idxs}) or 1
        b_recs, b_sh = run(idxs, 0.0)
        bk = _kpi([x for x in b_recs if x is not None])
        print(f"{title}  n={len(idxs):,}商品（ゲート通過 {bk['n']:,}）")
        print(f"{'腕':22s}{'件/日':>7}{'表示的中':>9}{'払戻中央':>10}{'10万+/日':>9}"
              f"{'ROI':>7}   Δ表示的中 95%CI          対照")
        print(f"{'現行':22s}{bk['n']/days:7.2f}{bk['shown']:8.2f}%{bk['med']:10,.0f}"
              f"{bk['big']/days:9.3f}{bk['roi']:7.1f}")
        for name, alpha, mode in arms:
            recs, sh = run(idxs, alpha, mode=mode)
            k = _kpi([x for x in recs if x is not None])
            both = [i for i in range(len(idxs))
                    if recs[i] is not None and b_recs[i] is not None]
            d = _ci([b_sh[i] for i in both], [sh[i] for i in both])
            mine = float(np.mean([sh[i] for i in both]))
            wins = 0
            for sd in range(nseed):
                _, csh = run(idxs, alpha, seed=4000 + sd, mode=mode)
                bo = [i for i in range(len(idxs))
                      if csh[i] is not None and b_sh[i] is not None]
                wins += mine > float(np.mean([csh[i] for i in bo]))
            print(f"{name:22s}{k['n']/days:7.2f}{k['shown']:8.2f}%{k['med']:10,.0f}"
                  f"{k['big']/days:9.3f}{k['roi']:7.1f}"
                  f"   {d[0]:+.2f} [{d[1]:+.2f},{d[2]:+.2f}]   {wins}/{nseed}")
        print()

    print("§4 学習した相手確率を商品の選抜へ（点数・帯・配分・ゲートは現行のまま）\n")
    for fname, sel in EV.items():
        ef = [j for j in np.where(sel)[0] if rows[j]["plan"] in ("E_hit", "F_hit")]
        table(f"--- (1) E_hit+F_hit  fold {fname}", ef,
              [("学習 a=0.5", 0.5, "mult"), ("学習 a=1.0", 1.0, "mult"),
               ("学習 a=2.0", 2.0, "mult")])
        at = [j for j in np.where(sel)[0] if rows[j]["plan"] == "A_trio"]
        table(f"--- (2) A_trio 相手2点の並び  fold {fname}", at,
              [("相手を学習順", 1.0, "order")])
        al = list(np.where(sel)[0])
        table(f"--- (3) ラインナップ全体  fold {fname}", al,
              [("学習 a=1.0", 1.0, "mult")], nseed=10)


if __name__ == "__main__":
    {"tune": tune, "fit": fit, "imp": imp, "why": why, "export": export,
     "product": product, "abl": abl}[sys.argv[1]]()
