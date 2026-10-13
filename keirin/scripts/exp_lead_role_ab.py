"""「普段は先頭を走る自力型が、今日は先頭でない」をモデルへ入れる A/B（2026-10-12 新設）。

記述（`docs/type_lab/role_and_class_2026_10_12.md` §3）で、直近5走のライン先頭率が高い
自力型が今日は単騎・番手のとき、1着が両窓で +2.5〜3.8pt 過小評価されていた。
現行の特徴は「今日どこを走るか」（`is_line_leader` / `line_pos` / `line_size`）と
脚質・決まり手の通算しか持たず、「**普段どこを走っているか**」が無い。

🔴 事前登録（アーム・指標・採用基準）は同文書 §3。**ここで動かさないこと。**

アーム:
  base        現行 FEATURE_COLS_WT
  +lead_role  lead5     直近5走（ライン情報のあるレースのみ・3走以上）のライン先頭率
              prev_lead 直前の1走で先頭だったか（ライン情報が無ければ NaN）

ターゲットは3着内と1着の2本。台・窓・seed・LightGBM 設定は
`exp_tanki_residual_ab.py`（→ `exp_h2h_line_records_ab.py`）と同じ。
特徴量キャッシュは `exp_tanki_residual_ab.py --build-cache` が作った同日凍結版を使う。

point-in-time: `(race_date, start_at, race_key)` 昇順で並べ、**自分の行より前**だけを使う
（`shift()` してから rolling）。「先頭」は ライン先頭（line_pos=1）かつ line_size>=2。
ライン情報が無いレース（単騎5人以上）は NaN として数えない。

Usage:
    PYTHONPATH=. .venv/bin/python scripts/exp_lead_role_ab.py --selftest
    PYTHONPATH=. .venv/bin/python scripts/exp_lead_role_ab.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exp_h2h_line_records_ab as base_ab  # noqa: E402
import exp_tanki_residual_ab as tk  # noqa: E402

from src.preprocessing.feature_wt import FEATURE_COLS_WT, TARGET_COL_WT  # noqa: E402

WINDOWS = base_ab.WINDOWS
SEEDS = base_ab.SEEDS
TRAIN_FROM = base_ab.TRAIN_FROM
LEAD_COLS = ["lead5", "prev_lead"]
WIN_COL = "win_flag_ab"
RESULT_JSON = tk.CACHE_DIR / "exp_lead_role_result_20261012.json"


def lead_records(hist: pd.DataFrame) -> pd.DataFrame:
    """(race_key, player_id) ごとに「そのレースより前」の先頭率。hist は sort_history 済み。"""
    solo = base_ab.line_pos_bucket(hist["line_pos"], hist["line_size"]) == "solo"
    n_solo = solo.groupby(hist["race_key"]).transform("sum")
    lineinfo = n_solo <= tk.SOLO_RACE_MAX
    lp = pd.to_numeric(hist["line_pos"], errors="coerce")
    ls = pd.to_numeric(hist["line_size"], errors="coerce")
    lead = np.where(lineinfo, ((lp == 1) & (ls >= 2)).astype(float), np.nan)
    s = pd.Series(lead, index=hist.index)
    g = s.groupby(hist["player_id"].values, sort=False)
    prev = g.shift()
    lead5 = prev.groupby(hist["player_id"].values, sort=False).transform(
        lambda x: x.rolling(5, min_periods=3).mean())
    return pd.DataFrame({"race_key": hist["race_key"].values,
                         "player_id": hist["player_id"].astype(str).values,
                         "lead5": lead5.values, "prev_lead": prev.values})


def attach(df: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["player_id"] = out["player_id"].astype(str)
    out = out.merge(lead_records(tk.sort_history(hist)), on=["race_key", "player_id"], how="left")
    fo = pd.to_numeric(out["finish_order"], errors="coerce")
    out[WIN_COL] = (fo == 1).astype(int)
    return out


def jiriki_nonlead_rows(test: pd.DataFrame) -> np.ndarray:
    """参考指標の母集団: 脚質が逃・両で、今日ライン先頭でない行。"""
    style = test["style_enc"].values if "style_enc" in test else None
    nonlead = test["is_line_leader"].values != 1
    if style is None:
        return nonlead
    return nonlead & np.isin(style, JIRIKI_STYLE_CODES)


# style_enc の符号は feature_wt の正本から引く（写さない）
def _jiriki_codes() -> list:
    from src.preprocessing import feature_wt as fw
    m = getattr(fw, "_STYLE_MAP", None)
    if isinstance(m, dict):
        return [v for k, v in m.items() if k in ("逃", "両")]
    return []


JIRIKI_STYLE_CODES = _jiriki_codes()


def run_window(df: pd.DataFrame, hist: pd.DataFrame, name: str, test_from: str, test_to: str) -> dict:
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score

    rng = hist[(hist["race_date"].astype(str) >= test_from) & (hist["race_date"].astype(str) <= test_to)]
    ne_map = dict(zip(rng["race_key"], pd.to_numeric(rng["n_entries"], errors="coerce")))
    train = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < test_from)]
    test = df[(df["race_date"] >= test_from) & (df["race_date"] <= test_to)]
    kind = "確認窓" if name in ("w3", "w4") else "掃引窓"
    print(f"\n######## {name}({kind}) test={test_from}〜{test_to}  "
          f"train {len(train):,}行 / test {len(test):,}行 ########", flush=True)
    sub = jiriki_nonlead_rows(test)
    res: dict = {}
    for target in (TARGET_COL_WT, WIN_COL):
        for arm, extra in (("base", []), ("+lead_role", LEAD_COLS)):
            cols = FEATURE_COLS_WT + extra
            acc = []
            for seed in SEEDS:
                m = lgb.LGBMClassifier(
                    objective="binary", n_estimators=500, learning_rate=0.05,
                    num_leaves=31, min_child_samples=20, subsample=0.8,
                    colsample_bytree=0.8, random_state=seed,
                    deterministic=True, force_row_wise=True, verbose=-1)
                m.fit(train[cols], train[target])
                p = m.predict_proba(test[cols])[:, 1]
                r = base_ab.race_metrics(test, p, ne_map)
                y = test[WIN_COL].values[sub]
                pp = np.clip(p[sub], 1e-6, 1 - 1e-6)
                ll = float(-np.mean(y * np.log(pp) + (1 - y) * np.log(1 - pp))) if target == WIN_COL else float("nan")
                acc.append({"auc": roc_auc_score(test[target], p), "win": r["win"],
                            "top3": r["top3"], "two": r["two"], "ll_sub": ll})
            d = pd.DataFrame(acc)
            key = f"{'top3' if target == TARGET_COL_WT else 'win'}|{arm}"
            res[key] = {k: float(d[k].mean()) for k in d.columns}
            res[key]["auc_sd"] = float(d["auc"].std(ddof=0))
            if extra:
                imp = pd.Series(m.feature_importances_, index=cols)
                rk = imp.rank(ascending=False, method="min").astype(int)
                res[key]["imp_rank"] = {c: int(rk[c]) for c in extra}
            x = res[key]
            print(f"== {key:<18} AUC {x['auc']:.5f}±{x['auc_sd']:.5f} / 1位勝率 {x['win']*100:.2f}% / "
                  f"1位3着内 {x['top3']*100:.2f}% / 二軸 {x['two']*100:.2f}%"
                  + (f" / 参考LL {x['ll_sub']:.5f}" if target == WIN_COL else "")
                  + (f" / 重要度順位 {x['imp_rank']}" if extra else ""), flush=True)
    return res


def judge(out: dict) -> None:
    print("\n" + "=" * 78)
    for w, r in out.items():
        kind = "確認" if w in ("w3", "w4") else "掃引"
        for t in ("top3", "win"):
            b, a = r[f"{t}|base"], r[f"{t}|+lead_role"]
            print(f"{t:<5} {w}({kind})  ΔAUC{a['auc']-b['auc']:+.5f}  Δ1位勝率{(a['win']-b['win'])*100:+.2f}pt  "
                  f"Δ1位3着内{(a['top3']-b['top3'])*100:+.2f}pt  Δ二軸{(a['two']-b['two'])*100:+.2f}pt"
                  + (f"  Δ参考LL{a['ll_sub']-b['ll_sub']:+.5f}" if t == "win" else ""))
    conf = [w for w in ("w3", "w4") if w in out]
    if len(conf) < 2:
        print("確認窓が揃っていないので判定しない")
        return
    print("\n== 事前登録の判定（確認窓 w3/w4）==")
    for t, met in (("top3", "top3"), ("win", "win")):
        dm = [(out[w][f"{t}|+lead_role"][met] - out[w][f"{t}|base"][met]) * 100 for w in conf]
        da = [out[w][f"{t}|+lead_role"]["auc"] - out[w][f"{t}|base"]["auc"] for w in conf]
        sd = [out[w][f"{t}|base"]["auc_sd"] for w in conf]
        main = all(x > 0 for x in dm) and np.mean(dm) >= 0.30
        guard = all(d >= -s for d, s in zip(da, sd))
        print(f"{t}モデル  Δ1位{'3着内' if t == 'top3' else '勝率'} {dm[0]:+.2f}/{dm[1]:+.2f}pt → "
              f"{'○' if main else '✕'} / ガード ΔAUC {da[0]:+.5f}/{da[1]:+.5f} → {'○' if guard else '✕'}"
              f" ⇒ {'合格' if main and guard else '不合格'}")


def selftest() -> int:
    def mk(leads: list[int]) -> pd.DataFrame:
        rows = []
        for i, ld in enumerate(leads):
            rk = f"202501{i+1:02d}_01_01"
            rows.append(dict(race_key=rk, player_id="A", line_pos=1 if ld else 2, line_size=3,
                             race_date=f"2025-01-{i+1:02d}", start_at=str(1735689600 + i * 86400)))
            for j in range(6):
                rows.append(dict(race_key=rk, player_id=f"X{j}", line_pos=1 + j % 3, line_size=3,
                                 race_date=f"2025-01-{i+1:02d}", start_at=str(1735689600 + i * 86400)))
        return pd.DataFrame(rows)

    def feat(h):
        r = lead_records(tk.sort_history(h))
        return r[r["player_id"] == "A"].reset_index(drop=True)

    ok = True
    a = feat(mk([1, 1, 0, 1, 0, 1]))
    b = feat(mk([1, 1, 0, 1, 0, 0]))
    if not np.allclose(a[["lead5"]].fillna(-1).values, b[["lead5"]].fillna(-1).values):
        print("✕ 未来非依存: 最後の立場を変えたら特徴が動いた"); ok = False
    if not np.isnan(a.loc[0, "prev_lead"]):
        print("✕ 自己排他: 初戦に自分の立場が入っている"); ok = False
    if a.loc[3, "lead5"] != 2 / 3 or a.loc[3, "prev_lead"] != 0.0:
        print(f"✕ 過去依存: 4戦目 lead5={a.loc[3,'lead5']} prev={a.loc[3,'prev_lead']}"); ok = False
    if not np.isnan(a.loc[2, "lead5"]):
        print("✕ min_periods: 2走しか無いのに lead5 が出ている"); ok = False
    print("selftest:", "OK" if ok else "NG")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="w1,w2,w3,w4")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if not tk.FEAT_CACHE.exists():
        df, hist = tk.build_cache()
    else:
        df, hist = pd.read_pickle(tk.FEAT_CACHE), pd.read_pickle(tk.HIST_CACHE)
    print(f"自力型の脚質コード: {JIRIKI_STYLE_CODES}", flush=True)
    df = attach(df, hist)
    out = {w: run_window(df, hist, w, *WINDOWS[w]) for w in args.windows.split(",")}
    RESULT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    judge(out)


if __name__ == "__main__":
    main()
