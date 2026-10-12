"""選手ごとの「単騎のときの残差」をモデルへ入れる A/B（2026-10-12 新設）。

ユーザー提起: 「単騎はスコア・脚質でしか判別されていないのでは。選手ごとに
どう動くかを読む要素は無いか」。記述分析（`docs/type_lab/tanki_read_2026_10_12.md`
§0〜§3）で、**その選手が単騎のとき過去にモデルをどれだけ上回ったか／下回ったか**
（読み A）が両窓で残り、しかも三連複の市場も織り込んでいなかった。
本スクリプトはそれをモデル（または後段補正）が追加で取り出せるかを測る。

🔴 事前登録（アーム・指標・採用基準）は同文書 §5。**ここで動かさないこと。**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
アーム
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  base           現行 FEATURE_COLS_WT
  +lp_lift       2026-08-31 に不採用の `lp_*` 5列（参照）。単騎の行では
                 「単騎時の3着内率 − 通算率」になる。`exp_h2h_line_records_ab.py`
                 の関数をそのまま使う（写さない）
  +tanki_resid   tk_resid3 = Σ(3着内 − p3) / (n + K)   過去の単騎レースだけ
                 tk_n      = その n
  posthoc        base の出力に、単騎の行だけ logit(p) + a + β·tk_resid3。
                 a・β は test_from より前の単騎行で、DB の pred_top3_pct を
                 offset にしたロジスティック回帰で推定する（再学習なし）

p3 は DB の `pred_top3_pct`（2024-01〜。月次 vintage／朝バッチ＝発走前の値）。
**2026-10-12 時点のスナップショットを凍結して使う**（`--build-cache` で作る）。
当月分は `--tail-only` と朝バッチで書き換わるので、凍結しないと再現しない。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
point-in-time
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
台と同じく `(race_date, start_at, race_key)` 昇順の**排他累積**
（`cumsum() - 自分の値`）。自分の結果は構造的に入らない。`--selftest` で
未来非依存・自己排他・過去依存（＝そもそも履歴を見ていないバグで自明に通らない）
の3本を検査する。

「単騎」から外す行:
  - ライン情報が無いレース（同じレースの単騎が5人以上。全員 line_size=1 になる）
  - p3 が無い行（2023 年）
DNF（finish_order=0）は着外として数える（発走前の p3 と比べる量なので）。

Usage:
    PYTHONPATH=. .venv/bin/python scripts/exp_tanki_residual_ab.py --selftest
    PYTHONPATH=. .venv/bin/python scripts/exp_tanki_residual_ab.py --build-cache
    PYTHONPATH=. .venv/bin/python scripts/exp_tanki_residual_ab.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exp_h2h_line_records_ab as base_ab  # noqa: E402

from src.preprocessing.feature_wt import (  # noqa: E402
    FEATURE_COLS_WT,
    TARGET_COL_WT,
    build_features_wt,
    load_raw_data_wt,
)

WINDOWS = base_ab.WINDOWS
SEEDS = base_ab.SEEDS
TRAIN_FROM = base_ab.TRAIN_FROM
LP_COLS = base_ab.LP_COLS

K_RESID = 8.0            # 縮小の強さ（記述分析と同じ・事前に固定）
SOLO_RACE_MAX = 4        # 単騎がこれを超えるレースはライン情報なしとみなす
PRED_FROM = "2024-01-01"

TK_COLS = ["tk_resid3", "tk_n"]
ARMS: dict[str, list[str]] = {
    "base": [],
    "+lp_lift": LP_COLS,
    "+tanki_resid": TK_COLS,
}

CACHE_DIR = Path(os.environ.get(
    "EXP_TANKI_CACHE_DIR",
    str(Path(__file__).resolve().parents[1] / "data" / "exp_cache")))
HIST_CACHE = CACHE_DIR / "exp_tanki_hist_20261012.pkl"
FEAT_CACHE = CACHE_DIR / "exp_tanki_feat_20261012.pkl"
RESULT_JSON = CACHE_DIR / "exp_tanki_result_20261012.json"


# ---------------------------------------------------------------------------
# 履歴（p3 つき）
# ---------------------------------------------------------------------------
def load_history_with_pred() -> pd.DataFrame:
    """台の `load_history()` に p3 と車数を足したもの。"""
    return base_ab._read_sql(
        "SELECT e.race_key, e.player_id, e.finish_order, e.line_pos, e.line_size, "
        "       e.pred_top3_pct, r.race_date, r.start_at, r.race_type, r.n_entries "
        "FROM keirin.wt_entries e JOIN keirin.wt_races r ON e.race_key=r.race_key "
        f"WHERE r.race_date >= '{base_ab.HISTORY_FROM}'")


def sort_history(hist: pd.DataFrame) -> pd.DataFrame:
    """🔴 時刻順の唯一の正本（台の build_extra_features と同じ並べ方）。"""
    h = hist.copy()
    h["race_date"] = h["race_date"].astype(str)
    h["_sort"] = pd.to_numeric(h["start_at"], errors="coerce").fillna(1 << 62)
    return h.sort_values(["race_date", "_sort", "race_key", "player_id"],
                         kind="mergesort").reset_index(drop=True)


def solo_mask(hist: pd.DataFrame) -> pd.Series:
    """ライン情報のあるレースの単騎だけ True。"""
    solo = base_ab.line_pos_bucket(hist["line_pos"], hist["line_size"]) == "solo"
    n_solo = solo.groupby(hist["race_key"]).transform("sum")
    return solo & (n_solo <= SOLO_RACE_MAX)


def tanki_resid_records(hist: pd.DataFrame) -> pd.DataFrame:
    """(race_key, player_id) ごとに「そのレースより前」の単騎残差を返す。

    hist は `sort_history` 済みであること。
    """
    fin = pd.to_numeric(hist["finish_order"], errors="coerce")
    p3 = pd.to_numeric(hist["pred_top3_pct"], errors="coerce") / 100.0
    valid = solo_mask(hist) & fin.notna() & p3.notna()
    top3 = ((fin >= 1) & (fin <= 3)).astype(float)
    r = np.where(valid, top3 - p3.fillna(0.0), 0.0)
    n = valid.astype(float).values
    h = pd.DataFrame({"player_id": hist["player_id"].values, "_r": r, "_n": n})
    g = h.groupby("player_id", sort=False)
    cs_r = g["_r"].cumsum() - h["_r"]
    cs_n = g["_n"].cumsum() - h["_n"]
    return pd.DataFrame({
        "race_key": hist["race_key"].values,
        "player_id": hist["player_id"].values,
        "tk_resid3": (cs_r / (cs_n + K_RESID)).values,
        "tk_n": cs_n.values,
    })


def lp_records(hist: pd.DataFrame) -> pd.DataFrame:
    """参照アーム。台の `_bucket_records` をそのまま通す。"""
    bucket = base_ab.line_pos_bucket(hist["line_pos"], hist["line_size"])
    return base_ab._bucket_records(hist, bucket, "lp", LP_COLS)


def attach(df: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    hist = sort_history(hist)
    out = df.copy()
    out["player_id"] = out["player_id"].astype(str)
    for part in (tanki_resid_records(hist), lp_records(hist)):
        part = part.copy()
        part["player_id"] = part["player_id"].astype(str)
        out = out.merge(part, on=["race_key", "player_id"], how="left")
    out["tk_resid3"] = out["tk_resid3"].fillna(0.0)
    out["tk_n"] = out["tk_n"].fillna(0.0)
    for c in LP_COLS:
        if c.endswith("_n") or c.endswith("lift"):
            out[c] = out[c].fillna(0.0)
        else:
            out[c] = out[c].fillna(base_ab.P0_TOP3 if "top3" in c else base_ab.P0_WIN)
    return out


# ---------------------------------------------------------------------------
# 後段補正（offset つきロジスティック回帰・2 パラメータの Newton 法）
# ---------------------------------------------------------------------------
def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_offset_logit(y: np.ndarray, offset: np.ndarray, x: np.ndarray,
                     n_iter: int = 50) -> tuple[float, float]:
    """y ~ Bernoulli(σ(offset + a + β·x)) の (a, β)。"""
    X = np.column_stack([np.ones_like(x), x])
    w = np.zeros(2)
    for _ in range(n_iter):
        z = offset + X @ w
        mu = 1.0 / (1.0 + np.exp(-z))
        g = X.T @ (y - mu)
        H = (X * (mu * (1 - mu))[:, None]).T @ X
        step = np.linalg.solve(H + 1e-9 * np.eye(2), g)
        w = w + step
        if np.abs(step).max() < 1e-10:
            break
    return float(w[0]), float(w[1])


def posthoc_coef(hist: pd.DataFrame, feats: pd.DataFrame, test_from: str) -> tuple[float, float, int]:
    """test_from より前の 7車・単騎行で (a, β) を当てる。"""
    h = sort_history(hist)
    rec = tanki_resid_records(h)
    h = h.assign(tk_resid3=rec["tk_resid3"].values)
    fin = pd.to_numeric(h["finish_order"], errors="coerce")
    p3 = pd.to_numeric(h["pred_top3_pct"], errors="coerce") / 100.0
    m = (solo_mask(h) & fin.notna() & p3.notna()
         & (pd.to_numeric(h["n_entries"], errors="coerce") == 7)
         & (h["race_date"] >= PRED_FROM) & (h["race_date"] < test_from))
    y = ((fin >= 1) & (fin <= 3)).astype(float)[m].values
    a, b = fit_offset_logit(y, _logit(p3[m].values), h.loc[m, "tk_resid3"].values)
    return a, b, int(m.sum())


def apply_posthoc(test: pd.DataFrame, p: np.ndarray, a: float, b: float) -> np.ndarray:
    solo = solo_rows(test)
    z = _logit(p) + np.where(solo, a + b * test["tk_resid3"].values, 0.0)
    return 1.0 / (1.0 + np.exp(-z))


# ---------------------------------------------------------------------------
# 指標
# ---------------------------------------------------------------------------
def solo_rows(test: pd.DataFrame) -> np.ndarray:
    iso = test["is_isolated"].values == 1
    n_solo = pd.Series(iso.astype(int)).groupby(test["race_key"].values).transform("sum").values
    return iso & (n_solo <= SOLO_RACE_MAX)


def tanki_metrics(test: pd.DataFrame, prob: np.ndarray, ne_map: dict) -> dict:
    """7車・ライン情報ありのレースで、単騎の被覆と logloss。"""
    t = pd.DataFrame({
        "race_key": test["race_key"].values,
        "fin": pd.to_numeric(test["finish_order"], errors="coerce").values,
        "p": prob,
        "solo": solo_rows(test),
    })
    t["ne"] = t["race_key"].map(ne_map)
    t["sz"] = t.groupby("race_key")["p"].transform("size")
    t = t[(t["ne"] == 7) & (t["sz"] == 7)]
    t["rk"] = t.groupby("race_key")["p"].rank(ascending=False, method="first")
    t["y"] = ((t["fin"] >= 1) & (t["fin"] <= 3)).astype(float)
    s = t[t["solo"]]
    hit = s[s["y"] == 1]
    pp = np.clip(s["p"].values, 1e-6, 1 - 1e-6)
    ll = float(-np.mean(s["y"].values * np.log(pp) + (1 - s["y"].values) * np.log(1 - pp)))
    return {"cov": float((hit["rk"] <= 3).mean()) if len(hit) else float("nan"),
            "n_hit": int(len(hit)), "ll": ll, "n_solo": int(len(s))}


def score(test: pd.DataFrame, p: np.ndarray, ne_map: dict) -> dict:
    from sklearn.metrics import roc_auc_score
    r = base_ab.race_metrics(test, p, ne_map)
    t = tanki_metrics(test, p, ne_map)
    return {"auc": float(roc_auc_score(test[TARGET_COL_WT], p)),
            "win": r["win"], "top3": r["top3"], "two": r["two"],
            "cov": t["cov"], "ll": t["ll"], "n_hit": t["n_hit"], "n_solo": t["n_solo"]}


# ---------------------------------------------------------------------------
# 窓ごとの実行
# ---------------------------------------------------------------------------
def run_window(df: pd.DataFrame, hist: pd.DataFrame, name: str,
               test_from: str, test_to: str) -> dict:
    import lightgbm as lgb

    rng = hist[(hist["race_date"].astype(str) >= test_from)
               & (hist["race_date"].astype(str) <= test_to)]
    ne_map = dict(zip(rng["race_key"], pd.to_numeric(rng["n_entries"], errors="coerce")))
    train = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < test_from)]
    test = df[(df["race_date"] >= test_from) & (df["race_date"] <= test_to)]
    kind = "確認窓" if name in ("w3", "w4") else "掃引窓"
    print(f"\n######## {name}({kind}) test={test_from}〜{test_to}  "
          f"train {len(train):,}行 / test {len(test):,}行 ########", flush=True)

    a, b, n_fit = posthoc_coef(hist, df, test_from)
    print(f"   posthoc: a={a:+.4f} β={b:+.4f}（{n_fit:,}行で推定）", flush=True)

    res: dict = {"_posthoc": {"a": a, "beta": b, "n_fit": n_fit}}
    acc: dict[str, list[dict]] = {k: [] for k in [*ARMS, "posthoc"]}
    imp_rank: dict[str, dict] = {}
    for arm, extra in ARMS.items():
        cols = FEATURE_COLS_WT + extra
        for seed in SEEDS:
            m = lgb.LGBMClassifier(
                objective="binary", n_estimators=500, learning_rate=0.05,
                num_leaves=31, min_child_samples=20, subsample=0.8,
                colsample_bytree=0.8, random_state=seed,
                deterministic=True, force_row_wise=True, verbose=-1)
            m.fit(train[cols], train[TARGET_COL_WT])
            p = m.predict_proba(test[cols])[:, 1]
            acc[arm].append(score(test, p, ne_map))
            if arm == "base":
                acc["posthoc"].append(score(test, apply_posthoc(test, p, a, b), ne_map))
        if extra:
            imp = pd.Series(m.feature_importances_, index=cols)
            rk = imp.rank(ascending=False, method="min").astype(int)
            imp_rank[arm] = {c: int(rk[c]) for c in extra}
    for arm, rows in acc.items():
        d = pd.DataFrame(rows)
        res[arm] = {k: float(d[k].mean()) for k in d.columns}
        res[arm]["auc_sd"] = float(d["auc"].std(ddof=0))
        res[arm]["cov_sd"] = float(d["cov"].std(ddof=0))
        if arm in imp_rank:
            res[arm]["imp_rank"] = imp_rank[arm]
        r = res[arm]
        print(f"== {arm:<13} AUC {r['auc']:.5f}±{r['auc_sd']:.5f} / 1位勝率 {r['win']*100:.2f}% / "
              f"1位3着内 {r['top3']*100:.2f}% / 二軸 {r['two']*100:.2f}% / "
              f"単騎被覆 {r['cov']*100:.2f}%(n={int(r['n_hit'])}) / 単騎LL {r['ll']:.5f}"
              + (f" / 重要度順位 {imp_rank[arm]}" if arm in imp_rank else ""), flush=True)
    return res


def judge(out: dict) -> None:
    """事前登録（文書 §5）の判定をそのまま出す。"""
    print("\n" + "=" * 78)
    print("== 差分（各アーム − base）==")
    for arm in ["+lp_lift", "+tanki_resid", "posthoc"]:
        for w, r in out.items():
            b = r["base"]
            kind = "確認" if w in ("w3", "w4") else "掃引"
            print(f"{arm:<13} {w}({kind})  ΔAUC{r[arm]['auc']-b['auc']:+.5f}  "
                  f"Δ1位3着内{(r[arm]['top3']-b['top3'])*100:+.2f}pt  "
                  f"Δ単騎被覆{(r[arm]['cov']-b['cov'])*100:+.2f}pt  "
                  f"Δ単騎LL{r[arm]['ll']-b['ll']:+.5f}")
    conf = [w for w in ("w3", "w4") if w in out]
    if len(conf) < 2:
        print("\n確認窓が揃っていないので判定しない")
        return
    print("\n== 事前登録の判定（確認窓 w3/w4）==")
    for arm in ["+tanki_resid", "posthoc"]:
        dcov = [(out[w][arm]["cov"] - out[w]["base"]["cov"]) * 100 for w in conf]
        dll = [out[w][arm]["ll"] - out[w]["base"]["ll"] for w in conf]
        dauc = [out[w][arm]["auc"] - out[w]["base"]["auc"] for w in conf]
        sd = [out[w]["base"]["auc_sd"] for w in conf]
        dt3 = np.mean([(out[w][arm]["top3"] - out[w]["base"]["top3"]) * 100 for w in conf])
        main1 = all(x > 0 for x in dcov) and np.mean(dcov) >= 0.30
        main2 = all(x < 0 for x in dll)
        guard = all(d >= -s for d, s in zip(dauc, sd)) and dt3 >= -0.30
        line = (f"{arm:<13} 主1 被覆 {dcov[0]:+.2f}/{dcov[1]:+.2f}pt → {'○' if main1 else '✕'} / "
                f"主2 LL {dll[0]:+.5f}/{dll[1]:+.5f} → {'○' if main2 else '✕'} / "
                f"ガード → {'○' if guard else '✕'}")
        if arm == "+tanki_resid":
            dlp = [(out[w]["+lp_lift"]["cov"] - out[w]["base"]["cov"]) * 100 for w in conf]
            sub = all(x > y for x, y in zip(dcov, dlp))
            line += f" / 従 対lp {dlp[0]:+.2f}/{dlp[1]:+.2f}pt → {'○' if sub else '✕'}"
        print(line)


# ---------------------------------------------------------------------------
# セルフテスト
# ---------------------------------------------------------------------------
def selftest() -> int:
    """未来非依存・自己排他・過去依存の3本。"""
    def mk(fins: list[int]) -> pd.DataFrame:
        rows = []
        for i, f in enumerate(fins):
            rk = f"2025010{i+1}_01_01"
            # 7車・単騎は A だけ（ライン情報あり）
            rows.append(dict(race_key=rk, player_id="A", finish_order=f, line_pos=1,
                             line_size=1, pred_top3_pct=40.0, race_date=f"2025-01-0{i+1}",
                             start_at=str(1735689600 + i * 86400), race_type="一般", n_entries=7))
            for j in range(6):
                rows.append(dict(race_key=rk, player_id=f"X{j}", finish_order=j + 2 if j < 5 else 1,
                                 line_pos=1 + j % 3, line_size=3, pred_top3_pct=40.0,
                                 race_date=f"2025-01-0{i+1}", start_at=str(1735689600 + i * 86400),
                                 race_type="一般", n_entries=7))
        return pd.DataFrame(rows)

    def feat(h: pd.DataFrame) -> pd.Series:
        r = tanki_resid_records(sort_history(h))
        return r[r["player_id"] == "A"].set_index("race_key")["tk_resid3"]

    ok = True
    a = feat(mk([1, 5, 2, 6]))
    b = feat(mk([1, 5, 2, 1]))            # 最後のレースの結果だけ変える
    if not np.allclose(a.iloc[:4].values, b.iloc[:4].values):
        print("✕ 未来非依存: 最後の結果を変えたら過去の特徴が動いた"); ok = False
    if a.iloc[0] != 0.0:
        print("✕ 自己排他: 初戦に自分の結果が入っている"); ok = False
    exp = ((1 - 0.4) + (0 - 0.4)) / (2 + K_RESID)   # 3戦目時点 = 1戦目+2戦目
    if not np.isclose(a.iloc[2], exp):
        print(f"✕ 過去依存: 3戦目 {a.iloc[2]:.6f} ≠ 手計算 {exp:.6f}"); ok = False
    c = feat(mk([5, 5, 2, 6]))            # 1戦目を変えると2戦目以降が動くこと
    if np.isclose(a.iloc[1], c.iloc[1]):
        print("✕ 過去依存: 1戦目を変えても2戦目の特徴が動かない"); ok = False
    print("selftest:", "OK" if ok else "NG")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
def build_cache() -> tuple[pd.DataFrame, pd.DataFrame]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print("履歴（p3 つき）読み込み ...", flush=True)
    hist = load_history_with_pred()
    hist.to_pickle(HIST_CACHE)
    print(f"  {len(hist):,}行 → {HIST_CACHE}", flush=True)
    max_to = max(t for _, t in WINDOWS.values())
    print("特徴量構築 ...", flush=True)
    df = build_features_wt(load_raw_data_wt(min_date="2023-06-01", max_date=max_to))
    df["race_date"] = df["race_date"].astype(str)
    df = attach(df, hist)
    keep = (["race_key", "player_id", "race_date", "finish_order", TARGET_COL_WT]
            + FEATURE_COLS_WT + LP_COLS + TK_COLS)
    df = df[[c for c in dict.fromkeys(keep) if c in df.columns]]
    df.to_pickle(FEAT_CACHE)
    print(f"  {len(df):,}行 {len(df.columns)}列 → {FEAT_CACHE}", flush=True)
    return df, hist


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="w1,w2,w3,w4")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--build-cache", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if args.build_cache or not FEAT_CACHE.exists():
        df, hist = build_cache()
        if args.build_cache:
            return
    else:
        df, hist = pd.read_pickle(FEAT_CACHE), pd.read_pickle(HIST_CACHE)
    out = {w: run_window(df, hist, w, *WINDOWS[w]) for w in args.windows.split(",")}
    RESULT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    judge(out)


if __name__ == "__main__":
    main()
