"""本番の生成経路で今日の B/C/D を組み、買い目・賭け金・入稿ゲート判定を表示（保存しない）。"""
import sys
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
import build_type_lab_picks as B
import netkeirin_submit_type_lab as S

B.N_ENTRIES = 7
B.MODE_TAG = ""
B.ONLY_KEYS = {"20261121_36_04", "20261121_51_01", "20261121_36_01"}
rows = B.run_live("2026-11-21")
for r in rows:
    if r["plan_key"] not in ("B_hit", "C_hit", "D_hit"):
        continue
    import json
    legs = json.loads(r["legs"]) if isinstance(r["legs"], str) else r["legs"]
    print(r["race_key"], r["plan_key"], r["bet_type"], "軸", r["axis1"], r["axis2"],
          "p3順", r.get("p3_order"), "平均想定", r.get("pred_mean_payout"),
          "ゲート", S._gate_reason(dict(r, legs=legs)))
    for lg in legs:
        print("   ", lg["combo"], lg["stake"], "円 ×", lg["pred_odds"], "倍 =", round(lg["stake"] * lg["pred_odds"]))
