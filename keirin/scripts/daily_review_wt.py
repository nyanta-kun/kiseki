#!/usr/bin/env python3
"""日次のレース単位レビュー（2026-08-17 新設・ユーザー要望）。

## 何をするか

その日に**買った**レースを1本ずつ見直し、次の3つを機械的に決めて台帳へ積む。

1. **外れの型**（固定の分類）… 何が起きて外れたのか
2. **最小の反実仮想**（朝の情報だけで組める範囲）… 何を変えれば当たったのか
3. **事前の帯**（買う前に分かる指標だけ）… 買ってよい帯だったのか

## 🔴 この道具の最大の危険は「後知恵の積み上げ」である

どのレースにも「こう買えば当たった」は必ず存在する。それを毎日拾って
その都度ルールを足すと、**過去にだけ最適化された規則の山**ができあがる。
本スクリプトはそれを防ぐために、次の3点を構造として持つ:

- **反実仮想は "型" に丸めて記録する**。個別レースの買い目案は出さない。
  出すのは `LEG_MISS → 相手を1枚広げれば的中` のような**分類**だけ。
- **採否の判断はここでしない**。台帳に型ごとの件数を積み、
  `--alert-min` 件を超えた型だけを「全期間で検証する候補」として報告する。
  型が溜まっていないうちは、何件外れていても提案しない。
- **「買うべきだったか」は事前指標だけで決める**。着順・配当は一切見ない。
  見るのは p3合計・印一致・相手点数など、朝の時点で確定している量のみ。

⚠️ **過去日に対して遡って実行しないこと。** 事前指標は
`wt_entries.pred_top3_pct`（＝その日の本番モデルの出力）から読む。当日〜翌朝に
実行する限りこれは「実際に推奨を作ったモデル」だが、モデルを再学習した後に
過去日へ遡ると別のモデルの目で過去を裁くことになり、台帳が汚れる。

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/daily_review_wt.py [YYYY-MM-DD]
        --no-append     台帳へ書かない（表示だけ）
        --alert-min N   型がN件以上たまったら検証候補として報告（既定20）

台帳: `data/analysis/daily_review_ledger.csv`（追記のみ・1レース1行）
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.combo_label import parse_pred_combo  # noqa: E402
from src.database import get_connection  # noqa: E402

LEDGER = ROOT / "data" / "analysis" / "daily_review_ledger.csv"

# 外れの型（固定・増やすときはここだけ）。**個別レース固有の理由を足さないこと**。
MISS_KINDS = {
    "HIT": "的中",
    "ORDER_MISS": "3車は合ったが着順違い（三連単）",
    "LEG_MISS": "軸2車は3着内・3着目が買い目外",
    "AXIS2_OUT": "軸2だけ3着内を外した",
    "AXIS1_OUT": "軸1が3着内を外した",
    "BOTH_AXIS_OUT": "軸2車とも3着内を外した",
    "NO_RESULT": "結果未確定",
}

# 🔴 **反実仮想には必ず採算を添える。** 「総流しなら当たった」は毎日必ず何件か出るが、
#    広げた点数で割ると投資割れ（ガミ）になっていることが多い。採算を書かずに件数だけ
#    積むと「相手を広げよう」という誤った方向へ必ず引っ張られる。
#: 反実仮想の型（朝の情報だけで組める変更に限る）。
FIX_KINDS = {
    "-": "—",
    "TRIO_INSTEAD": "同じ3車を三連複で買っていれば的中",
    "LEGS_WIDER": "相手を{n}番手まで広げれば的中",
    "LEGS_ALL": "相手を総流しにすれば的中",
    "AXIS2_SWAP": "軸2を指数{n}番手に替えれば的中",
    "UNREACHABLE": "朝の指数上位から到達不能（軸1が飛んだ等）",
}


def _load(target_date: str) -> tuple[list[dict], dict, dict, dict]:
    with get_connection() as c:
        picks = [dict(zip(
            ("race_key", "rank", "pred_combo", "n_combos", "hit", "payout",
             "bet_amount", "trio_payout", "trifecta_payout"), tuple(r)))
            for r in c.execute(
                "SELECT race_key, rank, pred_combo, n_combos, hit, payout, "
                "bet_amount, trio_payout, trifecta_payout FROM picks_history "
                "WHERE race_date=? AND bet_amount>0 ORDER BY race_key",
                (target_date,))]
        fins: dict = defaultdict(dict)
        p3: dict = defaultdict(dict)
        marks: dict = defaultdict(dict)
        for rk, fno, fo, pt, pm in c.execute(
                "SELECT e.race_key, e.frame_no, e.finish_order, e.pred_top3_pct, "
                "e.prediction_mark FROM wt_entries e JOIN wt_races r "
                "ON r.race_key=e.race_key WHERE r.race_date=?", (target_date,)):
            if fo is not None and 1 <= int(fo) <= 3:
                fins[rk][int(fo)] = int(fno)
            if pt is not None:
                p3[rk][int(fno)] = float(pt) / 100.0
            if pm is not None:
                marks[rk][int(pm)] = int(fno)
    return picks, fins, p3, marks


def _axes_and_legs(combos: list[tuple[int, ...]], kind: str):
    """買い目から (軸2車, 相手の集合) を復元する。

    三連複は全点に共通する2車が軸。三連単は1着・2着の固定車が軸
    （固定されていなければ軸なしとして None を返す）。
    """
    if not combos:
        return None, set()
    if kind == "trio":
        common = set(combos[0])
        for c in combos[1:]:
            common &= set(c)
        if len(common) != 2:
            return None, set()
        axes = tuple(sorted(common))
        legs = {x for c in combos for x in c if x not in common}
        return axes, legs
    firsts = {c[0] for c in combos}
    seconds = {c[1] for c in combos}
    if len(firsts) != 1 or len(seconds) != 1:
        return None, {c[2] for c in combos}
    axes = (next(iter(firsts)), next(iter(seconds)))
    return axes, {c[2] for c in combos}


def classify(pick: dict, order: list[int], p3: dict[int, float]):
    """(外れの型, 反実仮想の型, 補足) を返す。"""
    if len(order) < 3:
        return "NO_RESULT", "-", ""
    top3 = set(order[:3])
    parsed = parse_pred_combo(pick["pred_combo"])
    if not parsed:
        return "NO_RESULT", "-", "買い目を解釈できない"
    kind, combos = parsed[0]
    if pick["hit"]:
        return "HIT", "-", ""

    axes, legs = _axes_and_legs(combos, kind)
    # 三連単で3車の集合は合っていた場合（着順違い）
    if kind == "trifecta" and any(set(c) == top3 for c in combos):
        # 三連複へ替えると同じ相手数で買える（着順の重複が畳まれるため点数は
        # 相手数と同じ）。三連単フォーメーションは相手数＝点数なのでそのまま。
        return "ORDER_MISS", "TRIO_INSTEAD", f"k={len({c[2] for c in combos})}"
    if axes is None:
        return "LEG_MISS", "-", "軸を復元できない買い目"

    a1, a2 = axes
    in1, in2 = a1 in top3, a2 in top3
    ranked = sorted(p3, key=lambda f: (-p3.get(f, 0.0), f)) if p3 else []
    pos = {f: i + 1 for i, f in enumerate(ranked)}       # 指数の全体順位（1始まり）

    if not in1 and not in2:
        return "BOTH_AXIS_OUT", "UNREACHABLE", ""
    if not in1:
        return "AXIS1_OUT", "UNREACHABLE", ""
    if not in2:
        # 軸2を替えれば届いたか（実際の3着内のうち軸1以外で、指数最上位のもの）
        cand = [f for f in top3 if f != a1]
        best = min((pos.get(f, 99) for f in cand), default=99)
        return ("AXIS2_OUT",
                "AXIS2_SWAP" if best <= 7 else "UNREACHABLE",
                f"n={best};k={pick['n_combos']}")
    # 軸2車は来た＝3着目が買い目外
    third = next(iter(top3 - set(axes)))
    n = pos.get(third, 99)
    if not legs:
        return "LEG_MISS", "LEGS_ALL", "k=5"
    widest = max((pos.get(x, 0) for x in legs), default=0)
    # 反実仮想の点数 = 3着目(指数n番手)まで相手を広げたときの相手数。
    # 軸2車を除くので、指数n番手まで広げる＝相手 n-2 点。
    k = max(len(legs), n - 2)
    return ("LEG_MISS",
            "LEGS_WIDER" if n > widest else "LEGS_ALL",
            f"n={n};k={k}")


def _cf_return(fix: str, note: str, missed_payout: int) -> float | None:
    """反実仮想を実行していた場合の回収倍率（予算枠 ÷ 点数 で買う前提）。

    None は「採算を計算できない／反実仮想なし」。
    """
    if fix in ("-", "UNREACHABLE") or not missed_payout:
        return None
    k = None
    for part in (note or "").split(";"):
        if part.startswith("k="):
            k = int(part[2:])
    if fix == "TRIO_INSTEAD":
        k = k or 1
    if not k:
        return None
    return round(missed_payout / 100 / k, 2)


def prerace_band(pick: dict, p3: dict[int, float], marks: dict[int, int]) -> dict:
    """事前に分かる帯だけを返す（着順・配当は一切見ない）。"""
    if not p3:
        return {"p3_sum": None, "mark": "?", "band": "?"}
    ranked = sorted(p3, key=lambda f: (-p3[f], f))
    s = p3[ranked[0]] + p3[ranked[1]]
    agree = ({ranked[0], ranked[1]} == {marks.get(1), marks.get(2)}) if marks else None
    band = ("混戦(<1.44)" if s < 1.44 else
            "標準(1.44-1.65)" if s < 1.65 else "集中(>=1.65)")
    return {"p3_sum": round(s, 3), "band": band,
            "mark": ("印一致" if agree else "印不一致") if agree is not None else "?"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("target_date", nargs="?",
                    default=date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--no-append", action="store_true")
    ap.add_argument("--alert-min", type=int, default=20)
    args = ap.parse_args()
    d = args.target_date

    picks, fins, p3s, marks = _load(d)
    if not picks:
        print(f"[daily-review] {d}: 購入行なし")
        return

    rows = []
    for p in picks:
        rk = p["race_key"].split("#")[0]
        order = [fins.get(rk, {}).get(i) for i in (1, 2, 3)]
        order = [x for x in order if x]
        miss, fix, note = classify(p, order, p3s.get(rk, {}))
        band = prerace_band(p, p3s.get(rk, {}), marks.get(rk, {}))
        mult = (p["payout"] / p["bet_amount"]) if p["bet_amount"] else 0
        # 逃した配当（そのレースで実際に付いた三連複／三連単の配当）
        missed = p["trio_payout"] if p["trio_payout"] else p["trifecta_payout"]
        rows.append({
            "race_date": d, "race_key": rk,
            "rank": p["rank"].replace("RANK_", ""),
            "n_combos": p["n_combos"], "bet": p["bet_amount"],
            "payout": p["payout"], "mult": round(mult, 2),
            "miss_kind": miss, "fix_kind": fix, "note": note,
            "p3_sum": band["p3_sum"], "band": band["band"], "mark": band["mark"],
            "order": "-".join(map(str, order)),
            "missed_odds": round(missed / 100, 1) if missed else 0,
            # 反実仮想を実行していたときの1レース回収倍率（予算枠なので 配当/点数）。
            # 🔴 1.0 未満＝**当てても投資割れ**。件数だけ見て買い目を広げないための歯止め。
            "cf_return": _cf_return(fix, note, missed),
        })

    # ── 表示: ランク別サマリー ──────────────────────────────────────────
    print(f"\n===== {d} 日次レビュー（購入 {len(rows)}件）=====")
    byrank = defaultdict(list)
    for r in rows:
        byrank[r["rank"]].append(r)
    print(f"{'ランク':6s} {'件数':>4s} {'的中':>4s} {'投資':>9s} {'払戻':>9s} {'回収':>7s}")
    for rk in sorted(byrank, key=lambda k: -len(byrank[k])):
        g = byrank[rk]
        b = sum(x["bet"] for x in g)
        pa = sum(x["payout"] for x in g)
        h = sum(1 for x in g if x["miss_kind"] == "HIT")
        print(f"{rk:6s} {len(g):4d} {h:4d} {b:9,} {pa:9,} "
              f"{100*pa/b if b else 0:6.1f}%")

    # ── 表示: レース単位の明細 ────────────────────────────────────────
    print(f"\n----- レース単位 -----")
    print(f"{'レース':16s} {'ランク':5s} {'点':>2s} {'着順':8s} {'帯':16s} {'印':6s} "
          f"{'結果':>9s} {'外れの型':28s} {'最小の変更'}")
    for r in sorted(rows, key=lambda x: (x["rank"], x["race_key"])):
        res = f"◎{r['mult']:.1f}倍" if r["miss_kind"] == "HIT" else "×"
        fix = FIX_KINDS[r["fix_kind"]]
        if "{n}" in fix:
            n = next((x[2:] for x in (r["note"] or "").split(";")
                      if x.startswith("n=")), "?")
            fix = fix.replace("{n}", n)
        extra = (f"（この目 {r['missed_odds']:.1f}倍）"
                 if r["miss_kind"] != "HIT" and r["missed_odds"] else "")
        if r["cf_return"] is not None:
            extra += (f" → {r['cf_return']:.2f}倍"
                      + ("  ※当てても投資割れ" if r["cf_return"] < 1 else ""))
        print(f"{r['race_key']:16s} {r['rank']:5s} {r['n_combos']:2d} "
              f"{r['order']:8s} {r['band']:16s} {r['mark']:6s} {res:>9s} "
              f"{MISS_KINDS[r['miss_kind']]:28s} {fix}{extra}")

    # ── 表示: 商品構成が表示的中率をどう決めているか ──────────────────
    # netkeirin の表示的中率は**全商品の平均**なので、的中率の低い高配当枠を
    # 多く出した日は、モデルが何も悪くなくても表示が下がる。ランクごとの
    # 設計的中率と件数比を並べて、モデルの不調と商品構成の問題を分けて読む。
    print(f"\n----- 商品構成（表示的中率への寄与）-----")
    settled = [r for r in rows if r["miss_kind"] != "NO_RESULT"]
    if settled:
        print(f"{'ランク':6s} {'件数':>4s} {'構成比':>6s} {'的中':>4s} {'的中率':>6s} "
              f"{'寄与(件数比×的中率)':>18s}")
        for rk in sorted(byrank, key=lambda k: -len(byrank[k])):
            g = [x for x in byrank[rk] if x["miss_kind"] != "NO_RESULT"]
            if not g:
                continue
            h = sum(1 for x in g if x["miss_kind"] == "HIT")
            share = len(g) / len(settled)
            print(f"{rk:6s} {len(g):4d} {100*share:5.1f}% {h:4d} "
                  f"{100*h/len(g):5.1f}% {100*share*h/len(g):17.1f}pt")
        tot_h = sum(1 for x in settled if x["miss_kind"] == "HIT")
        print(f"{'合計':6s} {len(settled):4d} {100.0:5.1f}% {tot_h:4d} "
              f"{100*tot_h/len(settled):5.1f}%")

    # ── 表示: 型別（当日）────────────────────────────────────────────
    print(f"\n----- 外れの型（当日）-----")
    cnt = Counter(r["miss_kind"] for r in rows)
    for k, n in cnt.most_common():
        print(f"  {MISS_KINDS[k]:30s} {n:3d}件 ({100*n/len(rows):4.1f}%)")

    # ── 台帳へ追記し、累計で判断する ────────────────────────────────
    if not args.no_append:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        # 🔴 **同じ日を二度流しても増えないこと**（冪等）。日次 cron は結果の
        #    到着待ちで同じ日に複数回走りうるので、追記のみだと台帳が水増しされ、
        #    型別の件数（＝検証候補の判定基準）が壊れる。
        keep: list[dict] = []
        if LEDGER.exists():
            with open(LEDGER, encoding="utf-8") as f:
                keep = [r for r in csv.DictReader(f) if r.get("race_date") != d]
        with open(LEDGER, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(keep + [{k: r[k] for k in rows[0]} for r in rows])
        print(f"\n[台帳] {LEDGER}: {d} の {len(rows)}行を書き込み"
              f"（既存 {len(keep)}行は保持）")

    if LEDGER.exists():
        with open(LEDGER, encoding="utf-8") as f:
            all_rows = list(csv.DictReader(f))
        days = len({r["race_date"] for r in all_rows})
        print(f"\n----- 台帳の累計（{days}日・{len(all_rows)}件）-----")
        pair = Counter((r["rank"], r["miss_kind"], r["fix_kind"]) for r in all_rows
                       if r["miss_kind"] != "HIT")
        print(f"{'ランク':6s} {'外れの型':28s} {'最小の変更':34s} {'件数':>4s}")
        for (rk, mk, fk), n in pair.most_common(12):
            flag = " ← 検証候補" if n >= args.alert_min else ""
            print(f"{rk:6s} {MISS_KINDS.get(mk, mk):28s} "
                  f"{FIX_KINDS.get(fk, fk).replace('{n}', 'N'):34s} {n:4d}{flag}")
        hot = [(k, n) for k, n in pair.items() if n >= args.alert_min]
        print()
        if hot:
            print(f"🔴 {args.alert_min}件以上たまった型が {len(hot)}件あります。"
                  f"**この時点ではまだ何も変えないこと。**")
            print("   次の手順は「その型を潰す変更を1つ決め、全期間 walk-forward で")
            print("   検証してから採否を決める」です（単日・単型の印象で触らない）。")
        else:
            print(f"まだ {args.alert_min}件に達した型はありません。"
                  f"変更の検討には早すぎます（積み上げを続けてください）。")


if __name__ == "__main__":
    main()
