#!/usr/bin/env python3
"""【イレギュラー検出層 Step A】4つの操作的定義を市場含意と横並びで比較する（2026-08-20）。

## この検証が答える問い

[[keirin_next_phase_handoff_2026_08_20]] のユーザー方針:

    ベース指数は「実力を測る」ものと割り切る（高的中率・低ROI）。その上に
    **イレギュラーが発生しやすい条件**を算出し、そのケースで穴を狙う。

Step A は「イレギュラーの**操作的定義**を決める」。ここで直感で1つ選ぶと、
[[keirin_verification_audit_2026_08_20]] が指摘した「棄却を根拠にする前に設計を疑う」
型の誤りを繰り返すため、**4定義を同一ハーネスで横並びに測ってから**決める。

判定の軸は「どれが穴らしいか」ではない。引き継ぎメモの前提2

    モデル残差は『市場に追いつく余地』であって『市場を出し抜く余地』ではない

に従い、**市場含意確率より上手く当てられる定義があるか**だけを見る。

## 4つの定義

| ID | 定義 | 市場含意 | 備考 |
|---|---|---|---|
| D1 | 本命バスト: 軸1==WT◎ の本命が4着以下 | 1 - marg(fav) | 既存 RANK_7H1 と同一定義 |
| D2 | 市場波乱: 市場の最有力車が4着以下 | 1 - marg(mktfav) | 定義が市場基準 |
| D2b | 高配当: 的中三連複が 30倍以上 | Σ p(30倍以上の目) | 配当そのもの |
| D4 | ライン崩壊: top3 に同一ライン2車が居ない | Σ p(該当する目) | 完全未検証 |

D3（展開イレギュラー = B取りが想定と違う）は **top3 の集合で表現できない**ため
市場含意が作れない。これは欠点ではなく「市場が構造的に織り込めない」ことの現れなので、
**ラベルではなくゲート**として扱い、D2b（高配当）の ratio を予測十分位で層別する。

## 測る指標（[[keirin_pair_correlation_mispricing_2026_07_30]] の枠組み）

    market(R) = Σ_{条件を満たす三連複の目} 正規化(0.75 / odds)   ← 市場の含意確率
    ratio     = 実測発生率 ÷ market                              ← >1 なら市場が過小評価
    ROI       = 0.75 × ratio                                     ← 100%超には ratio >= 1.333

全体 ratio が 1.000 付近なのは既定（市場は正しい）。見るのは
**「我々の予測 p と市場含意 m の差」で層別したときに最上位バケットの ratio が上がるか**。
上がらなければ、その定義に未織込みの残差は無い。

## 🔴 事前登録した採用ライン（事後に動かさない）

    スクリーニング通過 : 最上位バケット(残差 top10%) で ratio >= 1.05 かつ t > 3
    運用可能（単体賭け）: 同バケットで ratio >= 1.333（ROI 100%超）

4定義すべてについて **n / 実測 / 市場 / ratio / t** を全バケット記録する
（ratio だけ残すと判定根拠が追えない、という監査メモの指摘に対応）。

## 窓

    TRAIN  2024-01-01 〜 2025-12-31   モデル学習のみ
    SWEEP  2026-01-01 〜 2026-08-20   本スクリーニングの報告窓

🔴 **確認窓は未開封のまま残す。** ここを通過した定義だけを Step B で
四半期 vintage の walk-forward に載せ替え、2025年を一度きりの確認窓として検定する
（[[keirin_b_line_model_2026_08_16]] が確認窓で不採用になった手続きと同じ）。

DB は **読み取り専用 SELECT のみ**。書き込みは一切しない。

使い方:
    cd keirin && PYTHONPATH=. .venv/bin/python scripts/exp_irregular_screen.py
    #  --force-cache で中間キャッシュを作り直す
"""
from __future__ import annotations

import argparse
import math
import pickle
import re
import sys
from collections import defaultdict
from pathlib import Path

import lightgbm as lgb
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.preprocessing.favbust_features import (  # noqa: E402
    RACE_FEATURE_COLS, race_features,
)

DATE_FROM = "2024-01-01"
TRAIN_TO = "2025-12-31"
DATE_TO = "2026-08-20"
N_CAR = 7
TAKEOUT_RETURN = 0.75
MIN_BOARD = 33          # 7車の三連複は 35 点。欠けが多い板は使わない
HIGHPAY_ODDS = 30.0
CACHE = REPO / "data" / "exp_cache" / "irregular_screen.pkl"

PARAMS = {
    "objective": "binary", "learning_rate": 0.05, "num_leaves": 31,
    "min_data_in_leaf": 80, "feature_fraction": 0.8, "bagging_fraction": 0.8,
    "bagging_freq": 1, "verbose": -1, "seed": 42,
}
N_ROUNDS = 300

#: 事前登録した採用ライン（🔴 事後に動かさない）
BAR_SCREEN_RATIO = 1.05
BAR_SCREEN_T = 3.0
BAR_OPERABLE_RATIO = 1.0 / TAKEOUT_RETURN     # 1.333…


# --------------------------------------------------------------------------
# 読み込み
# --------------------------------------------------------------------------
def load_context() -> tuple[dict, dict]:
    """(race_key -> meta, race_key -> entries) を返す（7車・非中止のみ）。"""
    with get_connection() as c:
        meta = {}
        for r in c.execute(
                "SELECT r.race_key, r.race_date, r.grade, r.race_type, r.day_index, "
                "       r.start_at, r.distance, v.bank_length, v.is_indoor "
                "FROM wt_races r LEFT JOIN venue_info v ON r.venue_id = v.venue_code "
                "WHERE r.n_entries = ? AND r.cancel = 0 "
                "  AND r.race_date BETWEEN ? AND ?", (N_CAR, DATE_FROM, DATE_TO)):
            meta[r["race_key"]] = dict(r)
        print(f"[load] 7車レース {len(meta):,}件", flush=True)

        keys = sorted(meta)
        ents: dict[str, list[dict]] = defaultdict(list)
        for i in range(0, len(keys), 700):
            ch = keys[i:i + 700]
            q = ("SELECT race_key, frame_no, player_id, pred_win_pct, pred_top3_pct, "
                 "       prediction_mark, race_point, line_group, line_size, line_pos, "
                 "       is_line_leader, n_lines, finish_order, style, prefecture, "
                 "       player_class, res_back FROM wt_entries WHERE race_key IN (%s)"
                 % ",".join("?" * len(ch)))
            for r in c.execute(q, ch):
                ents[r["race_key"]].append(dict(r))
        print(f"[load] 出走 {sum(len(v) for v in ents.values()):,}件", flush=True)
    return meta, dict(ents)


def load_trio_odds(race_keys: list[str]) -> dict[str, dict[int, float]]:
    """race_key -> {top3集合のビットマスク: 最終オッズ}。"""
    out: dict[str, dict[int, float]] = {}
    with get_connection() as c:
        for i in range(0, len(race_keys), 600):
            ch = race_keys[i:i + 600]
            q = ("SELECT race_key, combination, odds_value FROM wt_odds "
                 "WHERE bet_type = 'trio' AND race_key IN (%s)" % ",".join("?" * len(ch)))
            for rk, comb, od in c.execute(q, ch):
                try:
                    fv = float(od) if od is not None else None
                except (TypeError, ValueError):
                    continue
                if fv is None or fv <= 0:
                    continue
                try:
                    parts = [int(x) for x in re.split(r"[-=→]", str(comb))]
                except ValueError:
                    continue
                if len(set(parts)) != 3:
                    continue
                m = 0
                for p in parts:
                    m |= 1 << p
                out.setdefault(rk, {})[m] = fv
    return out


def b_rate_prior(meta: dict, ents: dict) -> dict[tuple[str, int], float]:
    """point-in-time な B取得率（直近90日）。D3 のゲート用。

    `feature_wt.b_rate_90` と同じ趣旨だが、本スクリプト内で自己完結させるため
    独自に集計する（重い特徴量パイプライン全体を回さないため）。
    """
    # player_id -> [(race_date, b_flag)]
    hist: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for rk, es in ents.items():
        d = meta[rk]["race_date"]
        for e in es:
            if e.get("player_id") is None:
                continue
            hist[int(e["player_id"])].append((str(d), 1 if e.get("res_back") else 0))
    for lst in hist.values():
        lst.sort()

    out: dict[tuple[str, int], float] = {}
    for rk, es in ents.items():
        d = str(meta[rk]["race_date"])
        for e in es:
            pid = e.get("player_id")
            if pid is None:
                continue
            lst = hist.get(int(pid), [])
            # strictly 過去のみ（当日を含めない = look-ahead を作らない）
            past = [b for dd, b in lst if dd < d]
            past = past[-40:]
            out[(rk, int(e["frame_no"]))] = (sum(past) / len(past)) if past else -1.0
    return out


# --------------------------------------------------------------------------
# 定義とラベル
# --------------------------------------------------------------------------
def build_rows(force: bool = False) -> list[dict]:
    if CACHE.exists() and not force:
        with CACHE.open("rb") as f:
            rows = pickle.load(f)
        print(f"[cache] {len(rows):,}件を再利用", flush=True)
        return rows

    meta, ents = load_context()
    print("[prep] B取得率(point-in-time)を集計 ...", flush=True)
    brate = b_rate_prior(meta, ents)

    by_month: dict[str, list[str]] = defaultdict(list)
    for rk, m in meta.items():
        by_month[str(m["race_date"])[:7]].append(rk)

    rows: list[dict] = []
    for ym in sorted(by_month):
        rks = by_month[ym]
        boards = load_trio_odds(rks)
        n_add = 0
        for rk in rks:
            es = ents.get(rk)
            board = boards.get(rk)
            if not es or len(es) != N_CAR or not board or len(board) < MIN_BOARD:
                continue
            if any(e.get("pred_top3_pct") is None or e.get("pred_win_pct") is None
                   for e in es):
                continue
            fin = [(int(e["finish_order"]), int(e["frame_no"])) for e in es
                   if e.get("finish_order") is not None and int(e["finish_order"]) >= 1]
            if len(fin) < 3:
                continue
            fin.sort()
            tm = 0
            for _, f in fin[:3]:
                tm |= 1 << f
            if tm not in board:
                continue

            feats = race_features(meta[rk], es)
            if feats is None:
                continue

            by_frame = {int(e["frame_no"]): e for e in es}
            frames = sorted(by_frame)

            # ---- 市場の正規化分布（三連複の板） ----
            raw = {m: TAKEOUT_RETURN / o for m, o in board.items() if o > 0}
            tot = sum(raw.values())
            if tot <= 0:
                continue
            mkt = {m: v / tot for m, v in raw.items()}
            marg = {f: 0.0 for f in frames}
            for m, p in mkt.items():
                for f in frames:
                    if (m >> f) & 1:
                        marg[f] += p

            # ---- 軸1 / WT◎ / 市場1番人気 ----
            w1 = max(frames, key=lambda f: float(by_frame[f].get("pred_win_pct") or 0))
            honmei = next((int(e["frame_no"]) for e in es
                           if e.get("prediction_mark") == 1), None)
            # 「1番人気」ではなく **三連複板のマージナル最大車**。
            # 単勝人気ではなく「市場が最も3着内に来ると見ている車」であり、
            # 市場含意 1-marg(f) と同じ板から導くので定義が閉じる。
            mkt_fav = max(frames, key=lambda f: marg[f])

            # ---- ライン所属（単騎は一意化） ----
            lg = {}
            for f in frames:
                g = by_frame[f].get("line_group")
                lg[f] = g if g is not None else f"__solo_{f}"

            def line_broken(mask: int) -> bool:
                """top3 に同一ライン2車が居なければライン崩壊。"""
                gs = [lg[f] for f in frames if (mask >> f) & 1]
                return len(set(gs)) == len(gs)

            # ---- D1 本命バスト（母集団: 軸1 == ◎） ----
            d1_y = d1_m = None
            if honmei is not None and honmei == w1:
                d1_y = 0.0 if (tm >> honmei) & 1 else 1.0
                d1_m = 1.0 - marg[honmei]

            # ---- D2 市場波乱 ----
            d2_y = 0.0 if (tm >> mkt_fav) & 1 else 1.0
            d2_m = 1.0 - marg[mkt_fav]

            # ---- D2b 高配当 ----
            d2b_y = 1.0 if board[tm] >= HIGHPAY_ODDS else 0.0
            d2b_m = sum(p for m, p in mkt.items()
                        if board.get(m, 0.0) >= HIGHPAY_ODDS)

            # ---- D4 ライン崩壊 ----
            d4_y = 1.0 if line_broken(tm) else 0.0
            d4_m = sum(p for m, p in mkt.items() if line_broken(m))

            # ---- D3 展開イレギュラー（ゲート用・市場含意なし） ----
            actual_b = next((int(e["frame_no"]) for e in es if e.get("res_back")), None)
            cand = [(brate.get((rk, f), -1.0), f) for f in frames]
            known = [(b, f) for b, f in cand if b >= 0]
            exp_b = max(known)[1] if known else None
            d3_y = (None if (actual_b is None or exp_b is None)
                    else (1.0 if actual_b != exp_b else 0.0))

            rows.append({
                **feats,
                "race_key": rk, "race_date": str(meta[rk]["race_date"]),
                "win_odds": board[tm],
                "d1_y": d1_y, "d1_m": d1_m,
                "d2_y": d2_y, "d2_m": d2_m,
                "d2b_y": d2b_y, "d2b_m": d2b_m,
                "d4_y": d4_y, "d4_m": d4_m,
                "d3_y": d3_y,
            })
            n_add += 1
        print(f"  {ym}: {len(rks):>5}R → {n_add:>5}件 (累計 {len(rows):,})", flush=True)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".pkl.tmp")
    with tmp.open("wb") as f:
        pickle.dump(rows, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(CACHE)
    return rows


# --------------------------------------------------------------------------
# 集計
# --------------------------------------------------------------------------
def ratio_stats(y: np.ndarray, m: np.ndarray) -> dict:
    """実測 vs 市場含意の ratio と、差の平均に対する t 値。"""
    n = len(y)
    if n == 0:
        return {"n": 0, "act": 0.0, "mkt": 0.0, "ratio": 0.0, "t": 0.0, "roi": 0.0}
    act, mk = float(y.mean()), float(m.mean())
    d = y - m
    sd = float(d.std(ddof=1)) if n > 1 else 0.0
    t = float(d.mean() / (sd / math.sqrt(n))) if sd > 0 else 0.0
    ratio = act / mk if mk > 0 else 0.0
    return {"n": n, "act": act * 100, "mkt": mk * 100, "ratio": ratio, "t": t,
            "roi": TAKEOUT_RETURN * ratio * 100}


def fit_predict(rows: list[dict], ycol: str) -> tuple[np.ndarray, list[dict]]:
    """TRAIN で学習し SWEEP を予測する。(予測, 対象行) を返す。"""
    use = [r for r in rows if r[ycol] is not None]
    tr = [r for r in use if r["race_date"] <= TRAIN_TO]
    te = [r for r in use if r["race_date"] > TRAIN_TO]
    if len(tr) < 3000 or len(te) < 500:
        return np.array([]), []
    Xtr = np.array([[float(r[c]) for c in RACE_FEATURE_COLS] for r in tr], dtype=float)
    ytr = np.array([r[ycol] for r in tr], dtype=float)
    ds = lgb.Dataset(Xtr, label=ytr, feature_name=list(RACE_FEATURE_COLS))
    model = lgb.train(PARAMS, ds, num_boost_round=N_ROUNDS)
    Xte = np.array([[float(r[c]) for c in RACE_FEATURE_COLS] for r in te], dtype=float)
    return model.predict(Xte), te


def auc(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(p)
    ys = y[order]
    n1 = float(ys.sum())
    n0 = float(len(ys) - n1)
    if n1 == 0 or n0 == 0:
        return 0.5
    ranks = np.empty(len(ys), dtype=float)
    ranks[order] = np.arange(1, len(ys) + 1, dtype=float)
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def report_definition(tag: str, title: str, rows: list[dict],
                      ycol: str, mcol: str) -> dict | None:
    print("\n" + "=" * 100)
    print(f"{tag}  {title}")
    print("=" * 100)

    p, te = fit_predict(rows, ycol)
    if not te:
        print("  母数不足のためスキップ")
        return None
    y = np.array([r[ycol] for r in te], dtype=float)
    m = np.array([r[mcol] for r in te], dtype=float)

    allst = ratio_stats(y, m)
    print(f"  SWEEP 全体   n={allst['n']:>6}  実測 {allst['act']:6.2f}%  "
          f"市場 {allst['mkt']:6.2f}%  ratio {allst['ratio']:.3f}  t={allst['t']:+.2f}")
    print(f"  予測 AUC     {auc(y, p):.4f}   （市場含意 AUC {auc(y, m):.4f}）")

    resid = p - m
    order = np.argsort(-resid)
    n = len(order)
    print("\n  残差(我々の予測 − 市場含意) 十分位   ※上ほど「市場より高く見積もった」")
    print(f"  {'帯':<10}{'n':>7}{'実測%':>9}{'市場%':>9}{'ratio':>9}{'t':>8}{'ROI%':>9}")
    print("  " + "-" * 60)
    buckets = []
    for k in range(10):
        idx = order[n * k // 10: n * (k + 1) // 10]
        st = ratio_stats(y[idx], m[idx])
        buckets.append(st)
        flag = ""
        if st["ratio"] >= BAR_OPERABLE_RATIO and st["t"] > BAR_SCREEN_T:
            flag = "  ★運用可"
        elif st["ratio"] >= BAR_SCREEN_RATIO and st["t"] > BAR_SCREEN_T:
            flag = "  ○通過"
        print(f"  D{k + 1:<9}{st['n']:>7}{st['act']:>9.2f}{st['mkt']:>9.2f}"
              f"{st['ratio']:>9.3f}{st['t']:>+8.2f}{st['roi']:>9.1f}{flag}")

    top = buckets[0]
    passed = top["ratio"] >= BAR_SCREEN_RATIO and top["t"] > BAR_SCREEN_T
    print(f"\n  → 最上位バケット ratio={top['ratio']:.3f} t={top['t']:+.2f} : "
          f"{'○ スクリーニング通過' if passed else '✗ 未織込み残差なし'}")
    return {"tag": tag, "title": title, "all": allst, "top": top, "passed": passed}


def report_gate_d3(rows: list[dict]) -> None:
    """D3（展開イレギュラー）はゲートとして扱う。"""
    print("\n" + "=" * 100)
    print("D3  展開イレギュラー（B取りが想定と違う）— ゲート用途")
    print("=" * 100)
    print("  ※ top3 の集合で表現できないため市場含意が作れない。")
    print("     ＝ 市場が構造的に織り込めない量。予測できるか／高配当と結びつくかを見る。")

    p, te = fit_predict(rows, "d3_y")
    if not te:
        print("  母数不足のためスキップ")
        return
    y = np.array([r["d3_y"] for r in te], dtype=float)
    print(f"\n  SWEEP n={len(te):,}  発生率 {y.mean() * 100:.2f}%  予測 AUC {auc(y, p):.4f}")

    # ゲートとして: P(展開イレギュラー) の十分位で D2b(高配当) の ratio を見る
    yb = np.array([r["d2b_y"] for r in te], dtype=float)
    mb = np.array([r["d2b_m"] for r in te], dtype=float)
    order = np.argsort(-p)
    n = len(order)
    print(f"\n  P(展開イレギュラー) 十分位 × D2b(三連複{HIGHPAY_ODDS:.0f}倍以上) の市場比")
    print(f"  {'帯':<10}{'n':>7}{'実測%':>9}{'市場%':>9}{'ratio':>9}{'t':>8}{'ROI%':>9}")
    print("  " + "-" * 60)
    for k in range(10):
        idx = order[n * k // 10: n * (k + 1) // 10]
        st = ratio_stats(yb[idx], mb[idx])
        flag = ""
        if st["ratio"] >= BAR_OPERABLE_RATIO and st["t"] > BAR_SCREEN_T:
            flag = "  ★運用可"
        elif st["ratio"] >= BAR_SCREEN_RATIO and st["t"] > BAR_SCREEN_T:
            flag = "  ○通過"
        print(f"  D{k + 1:<9}{st['n']:>7}{st['act']:>9.2f}{st['mkt']:>9.2f}"
              f"{st['ratio']:>9.3f}{st['t']:>+8.2f}{st['roi']:>9.1f}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-cache", action="store_true")
    args = ap.parse_args()

    rows = build_rows(force=args.force_cache)
    tr = sum(1 for r in rows if r["race_date"] <= TRAIN_TO)
    print(f"\n[data] 全 {len(rows):,}件  TRAIN {tr:,} / SWEEP {len(rows) - tr:,}")

    print("\n" + "#" * 100)
    print("# イレギュラー検出層 Step A — 4定義の横並びスクリーニング")
    print(f"# 事前登録: 通過 ratio>={BAR_SCREEN_RATIO} かつ t>{BAR_SCREEN_T} / "
          f"運用可 ratio>={BAR_OPERABLE_RATIO:.3f}")
    print(f"# TRAIN {DATE_FROM}〜{TRAIN_TO} / SWEEP {TRAIN_TO}〜{DATE_TO}")
    print("# 確認窓(2025年 walk-forward)は未開封のまま残す")
    print("#" * 100)

    results = []
    for tag, title, yc, mc in [
        ("D1 ", "本命バスト（軸1==WT◎ の本命が4着以下）", "d1_y", "d1_m"),
        ("D2 ", "市場波乱（三連複板の最有力車が4着以下）", "d2_y", "d2_m"),
        ("D2b", f"高配当（的中三連複 {HIGHPAY_ODDS:.0f}倍以上）", "d2b_y", "d2b_m"),
        ("D4 ", "ライン崩壊（top3 に同一ライン2車が居ない）", "d4_y", "d4_m"),
    ]:
        r = report_definition(tag, title, rows, yc, mc)
        if r:
            results.append(r)

    report_gate_d3(rows)

    print("\n" + "#" * 100)
    print("# まとめ")
    print("#" * 100)
    print(f"  {'定義':<40}{'全体ratio':>10}{'最上位ratio':>12}{'t':>8}  判定")
    for r in results:
        print(f"  {r['tag'] + ' ' + r['title']:<40}{r['all']['ratio']:>10.3f}"
              f"{r['top']['ratio']:>12.3f}{r['top']['t']:>+8.2f}  "
              f"{'○ 通過' if r['passed'] else '✗'}")
    if not any(r["passed"] for r in results):
        print("\n  → 全定義で未織込み残差なし。イレギュラー検出層は"
              "『定義の選び方』の問題ではなく、市場効率の壁として非成立。")


if __name__ == "__main__":
    main()
