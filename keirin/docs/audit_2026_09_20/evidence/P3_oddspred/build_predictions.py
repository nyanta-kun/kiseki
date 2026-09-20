"""現行モデル(train_end=2025-12-31)をJan-Aug 2026(および2024-07-2025-12)の
無作為抽出7車レースへ適用し、確定オッズと突き合わせる。
p3/pw は C_model の honest walk-forward（wf_preds_audit.pkl）を使う。
"""
import sys, pickle
import numpy as np, pandas as pd

sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
from src.odds_prediction_tf import build_race_features, load_model, target_sum, FEATURE_NAMES  # noqa: E402

BASE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"

sample = pd.read_csv(f"{BASE}/C_model/sample_races.csv")
wf = pd.read_pickle(f"{BASE}/C_model/wf_preds_audit.pkl")
entries = pd.read_pickle(f"{BASE}/P3_oddspred/entries_sample.pkl")
odds = pd.read_pickle(f"{BASE}/C_model/odds_trifecta_sample.pkl")
odds["odds_value"] = pd.to_numeric(odds["odds_value"], errors="coerce")

wf_by_rk = {}
for rk, g in wf.groupby("race_key"):
    wf_by_rk[rk] = (dict(zip(g.frame_no, g.p3)), dict(zip(g.frame_no, g.pw)))

meta_by_rk = {}
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

board_by_rk = {}
for rk, g in odds.groupby("race_key"):
    board_by_rk[rk] = dict(zip(g.combination, g.odds_value))

booster = load_model(7)
ts = target_sum(7)

rows = []
skipped = {"wf": 0, "meta": 0, "board": 0, "feat": 0}
for rk, ym in zip(sample.race_key, sample.ym):
    if rk not in wf_by_rk:
        skipped["wf"] += 1
        continue
    p3, pw = wf_by_rk[rk]
    if len(p3) != 7 or len(pw) != 7:
        skipped["wf"] += 1
        continue
    meta = meta_by_rk.get(rk)
    if not meta or len(meta) != 7:
        skipped["meta"] += 1
        continue
    board = board_by_rk.get(rk)
    if not board or len(board) < 200:
        skipped["board"] += 1
        continue
    try:
        combos, X = build_race_features(sorted(p3), p3, pw, meta)
    except Exception:
        skipped["feat"] += 1
        continue
    raw = np.power(10.0, booster.predict(X))
    raw = np.clip(raw, 1.0, None)
    scale = float((1.0 / raw).sum()) / ts
    coherent = raw * scale
    for t, c, pr in zip(combos, coherent, raw):
        lab = "-".join(map(str, t))
        fo = board.get(lab)
        if fo is None or fo <= 0:
            continue
        rows.append((rk, ym, lab, float(c), float(pr), float(fo)))

print("skipped", skipped, "rows kept", len(rows))
out = pd.DataFrame(rows, columns=["race_key", "ym", "combo", "pred_coh", "pred_raw", "final"])
out.to_pickle(f"{BASE}/P3_oddspred/pred_vs_final_7car.pkl")
print(out.shape, out.race_key.nunique(), "races")
