"""現行の凍結オッズモデル(odds_tf_n7, train_end=2025-12-31)を、DBに現在保存されている
wt_entries.pred_win_pct/pred_top3_pct（本番の入稿経路 load_race_inputs と同一ソース）と
組み合わせて板を作り、確定オッズと突き合わせる。

xcheck_prod.py で「このソース+このモデル」が実際の bet_detail.po をほぼ再現する
ことを確認済み（12レース36点、比の中央 ~0.99-1.0）。
"""
import sys
import numpy as np, pandas as pd

sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
from src.odds_prediction_tf import build_race_features, load_model, target_sum  # noqa: E402

BASE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"

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

booster = load_model(7)
ts = target_sum(7)

rows = []
skipped = {"meta": 0, "board": 0, "feat": 0, "zero": 0}
for rk, ym in zip(sample.race_key, sample.ym):
    meta = meta_by_rk.get(rk)
    p3, pw = p3_by_rk.get(rk), pw_by_rk.get(rk)
    if not meta or len(meta) != 7 or not p3 or not pw:
        skipped["meta"] += 1
        continue
    if any(v <= 0 for v in p3.values()) or any(v <= 0 for v in pw.values()):
        skipped["zero"] += 1
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
    for t, c in zip(combos, coherent):
        lab = "-".join(map(str, t))
        fo = board.get(lab)
        if fo is None or fo <= 0:
            continue
        rows.append((rk, ym, lab, float(c), float(fo)))

print("skipped", skipped, "rows kept", len(rows))
out = pd.DataFrame(rows, columns=["race_key", "ym", "combo", "pred_coh", "final"])
out.to_pickle(f"{BASE}/P3_oddspred/pred_vs_final_7car_v2.pkl")
print(out.shape, out.race_key.nunique(), "races")
