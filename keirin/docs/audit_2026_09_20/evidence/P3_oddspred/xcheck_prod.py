"""実際の本番入稿(bet_detail の po)を、現行モデル+実際のwt_entries.pred_*で
再現できるか確認する（自分の再構築(wf由来)とは別ソースでの突き合わせ）。"""
import csv, json, sys, os
sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
os.chdir("/Users/ysuzuki/GitHub/kiseki/keirin")
from src.odds_prediction_tf import predict_board, load_meta, model_train_end  # noqa
from src.odds_prediction import load_race_inputs  # noqa

print("model_train_end(7)=", model_train_end(7))

BASE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
rows = list(csv.DictReader(open(f"{BASE}/B_live/subs.csv")))
cand = [r for r in rows if r["bet_detail"] and r["race_key"][:8] >= "20260801"
        and r["n_entries"] == "7"]
import random
random.seed(1)
random.shuffle(cand)

checked = 0
for r in cand:
    if checked >= 12:
        break
    try:
        bd = json.loads(r["bet_detail"])
    except Exception:
        continue
    lines = bd.get("lines") or []
    tri = [ln for ln in lines if str(ln.get("bet_type")) in ("3連単",)]
    if not tri:
        continue
    rk = r["race_key"]
    try:
        cars, p3, pw, meta = load_race_inputs(rk)
        board = predict_board(cars, p3, pw, meta)
    except Exception as e:
        print(rk, "SKIP", e)
        continue
    checked += 1
    print(f"\n--- {rk} (n_entries={r['n_entries']}) ---")
    for ln in tri[:3]:
        combo = str(ln.get("combo")).replace("=", "-")
        po = ln.get("odds")
        key = tuple(int(x) for x in combo.split("-"))
        mine = board.get(key)
        print(f"  combo={combo} bet_detail.po={po} 再現predict_board={mine}"
              f"  比={float(po)/mine if po and mine else None}")
print("\nchecked races:", checked)
