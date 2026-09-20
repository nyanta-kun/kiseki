"""月別に保存した odds / entries を結合し、レースごとの当たり目・払戻を計算して
1本の分析用データセットに固める。DB へは再接続しない（ローカルキャッシュのみ）。"""
import glob
import itertools
import pickle
import re

import numpy as np
import pandas as pd

OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/D_market"

sample_df = pd.read_pickle(f"{OUT}/sample_races.pkl")

odds_files = sorted(glob.glob(f"{OUT}/odds_by_month/*.pkl"))
ent_files = sorted(glob.glob(f"{OUT}/entries_by_month/*.pkl"))
print("odds files:", len(odds_files), "entry files:", len(ent_files))

odds_df = pd.concat([pd.read_pickle(f) for f in odds_files], ignore_index=True)
ent_df = pd.concat([pd.read_pickle(f) for f in ent_files], ignore_index=True)
print("odds rows:", len(odds_df), "entries rows:", len(ent_df))

# race_key が sample に含まれるものだけに絞る(念のため)
valid_keys = set(sample_df["race_key"])
odds_df = odds_df[odds_df["race_key"].isin(valid_keys)].copy()
ent_df = ent_df[ent_df["race_key"].isin(valid_keys)].copy()

payout_per_100 = lambda odds: None if pd.isna(odds) else int(round(float(odds) * 100)) // 10 * 10


def groups_from_finishers(rows):
    """[(finish_order, frame_no), ...] -> 着順ごとの同着グループ(車番昇順)。finish_orderは1..3のみ。"""
    rows = sorted([(int(fo), int(fn)) for fo, fn in rows if fo is not None and 1 <= int(fo) <= 3])
    out = []
    for fo, fn in rows:
        if out and out[-1][0] == fo:
            out[-1][1].append(fn)
        else:
            out.append((fo, [fn]))
    return out


def winning_trifectas(groups):
    if sum(len(g) for _, g in groups) < 3:
        return []
    options = [()]
    remaining = 3
    for _fo, frames in groups:
        if remaining <= 0:
            break
        take = min(len(frames), remaining)
        options = [p + q for p in options for q in itertools.permutations(frames, take)]
        remaining -= take
    return sorted(set(options))


def winning_trios(trifectas):
    return sorted(set(frozenset(t) for t in trifectas))


def winning_exactas(groups):
    # 上位2着枠だけ使う
    if sum(len(g) for _, g in groups) < 2:
        return []
    options = [()]
    remaining = 2
    for _fo, frames in groups:
        if remaining <= 0:
            break
        take = min(len(frames), remaining)
        options = [p + q for p in options for q in itertools.permutations(frames, take)]
        remaining -= take
    return sorted(set(options))


def winning_quinellas(exactas):
    return sorted(set(frozenset(e) for e in exactas))


def winning_wide_pairs(trios):
    # trio(3着以内3車の組)から2車の組み合わせを全部取り出す(ワイドの当たり)
    pairs = set()
    for t in trios:
        for p in itertools.combinations(sorted(t), 2):
            pairs.add(frozenset(p))
    return sorted(pairs)


# --- レースごとに着順→当たり目を作る ---
race_results = {}
for race_key, g in ent_df.groupby("race_key"):
    rows = list(zip(g["finish_order"], g["frame_no"]))
    groups = groups_from_finishers(rows)
    trifectas = winning_trifectas(groups)
    trios = winning_trios(trifectas)
    exactas = winning_exactas(groups)
    quinellas = winning_quinellas(exactas)
    wides = winning_wide_pairs(trios)
    race_results[race_key] = dict(
        trifecta=set("-".join(map(str, t)) for t in trifectas),
        trio=set("=".join(map(str, sorted(t))) for t in trios),
        exacta=set("-".join(map(str, e)) for e in exactas),
        quinella=set("=".join(map(str, sorted(q))) for q in quinellas),
        quinellaPlace=set("=".join(map(str, sorted(w))) for w in wides),
        n_finishers_top3=sum(len(gr) for _, gr in groups),
    )

print("races with results:", len(race_results))
missing = valid_keys - set(race_results.keys())
print("races with NO finish_order rows at all:", len(missing))

# --- combination の並び正規化(念のため。DBの表記が既に正規形か確認) ---
def norm_combo(bet_type, combo):
    sep = "=" if bet_type in ("trio", "quinella", "quinellaPlace") else "-"
    parts = re.split(r"[-=]", str(combo))
    if sep == "=":
        parts = sorted(parts, key=int)
    return sep.join(parts)

odds_df["combo_norm"] = [norm_combo(bt, c) for bt, c in zip(odds_df["bet_type"], odds_df["combination"])]
mismatch = (odds_df["combo_norm"] != odds_df["combination"]).sum()
print("combination表記の不一致(正規化差):", mismatch, "/", len(odds_df))

# --- 当たりフラグ・払戻 ---
def is_winner(row):
    rr = race_results.get(row["race_key"])
    if rr is None:
        return np.nan
    return row["combo_norm"] in rr[row["bet_type"]]

odds_df["win_flag"] = odds_df.apply(is_winner, axis=1)
odds_df["payout100"] = odds_df["odds_value"].map(payout_per_100)

odds_df = odds_df.merge(
    sample_df[["race_key", "n_entries", "grade", "race_type", "race_date", "ym", "venue_id"]],
    on="race_key", how="left",
)
odds_df["year"] = odds_df["race_date"].dt.year

with open(f"{OUT}/dataset.pkl", "wb") as f:
    pickle.dump({"odds": odds_df, "race_results": race_results, "sample": sample_df}, f)

print("saved dataset.pkl", odds_df.shape)
print(odds_df.groupby("bet_type")["win_flag"].agg(["sum", "count"]))
