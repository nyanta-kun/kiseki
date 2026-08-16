"""JRA「人気薄の好走馬をどれだけ拾えているか」の walk-forward honest 検証。

## なぜ必要か

「人気決着になりそうなレースを避けて、人気下位の好走馬を的確に選ぶ」を改善したい。
だが着手前に **今の指数が人気薄帯でどれだけ効いているか** が測れていない。
測らずに改善案を作ると、良し悪しを判定する基準が無い。

測り方には 2 つの落とし穴があり、どちらも過去に踏んでいる:

1. **model-vintage look-ahead** — DB の `calculated_indices` v27 は全期間 refit した
   モデルの遡及適用。人気薄 × 指数上位のような **n が小さく分散の大きい部分集合ほど
   劣化が集中する**（穴ぐさ検証: 3着内率 36.3%→24.9% / 複ROI 1.207→0.848。
   memory: jra_anagusa_top3_lookahead_2026_08_15）。
   → 四半期ごとに vintage モデルを学習し直す（`anagusa_top3_walkforward` と同じ枠組み）。

2. **人気帯を揃えない比較** — 「5番人気以下で指数最上位」を取ると 3着内率は
   10.9%→26.7% に跳ね上がるが、その大半は **5〜6番人気を選び直しているだけ**
   （composite 順位と人気順位のレース内 Spearman は 0.815 = ほぼ市場のエコー）。
   → 主指標は **人気を完全に揃えた層化差**（strata = win_popularity そのもの）にする。

## 何を測るか

  1. 人気帯（5-8 / 9-12 / 13+）ごとの ベース vs 選出 の 3着内率・ROI
  2. **人気を揃えた層化リフト**（帯内・指数上位 − 帯内・それ以外）+ レース単位 CI
  3. 市場乖離ルール（指数順位が人気順位より m 以上上）の効き
  4. 同一母集団での in-sample（DB v27）との乖離＝ look-ahead の大きさ
  5. 四半期別の安定性

⚠️ ROI を主目標にしない。控除率 20% の壁があり、地方・競輪では全セグメントが
そこへ収束した。ここでの目標は **同じ人気帯の中での識別力**（3着内率のリフト）。

⚠️ `win_popularity` は確定オッズ由来。発走 10 分前の人気とは 2 割ずれる
（`hit_tier_races` の実測）。帯の切り方としては許容するが、
**運用ルールに落とすときは発走前オッズで再確認すること**。

使い方:
    cd backend
    .venv/bin/python scripts/jra_darkhorse_walkforward.py
    .venv/bin/python scripts/jra_darkhorse_walkforward.py --out /tmp/darkhorse.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# 学習・読込・統計は穴ぐさ検証と同一実装を使い回す（vintage の切り方を二重管理しない）
from scripts.anagusa_top3_walkforward import (  # noqa: E402
    FEATURES,
    _race_z,
    add_rank,
    boot_mean,
    fit_vintage,
    load_all,
    normalized_rank,
    prepare,
    quarters,
    strat_diff,
)
from src.indices.composite import V27_OUT_WEIGHT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("darkhorse_wf")

# 人気帯の切り方。「人気薄」の定義を 1 か所に置く。
POP_BANDS: list[tuple[str, int, int]] = [
    ("1-4番人気", 1, 4),
    ("5-8番人気", 5, 8),
    ("9-12番人気", 9, 12),
    ("13番人気以下", 13, 99),
    ("5番人気以下(まとめ)", 5, 99),
]


def band_rank(ev: pd.DataFrame, mask: pd.Series, score_col: str) -> pd.Series:
    """帯の中だけで付け直したレース内順位（1 が最上位）。帯外は NaN。"""
    sub = ev[mask]
    r = sub.groupby("race_id")[score_col].rank(ascending=False, method="min")
    out = pd.Series(np.nan, index=ev.index, dtype=float)
    out.loc[sub.index] = r
    return out


def stat(sub: pd.DataFrame) -> dict:
    """3着内率・複勝ROI・勝率・単勝ROI。複勝は払戻対象頭数と払戻データの有無で絞る。"""
    fin = sub[sub["is_finisher"]]
    if fin.empty:
        return {"n": 0}
    pl = fin[fin["place_slots"] > 0]
    po = pl[pl["payout_ok"]]
    ret = po["place_odds"].fillna(0.0).where(po["place_hit"] == 1, 0.0).sum()
    wo = fin[fin["win_odds"].notna()]
    win_ret = wo["win_odds"].where(wo["win_hit"] == 1, 0.0).sum()
    return {
        "n": len(fin),
        "3着内率": round(pl["place_hit"].mean() * 100, 2) if len(pl) else np.nan,
        "複勝ROI": round(ret / len(po), 3) if len(po) else np.nan,
        "勝率": round(fin["win_hit"].mean() * 100, 2),
        "単勝ROI": round(win_ret / len(wo), 3) if len(wo) else np.nan,
        "平均人気": round(fin["win_popularity"].mean(), 2),
    }


def show(rows: list[tuple[str, pd.DataFrame]], title: str) -> pd.DataFrame:
    recs = []
    for label, sub in rows:
        d = stat(sub)
        d["label"] = label
        recs.append(d)
    out = pd.DataFrame(recs)
    cols = ["label", "n", "3着内率", "複勝ROI", "勝率", "単勝ROI", "平均人気"]
    out = out[[c for c in cols if c in out.columns]]
    print("\n" + "=" * 100)
    print(f"  {title}")
    print("=" * 100)
    print(out.to_string(index=False))
    return out


def build_walk_forward(args: argparse.Namespace) -> pd.DataFrame:
    """四半期ごとに vintage モデルを学習し、その四半期を予測して連結する。"""
    df = prepare(load_all(args.data_start, args.eval_end))
    df["win_popularity"] = pd.to_numeric(df["win_popularity"], errors="coerce")
    logger.info(
        f"読込: {len(df):,}行 / {df['race_id'].nunique():,}レース "
        f"({df['date'].min()}〜{df['date'].max()})"
    )

    fin = df[df["is_finisher"]].copy()
    fin["y_rank"] = normalized_rank(fin)
    fin["y_out"] = (fin["finish_position"] >= 6).astype(int)

    results = []
    for label, qstart, qend in quarters(args.eval_start, args.eval_end):
        train = fin[fin["date"] < qstart]
        if train.empty:
            continue
        span = (pd.to_datetime(qstart) - pd.to_datetime(train["date"].min())).days
        if span < args.min_train_days:
            logger.info(f"{label}: 学習期間 {span}日 < {args.min_train_days} のためスキップ")
            continue
        target = df[(df["date"] >= qstart) & (df["date"] <= qend)]
        if target.empty:
            continue
        logger.info(
            f"{label}: train {len(train):,}行 ({train['date'].min()}〜{train['date'].max()}"
            f" / {span}日) → predict {len(target):,}行 ({qstart}〜{qend})"
        )
        reg_m, out_m = fit_vintage(train, args.seed, args.valid_days)
        X = target[FEATURES].values
        res = target.copy()
        res["_reg"] = reg_m.predict(X)
        res["_out"] = np.clip(out_m.predict(X), 0.0, 1.0)
        # 本番と同じ z 合成。blend_v27 のスケーリングはレース内単調なので順位は不変。
        res["wf_score"] = _race_z(res, "_reg") * -1.0 - V27_OUT_WEIGHT * _race_z(res, "_out")
        res["quarter"] = label
        res["train_days"] = span
        results.append(res)

    if not results:
        raise SystemExit("評価対象の四半期がありません")
    return pd.concat(results, ignore_index=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-start", default="20230506", help="学習に使えるデータの最初")
    p.add_argument("--eval-start", default="20240101", help="評価開始")
    p.add_argument("--eval-end", default="20260815")
    p.add_argument("--min-train-days", type=int, default=200)
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--top-k", type=int, default=3, help="レース全体での指数上位 k 位")
    p.add_argument("--out", default=None, help="結果 JSON の書き出し先")
    p.add_argument(
        "--cache",
        default=None,
        help="walk-forward 予測の保存先 pickle。存在すれば再学習せず読み込む"
        "（集計だけ変えたいときに四半期ぶんの再学習を避ける）",
    )
    args = p.parse_args()

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        ev = pd.read_pickle(cache)
        logger.info(f"キャッシュから読込: {cache}（再学習なし）")
    else:
        ev = build_walk_forward(args)
        if cache:
            ev.to_pickle(cache)
            logger.info(f"walk-forward 予測を保存: {cache}")
    add_rank(ev, "wf_score", "wf_rank", ascending=False)
    add_rank(ev, "db_composite", "db_rank", ascending=False)
    ev = ev[ev["win_popularity"].notna()].copy()

    k = args.top_k
    print(
        f"\n評価対象: {ev['race_id'].nunique():,}レース / {len(ev):,}頭 "
        f"({ev['date'].min()}〜{ev['date'].max()})"
    )
    print(f"四半期: {', '.join(sorted(ev['quarter'].unique()))}")
    print(
        f"walk-forward と DB(v27) で指数{k}位以内の一致率: "
        f"{((ev['wf_rank'] <= k) == (ev['db_rank'] <= k)).mean() * 100:.1f}%"
    )

    # --- 市場エコー度（この検証の前提になる数字） -------------------------------
    def mean_rho(a: str, b: str) -> float:
        def _r(g: pd.DataFrame) -> float:
            if len(g) < 3:
                return np.nan
            return g[a].rank().corr(g[b].rank())

        return float(ev.groupby("race_id")[[a, b]].apply(_r).mean())

    print(
        f"\nレース内順位相関（人気順位との Spearman 平均）: "
        f"WF指数 {mean_rho('wf_rank', 'win_popularity'):.3f} / "
        f"DB v27 {mean_rho('db_rank', 'win_popularity'):.3f}"
    )

    # --- 【1】人気帯ごとの素の成績 ------------------------------------------------
    rows: list[tuple[str, pd.DataFrame]] = []
    for name, lo, hi in POP_BANDS:
        m = ev["win_popularity"].between(lo, hi)
        rows.append((f"{name} ベース", ev[m]))
    show(rows, "【1】人気帯別のベースライン（母集団＝全出走馬）")

    # --- 【2】帯の中で指数上位を取ったときの成績（WF vs in-sample） ----------------
    for name, lo, hi in POP_BANDS:
        m = ev["win_popularity"].between(lo, hi)
        if m.sum() == 0:
            continue
        ev["_br_wf"] = band_rank(ev, m, "wf_score")
        ev["_br_db"] = band_rank(ev, m, "db_composite")
        show(
            [
                (f"{name} ベース", ev[m]),
                ("　帯内 指数1位 [WF honest]", ev[m & (ev["_br_wf"] == 1)]),
                ("　帯内 指数1位 [in-sample DB v27]", ev[m & (ev["_br_db"] == 1)]),
                ("　帯内 指数2位以内 [WF]", ev[m & (ev["_br_wf"] <= 2)]),
                (f"　レース全体で指数{k}位以内 [WF]", ev[m & (ev["wf_rank"] <= k)]),
                (f"　レース全体で指数{k}位以内 [in-sample DB v27]", ev[m & (ev["db_rank"] <= k)]),
            ],
            f"【2-{name}】帯の中で指数上位を取る（honest / in-sample 比較）",
        )

    # --- 【3】人気を完全に揃えた層化リフト（主指標） --------------------------------
    fin_ev = ev[ev["is_finisher"] & (ev["place_slots"] > 0)].copy()
    fin_ev["ret_place"] = fin_ev["place_odds"].fillna(0.0).where(fin_ev["place_hit"] == 1, 0.0)
    fin_ev["ret_win"] = fin_ev["win_odds"].fillna(0.0).where(fin_ev["win_hit"] == 1, 0.0)
    strata = fin_ev["win_popularity"].astype("Int64").astype(str)

    print("\n" + "=" * 100)
    print("  【3】人気を完全に揃えた層化リフト（strata = 人気順位そのもの・レース単位CI）")
    print("      「帯内 指数1位」−「帯内 それ以外」。市場のエコー分を差し引いた正味の識別力。")
    print("=" * 100)
    lift_summary = {}
    for name, lo, hi in POP_BANDS:
        m = fin_ev["win_popularity"].between(lo, hi)
        if m.sum() == 0:
            continue
        br_wf = band_rank(fin_ev, m, "wf_score")
        br_db = band_rank(fin_ev, m, "db_composite")
        print(f"\n  [{name}]  n={int(m.sum()):,}")
        for tag, br in (("WF honest", br_wf), ("in-sample", br_db)):
            pt, lo_ci, hi_ci = strat_diff(
                fin_ev, m & (br == 1), m & (br > 1), strata, "place_hit"
            )
            base = fin_ev[m & (br > 1)]["place_hit"].mean() * 100
            sel = fin_ev[m & (br == 1)]["place_hit"].mean() * 100
            print(
                f"    {tag:10s} 3着内率 {sel:5.2f}% (帯内その他 {base:5.2f}%)  "
                f"人気を揃えた差 {pt * 100:+.2f}pt [{lo_ci * 100:+.2f}, {hi_ci * 100:+.2f}]"
            )
            if tag == "WF honest":
                lift_summary[name] = {
                    "n_selected": int((m & (br == 1)).sum()),
                    "rate_selected": round(sel, 2),
                    "rate_others": round(base, 2),
                    "strat_lift_pt": round(pt * 100, 2),
                    "ci": [round(lo_ci * 100, 2), round(hi_ci * 100, 2)],
                }
        pay = fin_ev[m & fin_ev["payout_ok"]]
        br_pay = band_rank(fin_ev, m, "wf_score").reindex(pay.index)
        for col, unit in (("ret_place", "複勝ROI"), ("ret_win", "単勝ROI")):
            src = pay[br_pay == 1] if col == "ret_place" else fin_ev[m & (br_wf == 1)]
            ptv, lov, hiv = boot_mean(src, col)
            print(f"    WF honest  帯内指数1位 {unit}: {ptv:.3f} [{lov:.3f}, {hiv:.3f}]")

    # --- 【4】市場乖離ルール ---------------------------------------------------------
    ev["divergence"] = ev["win_popularity"] - ev["wf_rank"]  # 正 = 指数が市場より高評価
    dark = ev["win_popularity"] >= 5
    show(
        [("5番人気以下 ベース", ev[dark])]
        + [
            (f"　乖離 >= {m2}（指数順位が人気より{m2}以上上）", ev[dark & (ev["divergence"] >= m2)])
            for m2 in (2, 4, 6, 8)
        ],
        "【4】市場乖離ルール（人気順位 − 指数順位）[すべて WF honest]",
    )

    # --- 【5】四半期別の安定性 -------------------------------------------------------
    m58 = ev["win_popularity"].between(5, 8)
    ev["_br58"] = band_rank(ev, m58, "wf_score")
    show(
        [
            (f"{q} 5-8番人気 帯内指数1位", ev[m58 & (ev["_br58"] == 1) & (ev["quarter"] == q)])
            for q in sorted(ev["quarter"].unique())
        ],
        "【5】四半期別の安定性（5-8番人気・帯内指数1位）[WF honest]",
    )

    # --- 【6】唯一の妙味候補セルに CI を付ける ----------------------------------------
    # 「人気薄 ∧ レース全体で指数上位」は帯内順位と違って n が小さい。
    # 8/15 の穴ぐさ検証の教訓（n が小さい部分集合ほど脆い）を踏まえ、
    # 点推定だけで判断せず必ず区間と四半期分解を見る。
    print("\n" + "=" * 100)
    print("  【6】人気薄 × レース全体で指数上位（妙味候補セル）[WF honest]")
    print("=" * 100)
    cell_summary = {}
    for name, lo, hi in [("5-8番人気", 5, 8), ("9-12番人気", 9, 12), ("9番人気以下", 9, 99)]:
        for kk in (2, 3):
            m = fin_ev["win_popularity"].between(lo, hi) & (fin_ev["wf_rank"] <= kk)
            src = fin_ev[m]
            if len(src) < 30:
                print(f"\n  [{name} × 指数{kk}位以内]  n={len(src)} … 少なすぎるため省略")
                continue
            print(f"\n  [{name} × 指数{kk}位以内]  n={len(src):,}")
            for col, unit, sc in (
                ("place_hit", "3着内率", 100),
                ("ret_place", "複勝ROI", 1),
                ("ret_win", "単勝ROI", 1),
            ):
                s2 = src[src["payout_ok"]] if col == "ret_place" else src
                pt, lo_ci, hi_ci = boot_mean(s2, col)
                suf = "%" if sc == 100 else ""
                print(
                    f"    {unit}: {pt * sc:.3f}{suf} "
                    f"[{lo_ci * sc:.3f}, {hi_ci * sc:.3f}]  (n={len(s2):,})"
                )
                if kk == 3:
                    cell_summary.setdefault(name, {})[unit] = [
                        round(pt * sc, 3),
                        round(lo_ci * sc, 3),
                        round(hi_ci * sc, 3),
                        int(len(s2)),
                    ]
            byq = src.groupby("quarter").agg(
                n=("place_hit", "size"), 複勝ROI=("ret_place", "mean")
            )
            print("    四半期別: " + " / ".join(
                f"{q} {int(r['n'])}頭 {r['複勝ROI']:.2f}" for q, r in byq.iterrows()
            ))

    if args.out:
        payload = {
            "eval_period": [ev["date"].min(), ev["date"].max()],
            "quarters": sorted(ev["quarter"].unique()),
            "n_races": int(ev["race_id"].nunique()),
            "echo_spearman_wf": round(mean_rho("wf_rank", "win_popularity"), 4),
            "echo_spearman_db": round(mean_rho("db_rank", "win_popularity"), 4),
            "stratified_lift": lift_summary,
            "value_cells_top3": cell_summary,
        }
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
