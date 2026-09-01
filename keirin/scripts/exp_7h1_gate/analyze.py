#!/usr/bin/env python3
"""7H1 に「本命が強すぎるレースは見送る」ゲートを足せるかを検証する。

## 検証する仮説（ユーザー提案・2026-08-25）

> 7H1 は優先順位だけで無茶狙いになっている例がある。本命の1着率が一定以上、
> または2番手との差が一定以上あるレースは見送れば、7H1 の的中率が上がり、
> 見送ったレースは他ランクが拾って的中になるのではないか。

## 測り方

- 母集団は `build_cache.py` が作った **月次凍結 vintage** の 7H1 選別結果
  （本番 `build_7h1_candidates.build()` と一致することを検証済み）
- 探索窓 2024-04〜2025-12 / 確認窓 2026-01〜2026-08（**年をまたぐ**）
- ROI は裾依存が強いのでレース単位ブートストラップの CI を必ず出す

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7h1_gate/analyze.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CACHE = REPO / "data" / "exp" / "7h1_gate_cache.jsonl"
EXPLORE = ("2024-04-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-12-31")


def load() -> list[dict]:
    rows = []
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("selected") and r.get("scored"):
            # 1着固定車（別ライン先頭）が本命以外6車の3着内率順で何番目か。
            # 弱い車を1着に固定していると当たりようがない、という仮説の検査用。
            lead = int(str(r["legs_tf"][0]).split("-")[0])
            oth = list(r.get("others") or [])
            r["lead_rank"] = float(oth.index(lead)) if lead in oth else -1.0
            rows.append(r)
    return rows


def stats(sub: list[dict]) -> dict:
    n = len(sub)
    if not n:
        return dict(n=0)
    hit = sum(r["hit"] for r in sub)
    pay = sum(r["payout"] for r in sub)
    bet = sum(r["bet_amount"] for r in sub)
    # netkeirin の表示的中は**ガミを不的中として数える**（払戻 > 賭け金）。
    real = sum(1 for r in sub if r["hit"] and r["payout"] > r["bet_amount"])
    bust = sum(r["fav_bust"] for r in sub)
    favwin = sum(r["fav_win"] for r in sub)
    days = len({r["race_date"] for r in sub})
    return dict(n=n, hit=hit, hit_rate=hit / n * 100, real=real,
                real_rate=real / n * 100, roi=pay / bet * 100,
                bust=bust / n * 100, favwin=favwin / n * 100,
                per_day=n / max(days, 1), pay=pay, bet=bet)


def boot_ci(sub: list[dict], key: str, iters: int = 2000, seed: int = 7):
    """レース単位ブートストラップの 95% CI（ROI / 的中率）。"""
    if not sub:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    vals = []
    n = len(sub)
    for _ in range(iters):
        s = [sub[rnd.randrange(n)] for _ in range(n)]
        if key == "roi":
            b = sum(r["bet_amount"] for r in s)
            vals.append(sum(r["payout"] for r in s) / b * 100 if b else 0.0)
        else:
            vals.append(sum(r["hit"] for r in s) / n * 100)
    vals.sort()
    return (vals[int(iters * 0.025)], vals[int(iters * 0.975)])


def line(label: str, s: dict) -> str:
    if not s.get("n"):
        return f"{label:26s} n=0"
    return (f"{label:26s} n={s['n']:5d} ({s['per_day']:.2f}件/日) "
            f"的中={s['hit']:3d} {s['hit_rate']:5.2f}% (表示{s['real_rate']:5.2f}%)  "
            f"ROI={s['roi']:6.1f}%  "
            f"本命バスト={s['bust']:5.1f}%  本命1着={s['favwin']:5.1f}%")


def window(rows, w):
    return [r for r in rows if w[0] <= r["race_date"] <= w[1]]


def quantile_table(rows, key, label, nq=5):
    print(f"\n### {label} の{nq}分位（窓別）")
    for wname, w in (("探索 2024-04〜2025-12", EXPLORE), ("確認 2026-01〜", CONFIRM)):
        sub = sorted(window(rows, w), key=lambda r: r[key])
        if not sub:
            continue
        print(f"-- {wname} (n={len(sub)})")
        q = len(sub) // nq
        for i in range(nq):
            part = sub[i * q:(i + 1) * q] if i < nq - 1 else sub[i * q:]
            rng = f"{part[0][key]:.3f}-{part[-1][key]:.3f}"
            print("   " + line(rng, stats(part)))


def rule_table(rows, key, thresholds, label):
    print(f"\n### 見送り規則: {label} >= しきい値 を除外")
    print(f"{'閾値':>8} | {'窓':<10} | {'残n':>5} {'件/日':>6} {'的中%':>6} "
          f"{'ROI%':>7} {'ROI 95%CI':>18} | {'除外n':>5} {'除外的中%':>8} {'除外ROI%':>8}")
    for th in thresholds:
        for wname, w in (("探索", EXPLORE), ("確認", CONFIRM)):
            sub = window(rows, w)
            keep = [r for r in sub if r[key] < th]
            drop = [r for r in sub if r[key] >= th]
            sk, sd = stats(keep), stats(drop)
            if not sk.get("n"):
                continue
            lo, hi = boot_ci(keep, "roi")
            print(f"{th:8.3f} | {wname:<10} | {sk['n']:5d} {sk['per_day']:6.2f} "
                  f"{sk['hit_rate']:6.2f} {sk['roi']:7.1f} [{lo:7.1f},{hi:7.1f}] | "
                  f"{sd.get('n', 0):5d} {sd.get('hit_rate', 0):8.2f} {sd.get('roi', 0):8.1f}")


def main() -> None:
    rows = load()
    print(f"7H1 選別・採点済み: {len(rows)}件 "
          f"({min(r['race_date'] for r in rows)}〜{max(r['race_date'] for r in rows)})")
    print("\n## 0. ベースライン")
    for wname, w in (("全期間", ("2000-01-01", "2999-12-31")),
                     ("探索 2024-04〜2025-12", EXPLORE),
                     ("確認 2026-01〜", CONFIRM)):
        s = stats(window(rows, w))
        print(line(wname, s))
        if s.get("n"):
            lo, hi = boot_ci(window(rows, w), "roi")
            print(f"{'':26s}   ROI 95%CI [{lo:.1f}, {hi:.1f}]")

    for key, label in (("fav_ppw_norm", "本命の表示1着率（正規化）"),
                       ("gap12_norm", "抜け度（表示1着率の1-2位差）"),
                       ("fav_ppw", "本命の生1着率（モデル出力）"),
                       ("gap12", "抜け度（生1着率の1-2位差・現行ゲートの量）"),
                       ("bust_prob", "バスト確率（現行ゲートの量）"),
                       ("lead_rank", "1着固定車の3着内率順位（本命除く6車中）")):
        quantile_table(rows, key, label)

    rule_table(rows, "fav_ppw_norm", [0.42, 0.45, 0.48, 0.50, 0.55],
               "本命の表示1着率")
    rule_table(rows, "gap12_norm", [0.22, 0.25, 0.28, 0.30, 0.35],
               "抜け度（表示）")


if __name__ == "__main__":
    main()
