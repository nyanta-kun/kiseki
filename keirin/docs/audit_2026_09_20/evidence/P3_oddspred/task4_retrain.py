"""再学習の効果を軽量に測る:
  現行モデル相当（train_end=2025-12-31）と、学習終端を2026-06-30まで伸ばした版を
  同一特徴量・同一ハイパラで作り、2026-07-01〜2026-08-31（現行モデルにとって
  完全OOS・新版にとっても完全OOS）の同一テストで比較する。

既存資産のみで完結（追加のDBアクセスなし）:
  C_model/sample_races.csv, C_model/odds_trifecta_sample.pkl,
  P3_oddspred/entries_sample2.pkl（wt_entries.pred_win_pct/pred_top3_pctを含む）
"""
import sys, time
import numpy as np, pandas as pd

sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
from src.odds_prediction_tf import build_race_features, FEATURE_NAMES  # noqa: E402

BASE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
t0 = time.time()

sample = pd.read_csv(f"{BASE}/C_model/sample_races.csv")
entries = pd.read_pickle(f"{BASE}/P3_oddspred/entries_sample2.pkl")
odds = pd.read_pickle(f"{BASE}/C_model/odds_trifecta_sample.pkl")
odds["odds_value"] = pd.to_numeric(odds["odds_value"], errors="coerce")

meta_by_rk, p3_by_rk, pw_by_rk = {}, {}, {}
for rk, g in entries.groupby("race_key"):
    meta_by_rk[rk] = {
        int(r.frame_no): {
            "race_point": r.race_point, "mark": r.prediction_mark,
            "player_class": r.player_class, "style": r.style,
            "line_group": r.line_group, "line_size": r.line_size,
            "line_pos": r.line_pos, "is_line_leader": r.is_line_leader,
            "first_rate": r.first_rate, "second_rate": r.second_rate,
            "third_rate": r.third_rate,
        }
        for r in g.itertuples(index=False)
    }
    p3_by_rk[rk] = {int(r.frame_no): float(r.pred_top3_pct) / 100.0 for r in g.itertuples(index=False)}
    pw_by_rk[rk] = {int(r.frame_no): float(r.pred_win_pct) / 100.0 for r in g.itertuples(index=False)}

board_by_rk = {}
for rk, g in odds.groupby("race_key"):
    board_by_rk[rk] = dict(zip(g.combination, g.odds_value))

date_by_rk = dict(zip(sample.race_key, sample.date))

frames = []
skipped = 0
for rk in sample.race_key:
    meta, p3, pw = meta_by_rk.get(rk), p3_by_rk.get(rk), pw_by_rk.get(rk)
    if not meta or not p3 or not pw or len(meta) != 7:
        skipped += 1
        continue
    if any(v <= 0 for v in p3.values()) or any(v <= 0 for v in pw.values()):
        skipped += 1
        continue
    board = board_by_rk.get(rk)
    if not board or len(board) < 200:
        skipped += 1
        continue
    try:
        combos, X = build_race_features(sorted(p3), p3, pw, meta)
    except Exception:
        skipped += 1
        continue
    keep = [i for i, c in enumerate(combos) if "-".join(map(str, c)) in board]
    if len(keep) < 200:
        skipped += 1
        continue
    fo = np.array([board["-".join(map(str, combos[i]))] for i in keep])
    df = pd.DataFrame(X[keep], columns=list(FEATURE_NAMES))
    df["rk"] = rk
    df["date"] = date_by_rk[rk]
    df["odds"] = fo
    frames.append(df)
print(f"採用 {len(frames)}R / 除外 {skipped}  ({time.time()-t0:.1f}s)", flush=True)

d = pd.concat(frames, ignore_index=True)
d["y"] = np.log10(d.odds)
print("total rows", len(d), "races", d.rk.nunique())

import lightgbm as lgb
PARAMS = dict(objective="regression", metric="l1", learning_rate=0.05,
              num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, verbose=-1)


def fit_eval(train_end, test_from, test_to, rounds=600):
    tr = d[d.date <= train_end]
    te = d[(d.date > test_from) & (d.date <= test_to)] if test_from else d[d.date > train_end]
    te = d[(d.date >= test_from) & (d.date <= test_to)]
    ts_target = float(tr.groupby("rk").odds.apply(lambda s: (1 / s).sum()).mean())
    t1 = time.time()
    booster = lgb.train(PARAMS, lgb.Dataset(tr[list(FEATURE_NAMES)], tr.y), num_boost_round=rounds)
    print(f"  fit train_end={train_end} n_train={len(tr)}({tr.rk.nunique()}R) "
          f"target_sum={ts_target:.4f}  {time.time()-t1:.1f}s")
    raw = np.clip(np.power(10.0, booster.predict(te[list(FEATURE_NAMES)])), 1.0, None)
    p = pd.DataFrame({"rk": te.rk.to_numpy(), "raw": raw, "odds": te.odds.to_numpy()})
    scale = p.groupby("rk").raw.transform(lambda s: (1 / s).sum() / ts_target)
    coherent = (p.raw * scale).to_numpy()
    err = np.log10(coherent) - np.log10(p.odds.to_numpy())
    ratio = coherent / p.odds.to_numpy()
    print(f"  test {test_from}〜{test_to} n={len(te)}({te.rk.nunique()}R) "
          f"logMAE={np.abs(err).mean():.4f} median_ratio={np.median(ratio):.4f} "
          f"within2x={((ratio>=.5)&(ratio<=2)).mean()*100:.1f}%")
    return dict(train_end=train_end, n_train=len(tr), logMAE=float(np.abs(err).mean()),
                median_ratio=float(np.median(ratio)), within2x=float(((ratio >= .5) & (ratio <= 2)).mean()),
                p=p, te=te, coherent=coherent)


print("\n=== テスト窓 2026-07-01〜2026-08-31（両モデルにとって完全OOS）===")
print("[現行モデル相当] train_end=2025-12-31")
r_cur = fit_eval("2025-12-31", "2026-07-01", "2026-08-31")
print("[再学習版] train_end=2026-06-30")
r_new = fit_eval("2026-06-30", "2026-07-01", "2026-08-31")

print("\n=== テスト窓を月別に分解 ===")
for m_from, m_to in (("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31")):
    print(f"-- {m_from}〜{m_to} --")
    print(" 現行(train_end 2025-12-31):")
    fit_eval("2025-12-31", m_from, m_to)
    print(" 再学習(train_end 2026-06-30):")
    fit_eval("2026-06-30", m_from, m_to)

print(f"\n総所要 {time.time()-t0:.1f}s")
