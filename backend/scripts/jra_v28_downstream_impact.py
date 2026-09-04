"""v28（`feat` 特徴 + 独立 is_placed ヘッド）にしたとき **下流の閾値がどう動くか** の実測。

事前登録: `docs/jra_winplace_structure_plan_2026_09_04.md` §16.3 / §18.3。
**閾値を1つも動かさない。** 既存の定数（`HIGHODDS_MAX_PP_RANK=2` /
`SWEET_SPOT_*` / tier の 65・80）のまま、`prod` と `new` で何が変わるかを数える。

## 腕

`scripts/jra_winplace_final_confirm.fit_arms` を **import して**そのまま使う
（🔴 再実装しない）。`prod` = 現行34特徴 → is_win → Harville /
`new` = `feat` 特徴 → is_win / 独立 is_placed ヘッド → Σ=place_slots。

## 🔴 下流コードは import して呼ぶ（判定ロジックを書き写さない）

- `src.indices.buy_signal.is_sweet_spot` / `jra_is_place_axis` / `jra_horse_purchase_signal`
- `src.indices.confidence.calculate_race_confidence` / `calculate_recommend_rank`
  / `is_market_favorite` / `calculate_market_chaos` / `JRA_GAP_FULL_SCORE`
- `src.indices.dm_signals.compute_dm_signals`
- `src.betting.place_ev.get_place_ev_model`
- 外部指数（穴ぐさ / netkeiba / kichiuma）の取得は
  `scripts.jra_verify_signals.fetch_external` を import

## 🔴 呼び出し元を辿って分かったこと（定数の存在＝使われている証拠ではない）

`grep` ではなく `src/` 全体の参照を辿った結果:

| 参照元 | 実際の状態 |
|---|---|
| `buy_signal.is_sweet_spot` | **`place_probability` を一切見ない**（`win_probability`×単勝オッズ の EV とバッジのみ）。本番呼び出しは `api/races.py:1221` |
| `buy_signal.jra_is_place_axis`（`HIGHODDS_MAX_PP_RANK`） | `src/` に**呼び出し元が無い**（tests のみ）。`recommender.py:403` が `place_prob_rank` を作るが**誰も読んでいない** |
| `indices/upset_reranker.py`（JRA） | `src/` に**呼び出し元が無い**。`chihou_upset.py` が `_rank_desc` だけ import。`pp` は生値で使う設計で、`pp` の**レース内 rank は使っていない** |
| `betting/place_ev.py` | **生きている**（`api/races.py:1304` / `services/recommender.py:775`）。しかも `pp`（生値）と **`pp_rank`（レース内 rank）の両方を特徴に持つ**。§16.3 の「DB は読まない」は誤り |
| `confidence.calculate_race_confidence` | **`win_probabilities` を受け取り「勝率集中スコア」15点を作る**。`composite_index` だけではない ⇒ tier は理屈の上でも動きうる |

したがって本スクリプトは「§16.3 の表のとおり」ではなく **実際の呼び出し元**を測る。
比較の便宜のため、呼び出し元が無い `jra_is_place_axis` / `pp_rank≤2` も併記する
（＝もし将来配線されたらどうなるか）。

## オッズ

🔴 **発走前オッズのみ**（`jra_prob_scoring.PRERACE_ODDS_SQL`・既定 60 分以内）。
確定オッズ・確定人気は使わない。`keiba.odds_history` は **2026-03-28 以降**にしか
無いので、2026Q1 はオッズ依存の指標（sweet_spot / tier / place_ev）が測れない。
その窓は対象から外し、オッズ非依存の指標（pp_rank / place_probability 分布）だけ出す。

## 使い方

    cd backend
    .venv/bin/python scripts/jra_v28_downstream_impact.py \
        --out ../docs/model_verification/jra_v28_downstream_impact.json

冪等。`--cache` / `--pred-cache-dir` に pickle を指定すると再実行が速い。
TEST 台帳へは追記しない（2026Q3 は §18 で既に消費済み・本スクリプトは
採否判断ではなく実装前の影響計測）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# --- 既存の測定基盤（🔴 再実装しない） -------------------------------------------
from scripts.jra_prob_scoring import (  # noqa: E402
    JRA_COURSES,
    PRERACE_ODDS_SQL,
    _connect,
    _query,
)
from scripts.jra_verify_signals import _JRA_TO_SEKITO, fetch_external  # noqa: E402
from scripts.jra_winplace_feature_ab import build_dataset  # noqa: E402
from scripts.jra_winplace_final_confirm import (  # noqa: E402
    ARMS,
    PLACE_COL,
    WIN_COL,
    fit_arms,
)

# --- 本番の下流コード（🔴 import して呼ぶ・書き写さない） -------------------------
from src.betting.place_ev import SUB_INDEX_COLUMNS, get_place_ev_model  # noqa: E402
from src.indices.buy_signal import (  # noqa: E402
    HIGHODDS_MAX_PP_RANK,
    is_sweet_spot,
    jra_horse_purchase_signal,
    jra_is_place_axis,
)
from src.indices.composite import COMPOSITE_VERSION  # noqa: E402
from src.indices.confidence import (  # noqa: E402
    JRA_GAP_FULL_SCORE,
    calculate_market_chaos,
    calculate_race_confidence,
    calculate_recommend_rank,
    is_market_favorite,
)
from src.indices.dm_signals import compute_dm_signals  # noqa: E402
from src import jra_protocol  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v28_downstream")

OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_v28_downstream_impact.json"

# 窓（§タスク）。主 = 2026Q3（§18 で消費済み）/ 参考 = 2026Q1・2026Q2（VAL）
WINDOWS: list[tuple[str, str, str]] = [
    ("2026Q1", "20260101", "20260331"),
    ("2026Q2", "20260401", "20260630"),
    ("2026Q3", "20260701", "20260830"),
]

# 🔴 **本番 v27 の指数**を引く（このスクリプトは v27 を prod 腕として使う）。
#    composite_index は v28 でも変えない設計なので、両腕がこの同じ v27 行を共有する。
#
# ⚠️ ここに `COMPOSITE_VERSION` を書いてはいけない。本 PR で 27 → 28 に上がったため、
#    `COMPOSITE_VERSION` を使うと「まだ DB に存在しない v28 行」を引きに行き、
#    v27 指数が 1 行も取れず全レースが落ちて 0 件になる（レビュー指摘5）。
#    「学習ソースの版を固定しない」（`composite.SUBINDEX_SOURCE_SQL`）とは逆で、
#    **比較の prod 腕は特定の版に固定するのが正しい**。両者を混同しないこと。
PROD_CI_VERSION = 27

CI_SQL = f"""
SELECT ci.race_id, ci.horse_id, ci.composite_index,
       ci.speed_index, ci.adjusted_speed_index, ci.last_3f_index, ci.course_aptitude,
       ci.distance_aptitude, ci.position_advantage, ci.jockey_index, ci.pace_index,
       ci.rotation_index, ci.rebound_index, ci.career_phase_index, ci.distance_change_index
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
WHERE ci.version = %(ver)s
  AND r.course IN {JRA_COURSES}
  AND r.date BETWEEN %(start)s AND %(end)s
"""

CI_COLS = ["composite_index", *SUB_INDEX_COLUMNS]
# 🔴 データセット側にも同名のサブ指数列がある（`prod_featurize` の fillna(50.0) 済み）。
#    本番 `place_ev` は `calculated_indices` の**生値**を渡すので、衝突を避けて別名で持つ。
V27 = "v27_"


# ---------------------------------------------------------------------------
# 補助データ（発走前オッズ / v27 指数 / 外部指数）
# ---------------------------------------------------------------------------

def attach_side_data(ev: pd.DataFrame, start: str, end: str,
                     max_lead_min: float) -> tuple[pd.DataFrame, dict, dict]:
    """評価行に v27 指数・発走前オッズを結合し、外部指数の辞書を返す。"""
    conn = _connect()
    ci = _query(conn, CI_SQL, {"ver": PROD_CI_VERSION, "start": start, "end": end})
    if not len(ci):
        conn.close()
        raise SystemExit(
            f"🔴 データ無し: calculated_indices に version={PROD_CI_VERSION} の行が "
            f"{start}〜{end} に1件も無い。prod 腕（本番 v27 指数）が作れないので中止する"
        )
    od = _query(conn, PRERACE_ODDS_SQL, {"courses": JRA_COURSES, "start": start,
                                         "end": end, "max_lead": max_lead_min})
    ext = fetch_external(conn, start, end)
    conn.close()

    d = ev.copy()
    d["horse_number"] = pd.to_numeric(d["horse_number"], errors="coerce")

    n0 = len(d)
    for c in CI_COLS:
        ci[c] = pd.to_numeric(ci[c], errors="coerce")
    ci = ci.drop_duplicates(subset=["race_id", "horse_id"], keep="first")
    ci = ci.rename(columns={c: V27 + c for c in CI_COLS})
    d = d.merge(ci, on=["race_id", "horse_id"], how="left")
    if len(d) != n0:
        raise SystemExit(f"v27 指数の結合で行数が変わった（{n0} → {len(d)}）")

    if len(od):
        od["horse_number"] = pd.to_numeric(od["combination"], errors="coerce")
        od["pre_odds"] = pd.to_numeric(od["odds"], errors="coerce")
        od = od.dropna(subset=["horse_number"])
        od = od.drop_duplicates(subset=["race_id", "horse_number"], keep="first")
        lead = od.groupby("race_id")["lead_min"].first().astype(float)
        d = d.merge(od[["race_id", "horse_number", "pre_odds"]],
                    on=["race_id", "horse_number"], how="left")
        if len(d) != n0:
            raise SystemExit(f"発走前オッズの結合で行数が変わった（{n0} → {len(d)}）")
    else:
        d["pre_odds"] = np.nan
        lead = pd.Series(dtype=float)

    # 🔴 「全馬そろったレース」だけをオッズ依存の判定対象にする
    #    （1頭でも欠けると人気順・市場一致・entropy が本番と別物になる）
    ok = d.groupby("race_id")["pre_odds"].transform(lambda s: s.notna().all() & (s > 0).all())
    d["odds_ok"] = ok.fillna(False).astype(bool)

    odds_info = {
        "max_lead_min": max_lead_min,
        "n_races": int(d["race_id"].nunique()),
        "n_races_full_prerace_odds": int(d.loc[d["odds_ok"], "race_id"].nunique()),
        "coverage_pct": round(100 * d.loc[d["odds_ok"], "race_id"].nunique()
                              / max(1, d["race_id"].nunique()), 2),
        "lead_min_median": round(float(lead.median()), 2) if len(lead) else None,
    }
    return d, ext, odds_info


# ---------------------------------------------------------------------------
# 1レース分の下流判定（🔴 本番関数を呼ぶだけ）
# ---------------------------------------------------------------------------

def race_downstream(g: pd.DataFrame, ext_race: dict, arm: str,
                    place_ev_model: Any) -> dict:
    """1レース・1腕ぶんの下流判定を返す。

    `g` は composite_index 降順にソート済みであること（本番 `races.py` と同じ順序で
    `composite_rank = index + 1` を振るため）。
    """
    n = len(g)
    hns = [int(x) for x in g["horse_number"]]
    comp = [float(x) for x in g[V27 + "composite_index"]]
    odds = {int(r.horse_number): float(r.pre_odds) for r in g.itertuples()
            if pd.notna(r.pre_odds)}
    wp = {int(r.horse_number): float(getattr(r, WIN_COL[arm])) for r in g.itertuples()}
    pp = {int(r.horse_number): float(getattr(r, PLACE_COL[arm])) for r in g.itertuples()}

    comp_rank = {hn: i + 1 for i, hn in enumerate(hns)}
    # place_probability レース内順位（本番 recommender.py:393-403 と同じ作り方）
    pp_sorted = sorted(hns, key=lambda h: pp[h], reverse=True)
    pp_rank = {hn: i + 1 for i, hn in enumerate(pp_sorted)}

    # --- DM シグナル（腕に依存しない。本番 races.py と同じ入力で呼ぶ） ---
    objs = [SimpleNamespace(
        horse_number=hn,
        composite_index=comp[i],
        jvan_time_dm=(float(g["jvan_time_dm__raw"].iloc[i])
                      if pd.notna(g["jvan_time_dm__raw"].iloc[i]) else None),
        jvan_battle_dm=(float(g["jvan_battle_dm__raw"].iloc[i])
                        if pd.notna(g["jvan_battle_dm__raw"].iloc[i]) else None),
        anagusa_rank=ext_race.get(hn, {}).get("anagusa_rank"),
        nb_ave_rank=ext_race.get(hn, {}).get("nb_ave_rank"),
        km_rank=ext_race.get(hn, {}).get("km_rank"),
        dm_signals=None,
    ) for i, hn in enumerate(hns)]
    compute_dm_signals(objs, win_odds_map=odds)
    dm = {o.horse_number: (o.dm_signals or []) for o in objs}

    top2_t3_gap = (comp[1] - comp[2]) if n >= 3 else None

    rows: list[dict] = []
    for i, hn in enumerate(hns):
        e = ext_race.get(hn, {})
        rank = comp_rank[hn]
        ps = jra_horse_purchase_signal(
            rank=rank,
            top2_t3_gap=top2_t3_gap if rank <= 2 else None,
            win_odds=odds.get(hn),
        )
        ss = is_sweet_spot(
            win_odds=odds.get(hn),
            win_probability=wp[hn],
            composite_rank=rank,
            dm_signals=dm[hn],
            purchase_signal=ps,
            anagusa_rank=e.get("anagusa_rank"),
            nb_course_rank=e.get("nb_course_rank"),
            nb_ave_rank=e.get("nb_ave_rank"),
            km_rank=e.get("km_rank"),
        )
        axis = jra_is_place_axis(
            win_odds=odds.get(hn),
            composite_rank=rank,
            place_prob_rank=pp_rank[hn],
            anagusa_rank=e.get("anagusa_rank"),
            nb_ave_rank=e.get("nb_ave_rank"),
            km_rank=e.get("km_rank"),
            dm_signals=dm[hn],
        )
        rows.append({
            "horse_number": hn, "composite_rank": rank, "pp_rank": pp_rank[hn],
            "p_win": wp[hn], "p_place": pp[hn],
            "ev_win": (wp[hn] * odds[hn]) if hn in odds else None,
            "sweet_spot": bool(ss), "place_axis": bool(axis),
        })

    # 🔴 本番 races.py:1243-1246 と同じ「3頭以上該当なら全取消」
    if sum(r["sweet_spot"] for r in rows) >= 3:
        for r in rows:
            r["sweet_spot"] = False

    # --- tier（confidence.py・🔴 win_probabilities を渡すのが本番） ---
    head_count = int(g["head_count"].iloc[0]) if pd.notna(g["head_count"].iloc[0]) else n
    conf = calculate_race_confidence(
        comp, head_count, [wp[hn] for hn in hns] or None,
        gap_full_score=JRA_GAP_FULL_SCORE,
    )
    top_odds = odds.get(hns[0])
    all_odds = [odds[h] for h in hns if h in odds]
    market_agree = is_market_favorite(top_odds, all_odds or None)
    entropy_norm = calculate_market_chaos(all_odds).get("entropy_norm")
    tier = calculate_recommend_rank(conf["score"], conf.get("win_prob_top"),
                                    top_odds, market_agree, entropy_norm)

    # --- place_ev モデル（🔴 生きている唯一の place_probability 消費者） ---
    pick_hn = None
    pick_p = None
    if place_ev_model is not None:
        ev_inputs = []
        for i, hn in enumerate(hns):
            e = ext_race.get(hn, {})
            d: dict[str, Any] = {
                "horse_number": hn,
                "win_odds": odds.get(hn),
                # 🔴 発走前判定なので確定複勝オッズは渡さない（近似 odds_impute を使わせる）
                "place_odds": None,
                "composite_index": comp[i],
                "win_probability": wp[hn],
                "place_probability": pp[hn],
                "surface": g["surface"].iloc[i],
                "distance": (float(g["distance"].iloc[i])
                             if pd.notna(g["distance"].iloc[i]) else None),
                "anagusa_rank": e.get("anagusa_rank"),
                "nb_ave_rank": e.get("nb_ave_rank"),
                "km_rank": e.get("km_rank"),
                "jvan_time_dm": objs[i].jvan_time_dm,
                "jvan_battle_dm": objs[i].jvan_battle_dm,
            }
            for c in SUB_INDEX_COLUMNS:
                v = g[V27 + c].iloc[i]
                d[c] = float(v) if pd.notna(v) else None
            ev_inputs.append(d)
        pick = place_ev_model.pick_race(ev_inputs, head_count)
        if pick is not None:
            pick_hn, pick_p = int(pick["horse_number"]), float(pick["place_probability"])

    return {
        "rows": rows,
        "confidence_score": int(conf["score"]),
        "tier": tier,
        "market_agree": market_agree,
        "place_ev_pick": pick_hn,
        "place_ev_pick_prob": pick_p,
    }


# ---------------------------------------------------------------------------
# 🔴 目視確認: prod / new を1レース並置する（CLAUDE.md「baseline は1件表示して目視」）
# ---------------------------------------------------------------------------

def visual_check(g: pd.DataFrame, res: dict[str, dict], label: str) -> list[str]:
    r0 = g.iloc[0]
    by_arm = {a: {x["horse_number"]: x for x in res[a]["rows"]} for a in ARMS}
    L = ["", "=" * 138]
    L.append(f"🔴 目視確認（{label}） race_id={int(r0['race_id'])} {r0['date']} "
             f"{r0['course_name']}{int(r0['race_number'])}R {r0['race_name']} "
             f"n={len(g)} place_slots={int(r0['place_slots'])}")
    L.append(f"   tier: prod={res['prod']['tier']}(conf={res['prod']['confidence_score']}) "
             f"/ new={res['new']['tier']}(conf={res['new']['confidence_score']})"
             f"  ｜ market_agree={res['prod']['market_agree']}"
             f"  ｜ place_ev pick: prod={res['prod']['place_ev_pick']} "
             f"/ new={res['new']['place_ev_pick']}")
    L.append("=" * 138)
    L.append(f"{'馬番':>4}{'着':>4}{'CI順':>5}{'発走前O':>9}"
             f"{'p_win prod':>12}{'p_win new':>11}"
             f"{'p_place prod':>13}{'p_place new':>12}"
             f"{'pp順 prod':>10}{'pp順 new':>9}"
             f"{'sweet p/n':>11}{'k<=2軸 p/n':>12}")
    for _, r in g.sort_values("horse_number").iterrows():
        hn = int(r["horse_number"])
        p, nw = by_arm["prod"][hn], by_arm["new"][hn]
        po = f"{float(r['pre_odds']):>9.1f}" if pd.notna(r["pre_odds"]) else f"{'-':>9}"
        L.append(f"{hn:>4}{int(r['finish_position']):>4}{p['composite_rank']:>5}{po}"
                 f"{p['p_win']:>12.5f}{nw['p_win']:>11.5f}"
                 f"{p['p_place']:>13.5f}{nw['p_place']:>12.5f}"
                 f"{p['pp_rank']:>10}{nw['pp_rank']:>9}"
                 f"{('○' if p['sweet_spot'] else '×') + '/' + ('○' if nw['sweet_spot'] else '×'):>13}"
                 f"{('○' if p['place_axis'] else '×') + '/' + ('○' if nw['place_axis'] else '×'):>14}")
    L.append(f"{'Σ':>4}{'':>4}{'':>5}{'':>9}"
             f"{sum(x['p_win'] for x in by_arm['prod'].values()):>12.5f}"
             f"{sum(x['p_win'] for x in by_arm['new'].values()):>11.5f}"
             f"{sum(x['p_place'] for x in by_arm['prod'].values()):>13.5f}"
             f"{sum(x['p_place'] for x in by_arm['new'].values()):>12.5f}")
    L.append(f"（期待: Σp_win=1.00000（両腕）/ Σp_place={int(r0['place_slots'])}.00000（両腕））")
    return L


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def summarize(recs: pd.DataFrame, odds_ok_only: bool) -> dict:
    """馬単位のレコードから下流指標を集計する。"""
    if not len(recs):     # 列すら無い空 DataFrame が来ても KeyError にしない
        return {"n_horses": 0, "n_races": 0, "note": "データ無し"}
    d = recs[recs["odds_ok"]] if odds_ok_only else recs
    out: dict[str, Any] = {"n_horses": int(len(d)), "n_races": int(d["race_id"].nunique())}
    if not len(d):
        return out

    placed = d["is_placed"].to_numpy(dtype=bool)
    won = d["is_win"].to_numpy(dtype=bool)

    # ---- (1) sweet_spot ----
    sp, sn = d["sweet_spot__prod"].to_numpy(bool), d["sweet_spot__new"].to_numpy(bool)
    both, only_p, only_n = int((sp & sn).sum()), int((sp & ~sn).sum()), int((~sp & sn).sum())
    union = both + only_p + only_n
    out["sweet_spot"] = {
        "n_prod": int(sp.sum()), "n_new": int(sn.sum()),
        "both": both, "prod_only": only_p, "new_only": only_n,
        "union": union,
        "jaccard": _rate(both, union),
        "turnover_rate_vs_prod": _rate(only_p + only_n, int(sp.sum())),
        "n_races_with_any_prod": int(d.loc[sp, "race_id"].nunique()),
        "n_races_with_any_new": int(d.loc[sn, "race_id"].nunique()),
        "place_rate_prod": _rate(int(placed[sp].sum()), int(sp.sum())),
        "place_rate_new": _rate(int(placed[sn].sum()), int(sn.sum())),
        "win_rate_prod": _rate(int(won[sp].sum()), int(sp.sum())),
        "win_rate_new": _rate(int(won[sn].sum()), int(sn.sum())),
        "place_rate_prod_only": _rate(int(placed[sp & ~sn].sum()), only_p),
        "place_rate_new_only": _rate(int(placed[~sp & sn].sum()), only_n),
    }

    # ---- (2) pp_rank ≤ 2（HIGHODDS_MAX_PP_RANK）のメンバー入れ替え ----
    kp = (d["pp_rank__prod"] <= HIGHODDS_MAX_PP_RANK).to_numpy(bool)
    kn = (d["pp_rank__new"] <= HIGHODDS_MAX_PP_RANK).to_numpy(bool)
    kboth, konly_p, konly_n = int((kp & kn).sum()), int((kp & ~kn).sum()), int((~kp & kn).sum())
    changed_races = (d.assign(_p=kp, _n=kn).groupby("race_id")
                     .apply(lambda x: bool((x["_p"] != x["_n"]).any()), include_groups=False))
    out["pp_rank_top2_membership"] = {
        "threshold": HIGHODDS_MAX_PP_RANK,
        "n_prod": int(kp.sum()), "n_new": int(kn.sum()),
        "both": kboth, "prod_only": konly_p, "new_only": konly_n,
        "member_turnover_rate": _rate(konly_p, int(kp.sum())),
        "n_races": int(len(changed_races)),
        "n_races_membership_changed": int(changed_races.sum()),
        "pct_races_membership_changed": _rate(int(changed_races.sum()), int(len(changed_races))),
        "place_rate_prod": _rate(int(placed[kp].sum()), int(kp.sum())),
        "place_rate_new": _rate(int(placed[kn].sum()), int(kn.sum())),
        "place_rate_prod_only": _rate(int(placed[kp & ~kn].sum()), konly_p),
        "place_rate_new_only": _rate(int(placed[~kp & kn].sum()), konly_n),
    }

    # ---- (2b) jra_is_place_axis（呼び出し元は無いが k≤2 を実際に使う唯一の関数） ----
    ap, an = d["place_axis__prod"].to_numpy(bool), d["place_axis__new"].to_numpy(bool)
    a_both, a_p, a_n = int((ap & an).sum()), int((ap & ~an).sum()), int((~ap & an).sum())
    out["place_axis"] = {
        "note": "🔴 src/ に呼び出し元が無い（tests のみ）。将来配線された場合の参考値",
        "n_prod": int(ap.sum()), "n_new": int(an.sum()),
        "both": a_both, "prod_only": a_p, "new_only": a_n,
        "turnover_rate_vs_prod": _rate(a_p + a_n, int(ap.sum())),
        "place_rate_prod": _rate(int(placed[ap].sum()), int(ap.sum())),
        "place_rate_new": _rate(int(placed[an].sum()), int(an.sum())),
    }

    # ---- (3) pp_rank の動き ----
    diff = (d["pp_rank__new"] - d["pp_rank__prod"]).to_numpy(dtype=int)
    ad = np.abs(diff)
    hist = {str(k): int((ad == k).sum()) for k in range(0, 6)}
    hist["6+"] = int((ad >= 6).sum())
    out["pp_rank_shift"] = {
        "n_horses": int(len(diff)),
        "n_moved": int((ad > 0).sum()),
        "pct_moved": _rate(int((ad > 0).sum()), int(len(diff))),
        "abs_shift_hist": hist,
        "abs_shift_mean": round(float(ad.mean()), 4),
        "abs_shift_p50": int(np.percentile(ad, 50)),
        "abs_shift_p90": int(np.percentile(ad, 90)),
        "abs_shift_max": int(ad.max()),
        "n_races_rank1_changed": int(
            d.assign(_p=d["pp_rank__prod"] == 1, _n=d["pp_rank__new"] == 1)
            .groupby("race_id").apply(lambda x: bool((x["_p"] != x["_n"]).any()),
                                      include_groups=False).sum()),
    }

    # ---- (4) place_probability そのものの分布 ----
    dist: dict[str, Any] = {}
    for a in ARMS:
        col = d[f"p_place__{a}"]
        r1 = d.loc[d[f"pp_rank__{a}"] == 1, f"p_place__{a}"]
        dist[a] = {
            "mean_all": round(float(col.mean()), 5),
            "std_all": round(float(col.std()), 5),
            "rank1_mean": round(float(r1.mean()), 5),
            "rank1_p10": round(float(r1.quantile(0.10)), 5),
            "rank1_p50": round(float(r1.quantile(0.50)), 5),
            "rank1_p90": round(float(r1.quantile(0.90)), 5),
            "rank1_max": round(float(r1.max()), 5),
            "n_gt_1": int((col > 1.0).sum()),
            "rank1_actual_place_rate": _rate(
                int(d.loc[d[f"pp_rank__{a}"] == 1, "is_placed"].sum()), int(len(r1))),
        }
    out["place_probability_dist"] = dist

    # ---- (5) 実複勝率（pp 上位 k のカバレッジ） ----
    cov: dict[str, Any] = {}
    for a in ARMS:
        for k in (1, 2, 3):
            m = (d[f"pp_rank__{a}"] <= k).to_numpy(bool)
            cov.setdefault(a, {})[f"top{k}"] = {
                "n": int(m.sum()),
                "place_rate": _rate(int(placed[m].sum()), int(m.sum())),
                "win_rate": _rate(int(won[m].sum()), int(m.sum())),
            }
    out["pp_topk_hit"] = cov
    return out


def summarize_race_level(rl: pd.DataFrame, odds_ok_only: bool) -> dict:
    if not len(rl):       # 列すら無い空 DataFrame が来ても KeyError にしない
        return {"n_races": 0, "note": "データ無し"}
    d = rl[rl["odds_ok"]] if odds_ok_only else rl
    out: dict[str, Any] = {"n_races": int(len(d))}
    if not len(d):
        return out
    tiers = ["S", "A", "B", "C+", "C"]
    out["tier_dist"] = {
        a: {t: int((d[f"tier__{a}"] == t).sum()) for t in tiers} for a in ARMS
    }
    out["tier_dist_pct"] = {
        a: {t: _rate(int((d[f"tier__{a}"] == t).sum()), len(d)) for t in tiers} for a in ARMS
    }
    ch = d[f"tier__prod"] != d[f"tier__new"]
    out["tier_changed_races"] = int(ch.sum())
    out["tier_changed_pct"] = _rate(int(ch.sum()), len(d))
    out["tier_transitions"] = (
        d.loc[ch].groupby([f"tier__prod", f"tier__new"]).size().to_dict()
        if ch.any() else {}
    )
    out["tier_transitions"] = {f"{k[0]}->{k[1]}": int(v)
                               for k, v in out["tier_transitions"].items()}
    cs = (d["conf__new"] - d["conf__prod"]).to_numpy(dtype=int)
    out["confidence_score_delta"] = {
        "n_changed": int((cs != 0).sum()),
        "pct_changed": _rate(int((cs != 0).sum()), len(d)),
        "mean": round(float(cs.mean()), 4),
        "abs_p50": int(np.percentile(np.abs(cs), 50)),
        "abs_p90": int(np.percentile(np.abs(cs), 90)),
        "abs_max": int(np.abs(cs).max()),
    }
    # place_ev の1頭推奨がどれだけ入れ替わるか
    pe = d[d["pe_pick__prod"].notna() | d["pe_pick__new"].notna()]
    same = (pe["pe_pick__prod"] == pe["pe_pick__new"]).sum()
    out["place_ev_pick"] = {
        "n_races_any_pick": int(len(pe)),
        "n_pick_prod": int(d["pe_pick__prod"].notna().sum()),
        "n_pick_new": int(d["pe_pick__new"].notna().sum()),
        "n_same_horse": int(same),
        "pct_changed": _rate(int(len(pe) - same), int(len(pe))),
        "hit_rate_prod": _rate(int(d["pe_hit__prod"].fillna(False).sum()),
                               int(d["pe_pick__prod"].notna().sum())),
        "hit_rate_new": _rate(int(d["pe_hit__new"].fillna(False).sum()),
                              int(d["pe_pick__new"].notna().sum())),
    }
    # tier 別の指数1位馬の勝率／複勝率（tier が動いたときの意味を見る）
    out["tier_top1_hit"] = {}
    for a in ARMS:
        out["tier_top1_hit"][a] = {
            t: {"n": int(((d[f"tier__{a}"] == t)).sum()),
                "win_rate": _rate(int(d.loc[d[f"tier__{a}"] == t, "top1_win"].sum()),
                                  int((d[f"tier__{a}"] == t).sum())),
                "place_rate": _rate(int(d.loc[d[f"tier__{a}"] == t, "top1_placed"].sum()),
                                    int((d[f"tier__{a}"] == t).sum()))}
            for t in tiers
        }
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--windows", default="2026Q1,2026Q2,2026Q3")
    p.add_argument("--data-start", default="20230101")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--max-lead-min", type=float, default=60.0)
    p.add_argument("--cache", default=None, help="データセット pickle（冪等・再利用可）")
    p.add_argument("--pred-cache-dir", default=None, help="窓ごとの予測 pickle 置き場")
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    want = [w.strip() for w in args.windows.split(",")]
    windows = [w for w in WINDOWS if w[0] in want]
    if not windows:
        raise SystemExit(f"--windows の指定に該当する窓が無い: {args.windows}")

    logger.info("プロトコル: %s", jra_protocol.describe())
    logger.info("🔴 閾値は動かさない: HIGHODDS_MAX_PP_RANK=%d / tier 閾値 65,80 / "
                "SWEET_SPOT EV[1.2,5.0] odds>=10", HIGHODDS_MAX_PP_RANK)

    data_end = max(w[2] for w in windows)
    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info("データセットをキャッシュから読込: %s (%d行)", cache, len(df))
    else:
        df = build_dataset(args.data_start, data_end)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache)

    place_ev_model = get_place_ev_model()
    if place_ev_model is None:
        raise SystemExit("place_ev_model.v1.json が無い。下流の主要消費者が測れない")
    logger.info("place_ev モデル: trained_at=%s floor=%.2f",
                place_ev_model.trained_at, place_ev_model.floor)

    results: dict[str, Any] = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "purpose": "docs/jra_winplace_structure_plan_2026_09_04.md §16.3 / §18.3 の"
                   "「下流の閾値がどう動くか」の実測（閾値の探索・最適化はしない）",
        "protocol": jra_protocol.describe(),
        "config": {
            "arms": list(ARMS),
            "windows": [w[0] for w in windows],
            "data_start": args.data_start, "seeds": seeds,
            "valid_days": args.valid_days, "max_lead_min": args.max_lead_min,
            # 🔴 prod 腕は「本番 v27 の指数」で固定。COMPOSITE_VERSION（=28）ではない
            "prod_ci_version": PROD_CI_VERSION,
            "composite_version_in_code": COMPOSITE_VERSION,
            "frozen_thresholds": {
                "HIGHODDS_MAX_PP_RANK": HIGHODDS_MAX_PP_RANK,
                "tier_confidence_cuts": [65, 80],
                "sweet_spot": "odds>=10 / EV in [1.2,5.0] / badge / race-level k<3 cancel",
                "place_ev_floor": place_ev_model.floor,
            },
            "odds": "🔴 発走前オッズのみ（odds_history・確定オッズと確定人気は不使用）",
        },
        "call_site_audit": {
            "is_sweet_spot": "api/races.py:1221（本番）。place_probability は**読まない**"
                             "（win_probability × 発走前オッズ の EV とバッジのみ）",
            "jra_is_place_axis__HIGHODDS_MAX_PP_RANK":
                "🔴 src/ に呼び出し元が無い（tests のみ）。recommender.py:403 が作る "
                "place_prob_rank も src/ の誰も読んでいない",
            "upset_reranker(JRA)": "🔴 src/ に呼び出し元が無い。chihou_upset.py が "
                                   "_rank_desc だけ import。pp は生値で使う設計で "
                                   "レース内 rank は作っていない（§16.3 の記述と異なる）",
            "place_ev": "🔴 生きている（api/races.py:1304 / services/recommender.py:775）。"
                        "pp（生値）と pp_rank（レース内 rank）の**両方**を特徴に持つ。"
                        "§16.3 の「DB は読まない」は誤り",
            "confidence.calculate_race_confidence":
                "🔴 win_probabilities を受け取り「勝率集中スコア」15点を作る。"
                "composite_index だけではないので tier は理屈の上でも動きうる",
        },
        "windows": {},
    }

    all_horse: list[pd.DataFrame] = []
    all_race: list[pd.DataFrame] = []

    for label, w_start, w_end in windows:
        train_end = (pd.to_datetime(w_start) - pd.Timedelta(days=1)).strftime("%Y%m%d")
        train = df[df["date"] <= train_end]
        te0 = df[(df["date"] >= w_start) & (df["date"] <= w_end)]
        if train.empty or te0.empty:
            logger.warning("窓 %s: train=%d eval=%d のため skip", label, len(train), len(te0))
            continue
        logger.info("=== 窓 %s: 学習 ≤%s (%d行/%dR) / 評価 %s〜%s (%d行/%dR) ===",
                    label, train_end, len(train), train["race_id"].nunique(),
                    te0["date"].min(), te0["date"].max(), len(te0), te0["race_id"].nunique())

        pc = Path(args.pred_cache_dir) / f"pred_{label}.pkl" if args.pred_cache_dir else None
        if pc and pc.exists():
            ev = pd.read_pickle(pc)
            fit_info = json.loads(Path(str(pc) + ".info.json").read_text())
            logger.info("  予測をキャッシュから読込: %s", pc)
        else:
            ev, fit_info = fit_arms(train, te0, seeds, args.valid_days, w_start)
            if pc:
                pc.parent.mkdir(parents=True, exist_ok=True)
                pd.to_pickle(ev, pc)
                Path(str(pc) + ".info.json").write_text(json.dumps(fit_info, ensure_ascii=False))
        ev = ev.reset_index(drop=True)

        ev, ext, odds_info = attach_side_data(ev, w_start, w_end, args.max_lead_min)
        # v27 指数が引けない行があるレースは下流判定が本番と別物になるので落とす
        ci_ok = ev.groupby("race_id")[V27 + "composite_index"].transform(lambda s: s.notna().all())
        n_drop_races = int(ev.loc[~ci_ok, "race_id"].nunique())
        ev = ev[ci_ok].reset_index(drop=True)
        # 🔴 空になったら「データ無し」と報告して次の窓へ。ここを素通りさせると
        #    空の DataFrame に列が無く `KeyError: 'odds_ok'` という無関係な例外になる
        if not len(ev):
            logger.warning("  窓 %s: 🔴 データ無し（v%d 指数が引けた行が0件）。スキップする",
                           label, PROD_CI_VERSION)
            results["windows"][label] = {
                "error": f"データ無し: calculated_indices の version={PROD_CI_VERSION} が"
                         f"全レースで欠けている（{w_start}〜{w_end}）",
                "races_dropped_missing_v27_composite": n_drop_races,
                "odds": odds_info,
            }
            continue

        # --- 自己検査（Σ）---
        sums = ev.groupby("race_id").agg(
            **{f"w_{a}": (WIN_COL[a], "sum") for a in ARMS},
            **{f"p_{a}": (PLACE_COL[a], "sum") for a in ARMS},
            slots=("place_slots", "first"))
        checks = {
            **{f"max_abs_dev_p_win_sum_from_1__{a}":
               round(float((sums[f"w_{a}"] - 1.0).abs().max()), 9) for a in ARMS},
            **{f"max_abs_dev_p_place_sum_from_slots__{a}":
               round(float((sums[f"p_{a}"] - sums["slots"]).abs().max()), 6) for a in ARMS},
            "races_dropped_missing_v27_composite": n_drop_races,
            "new_place_clipped_horses": fit_info["new_place_clipped_horses"],
        }
        logger.info("  自己検査: %s", checks)

        # --- レースごとに下流判定 ---
        horse_rows: list[dict] = []
        race_rows: list[dict] = []
        visual_lines: list[str] | None = None
        for rid, g in ev.groupby("race_id", sort=False):
            g = g.sort_values(V27 + "composite_index", ascending=False).reset_index(drop=True)
            date = str(g["date"].iloc[0])
            rn = int(g["race_number"].iloc[0]) if pd.notna(g["race_number"].iloc[0]) else None
            key = (f"{date[:4]}-{date[4:6]}-{date[6:]}",
                   _JRA_TO_SEKITO.get(str(g["course"].iloc[0])), rn)
            ext_race = ext.get(key, {})
            odds_ok = bool(g["odds_ok"].iloc[0])

            res = {a: race_downstream(g, ext_race, a, place_ev_model) for a in ARMS}

            # 🔴 窓ごとに1レースだけ prod / new を並置して目視する（JSON にも残す）
            if visual_lines is None and odds_ok and int(g["place_slots"].iloc[0]) == 3 \
                    and len(g) >= 12 and any(r["sweet_spot"] for r in res["prod"]["rows"]):
                visual_lines = visual_check(g, res, label)
                print("\n".join(visual_lines))

            slots = int(g["place_slots"].iloc[0])
            fin = {int(r.horse_number): int(r.finish_position) for r in g.itertuples()}
            by_arm = {a: {x["horse_number"]: x for x in res[a]["rows"]} for a in ARMS}
            for hn, fp in fin.items():
                row = {"window": label, "race_id": int(rid), "horse_number": hn,
                       "odds_ok": odds_ok, "place_slots": slots,
                       "is_placed": bool(slots > 0 and fp <= slots), "is_win": bool(fp == 1)}
                for a in ARMS:
                    x = by_arm[a][hn]
                    row[f"p_win__{a}"] = x["p_win"]
                    row[f"p_place__{a}"] = x["p_place"]
                    row[f"pp_rank__{a}"] = x["pp_rank"]
                    row[f"sweet_spot__{a}"] = x["sweet_spot"]
                    row[f"place_axis__{a}"] = x["place_axis"]
                horse_rows.append(row)

            top1_hn = int(g["horse_number"].iloc[0])
            rr = {"window": label, "race_id": int(rid), "odds_ok": odds_ok,
                  "top1_win": fin[top1_hn] == 1,
                  "top1_placed": bool(slots > 0 and fin[top1_hn] <= slots)}
            for a in ARMS:
                rr[f"tier__{a}"] = res[a]["tier"]
                rr[f"conf__{a}"] = res[a]["confidence_score"]
                pk = res[a]["place_ev_pick"]
                rr[f"pe_pick__{a}"] = pk
                rr[f"pe_hit__{a}"] = (bool(slots > 0 and fin[pk] <= slots)
                                      if pk is not None else None)
            race_rows.append(rr)

        hdf = pd.DataFrame(horse_rows)
        rdf = pd.DataFrame(race_rows)
        all_horse.append(hdf)
        all_race.append(rdf)

        results["windows"][label] = {
            "eval": {"date_min": str(ev["date"].min()), "date_max": str(ev["date"].max()),
                     "n_rows": int(len(ev)), "n_races": int(ev["race_id"].nunique())},
            "train_end": train_end,
            "fit_info": {k: v for k, v in fit_info.items() if k != "is_placed_head_dropped_rows"},
            "self_checks": checks,
            "visual_check": visual_lines,
            "odds": odds_info,
            "horse_level_odds_races": summarize(hdf, odds_ok_only=True),
            "horse_level_all_races": summarize(hdf, odds_ok_only=False),
            "race_level_odds_races": summarize_race_level(rdf, odds_ok_only=True),
        }
        logger.info("  窓 %s 完了", label)

    # --- 全窓プール ---
    if all_horse:
        H = pd.concat(all_horse, ignore_index=True)
        R = pd.concat(all_race, ignore_index=True)
        results["pooled_all_windows"] = {
            "horse_level_odds_races": summarize(H, odds_ok_only=True),
            "horse_level_all_races": summarize(H, odds_ok_only=False),
            "race_level_odds_races": summarize_race_level(R, odds_ok_only=True),
        }

    # --- 画面出力 ---
    print("\n" + "=" * 120)
    print("  【窓ごとの内訳】")
    print("=" * 120)
    for label in results["windows"]:
        w = results["windows"][label]
        if "error" in w:      # データ無しでスキップした窓
            print(f"\n── {label}  🔴 {w['error']}")
            continue
        print(f"\n── {label}  評価 {w['eval']['date_min']}〜{w['eval']['date_max']} "
              f"{w['eval']['n_races']}R / {w['eval']['n_rows']}頭 "
              f"｜ 発走前オッズ全馬そろい {w['odds']['n_races_full_prerace_odds']}"
              f"/{w['odds']['n_races']}R = {w['odds']['coverage_pct']}%")
        ho, ha = w["horse_level_odds_races"], w["horse_level_all_races"]
        rl = w["race_level_odds_races"]
        if ha.get("pp_rank_shift"):
            s = ha["pp_rank_shift"]
            print(f"   pp_rank が動いた馬: {s['n_moved']}/{s['n_horses']} "
                  f"= {s['pct_moved']:.1%}  ｜ |Δ| 平均 {s['abs_shift_mean']} "
                  f"/ p90 {s['abs_shift_p90']} / max {s['abs_shift_max']} "
                  f"｜ pp1位が入れ替わったレース {s['n_races_rank1_changed']}")
        if ho.get("sweet_spot"):
            ss = ho["sweet_spot"]
            print(f"   sweet_spot: prod {ss['n_prod']} / new {ss['n_new']} "
                  f"（両方 {ss['both']} / prod のみ {ss['prod_only']} / new のみ {ss['new_only']}）"
                  f" 入替率 {ss['turnover_rate_vs_prod']}"
                  f" ｜ 実複勝率 prod {ss['place_rate_prod']} → new {ss['place_rate_new']}")
            k = ho["pp_rank_top2_membership"]
            print(f"   pp_rank≤2: prod {k['n_prod']} / new {k['n_new']} "
                  f"（両方 {k['both']} / prod のみ {k['prod_only']} / new のみ {k['new_only']}）"
                  f" 入替率 {k['member_turnover_rate']}"
                  f" ｜ メンバーが動いたレース {k['n_races_membership_changed']}/{k['n_races']}"
                  f" = {k['pct_races_membership_changed']}")
        if rl.get("tier_dist"):
            print(f"   tier 分布 (n={rl['n_races']}R):")
            for a in ARMS:
                print(f"      {a:<5}" + "  ".join(
                    f"{t}={rl['tier_dist'][a][t]}({rl['tier_dist_pct'][a][t]:.1%})"
                    for t in ("S", "A", "B", "C+", "C")))
            print(f"   🔴 tier が動いたレース: {rl['tier_changed_races']}/{rl['n_races']} "
                  f"= {rl['tier_changed_pct']}  遷移: {rl['tier_transitions']}")
            cd = rl["confidence_score_delta"]
            print(f"      confidence_score が動いたレース {cd['n_changed']} "
                  f"({cd['pct_changed']}) 平均Δ {cd['mean']} / |Δ|p90 {cd['abs_p90']} "
                  f"/ |Δ|max {cd['abs_max']}")
            pe = rl["place_ev_pick"]
            print(f"   place_ev の1頭推奨: prod {pe['n_pick_prod']} / new {pe['n_pick_new']} "
                  f"｜ 馬が変わったレース {pe['pct_changed']} "
                  f"｜ 複勝的中率 prod {pe['hit_rate_prod']} → new {pe['hit_rate_new']}")
        d = ha.get("place_probability_dist") or {}
        if d:
            print(f"   place_probability: 全体平均 prod {d['prod']['mean_all']} / "
                  f"new {d['new']['mean_all']} ｜ pp1位馬 平均 "
                  f"prod {d['prod']['rank1_mean']} → new {d['new']['rank1_mean']} "
                  f"（実複勝率 prod {d['prod']['rank1_actual_place_rate']} → "
                  f"new {d['new']['rank1_actual_place_rate']}）")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def _clean(o):
        if isinstance(o, dict):
            return {str(k): _clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        return o

    out.write_text(json.dumps(_clean(results), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n保存: {out}")
    print("⚠️ 本スクリプトは TEST 台帳へ追記しない（2026Q3 は §18 で消費済み・"
          "ここでは採否判断をしていない）")


if __name__ == "__main__":
    main()
