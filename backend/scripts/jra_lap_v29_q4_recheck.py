"""JRA v29（ラップ7列）の 2026Q4 再確認（事前登録 §9）。

事前登録: `docs/jra_lap_v29_q3_confirm_plan_2026_11_07.md` §9
（**2026Q3 の結果を見た後、Q4 を一度も見る前に書いた**）。
本スクリプトは §9 をそのまま実装するだけで、腕・seed・bootstrap・判定を動かさない。

## 何をするか

1. Q3 の脚: 学習 〜2026-06-30 → 2026Q3 を予測（§8 と同じ。再現の検査を兼ねる）
2. Q4 の脚: 学習 〜2026-09-30 → 2026Q4 を予測
3. Q4 単独の Δ と CI、Q3+Q4 を合わせた Δ と CI（レース単位 bootstrap）
4. §9 の表で対応を決め、`record_test_usage()` で台帳に残す

🔴 **合わせ方は walk-forward**: Q3 は 6/30 までで学習したモデル、Q4 は 9/30 までで
学習したモデルの予測を腕ごとに縦に繋ぎ、その和集合を1回 bootstrap する。
9/30 までの1モデルで Q3 も予測すると Q3 が学習に入ってしまう。

## 独立実装をしない

学習・正規化・CI・判定はすべて `jra_lap_v29_q3_confirm` の関数を呼ぶ
（`fit_condition` / `win_delta` / `assert_no_test_rows`）。

## 早く走らせない

DB には 2026-10-01 以降の行がもう入っている。Q4 が閉じる前に Δ を出すと
TEST を開封したことになるので、本番の実行は **2027-01-01 以降**でないと止まる。
その日には `jra_protocol.TEST_START` が 20270101 に繰り上がるので、
台帳の行が正しい窓を指すように **`JRA_TEST_START=20261001` を付けて起動する**
（付け忘れたら止まる）。

    cd backend
    ~/bin/kiseki-ro env JRA_TEST_START=20261001 .venv/bin/python scripts/jra_lap_v29_q4_recheck.py \\
        --cache /tmp/v29_q4_dataset.pkl

1/1 03:20 の四半期ローリング（本番モデルの再学習）とは独立に学習するので前後は問わない。
ただし結果が「v28 へ戻す」なら、2027Q1 の v29 バックフィルより前に戻すこと。

## 試走（`--rehearsal`）

窓を1四半期ずつ前へずらす（Q2 の脚 → Q3 の脚）。使うのは 2026-09-30 までの
VAL だけなので TEST を開けない。台帳にも書かない。Q3 の脚が §8 の数字を
再現するかもここで確かめられる。

    ~/bin/kiseki-ro .venv/bin/python scripts/jra_lap_v29_q4_recheck.py --rehearsal \\
        --cache /tmp/v29_rehearsal_dataset.pkl
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pandas as pd  # noqa: E402

import scripts.jra_lap_feature_ab as lab  # noqa: E402
import scripts.jra_winplace_feature_ab as ab  # noqa: E402
from scripts.jra_lap_v29_q3_confirm import (  # noqa: E402
    ARMS,
    BOOT_SEED,
    PREREG,
    assert_no_test_rows,
    fit_condition,
    win_delta,
)
from src import jra_protocol  # noqa: E402

logger = logging.getLogger("lap_v29_q4")

Q3_JSON = _root.parent / "docs" / "model_verification" / "jra_lap_v29_q3_confirm.json"
OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_lap_v29_q4_recheck.json"

# §9 の表の文言そのまま（台帳と文書を grep で突き合わせられるように）
ACTION_KEEP = "v29 を続ける"
ACTION_KEEP_RECHECK = "v29 を続け、2027Q1 でもう一度同じ比較をする"
ACTION_ROLLBACK = "v28 へ戻す"

# Q3 の脚が §8 と一致しているかの許容幅（§8 の JSON は小数5桁で丸めてある）
REPRO_TOL = 6e-5


@dataclass(frozen=True)
class Leg:
    """1四半期ぶんの脚。学習は eval_start の前日まで。"""

    quarter: str
    eval_start: str
    eval_end: str


Q2 = Leg("2026Q2", "20260401", "20260630")
Q3 = Leg("2026Q3", "20260701", "20260930")
Q4 = Leg("2026Q4", "20261001", "20261231")

REAL = (Q3, Q4)  # §9
REHEARSAL = (Q2, Q3)  # 1四半期前へずらした試走（VAL のみ）


def hard_stop_after(leg: Leg) -> str:
    """後ろの脚の翌日。これ以降の行を読んだら止める。"""
    d = datetime.datetime.strptime(leg.eval_end, "%Y%m%d").date() + datetime.timedelta(days=1)
    return d.strftime("%Y%m%d")


def check_window_closed(later: Leg, today: datetime.date, test_start: str) -> None:
    """🔴 本番の実行は Q4 が閉じてから。台帳が Q4 を指すよう TEST_START も固定させる。"""
    end = datetime.datetime.strptime(later.eval_end, "%Y%m%d").date()
    if today <= end:
        raise SystemExit(
            f"🔴 {later.quarter} はまだ閉じていない（今日 {today} ≤ {end}）。"
            f"途中で Δ を出すと TEST を開封したことになる。試走は --rehearsal で"
        )
    if test_start != later.eval_start:
        raise SystemExit(
            f"🔴 jra_protocol.TEST_START={test_start}。台帳の行が {later.quarter} を指すよう "
            f"JRA_TEST_START={later.eval_start} を付けて起動すること"
        )


def q4_action(later_alone: dict, pooled: dict) -> str:
    """§9 の表。漏れが無いよう「それ以外は戻す」で閉じる。

    表の3行目は「CI が 0 を跨ぐ」だが、CI が丸ごと 0 より上でも戻すのが自然なので
    「上端 < 0 でなければ戻す」とした（§9 の趣旨どおり）。
    """
    if later_alone["delta_logloss"] < 0:
        return ACTION_KEEP
    if pooled["ci95"][1] < 0:
        return ACTION_KEEP_RECHECK
    return ACTION_ROLLBACK


def pool_legs(evs_by_leg: list[dict[str, pd.DataFrame]]) -> dict[str, pd.DataFrame]:
    """腕ごとに各脚の評価行を縦に繋ぐ（walk-forward で合わせる）。"""
    out: dict[str, pd.DataFrame] = {}
    for arm in ARMS:
        frames = [e[arm] for e in evs_by_leg]
        ids = [set(f["race_id"]) for f in frames]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if ids[i] & ids[j]:
                    raise SystemExit("🔴 脚の間で race_id が重なった。窓が重なっている")
        out[arm] = pd.concat(frames, ignore_index=True)
    return out


def check_q3_reproduces(q3_result: dict, stored: dict, tol: float = REPRO_TOL) -> list[str]:
    """§8 の Q3 の主指標と一致しているか。ずれた項目を返す（空なら一致）。"""
    bad = []
    if q3_result["n_races"] != stored["n_races"]:
        bad.append(f"n_races {q3_result['n_races']} ≠ {stored['n_races']}")
    pairs = [
        ("delta_logloss", q3_result["delta_logloss"], stored["delta_logloss"]),
        ("ci95_lo", q3_result["ci95"][0], stored["ci95"][0]),
        ("ci95_hi", q3_result["ci95"][1], stored["ci95"][1]),
        ("v28_logloss", q3_result["v28_logloss"], stored["v28_logloss"]),
        ("lap_logloss", q3_result["lap_logloss"], stored["lap_logloss"]),
    ]
    for k, a, b in pairs:
        if abs(a - b) > tol:
            bad.append(f"{k} {a:+.5f} ≠ {b:+.5f}")
    return bad


def _fmt(c: dict) -> str:
    return (
        f"n={c['n_races']:>4}R v28={c['v28_logloss']:.5f} lap={c['lap_logloss']:.5f} "
        f"Δ={c['delta_logloss']:+.5f} 95%CI=[{c['ci95'][0]:+.5f}, {c['ci95'][1]:+.5f}]"
    )


def record_usage(later: Leg, alone: dict, pooled: dict, action: str) -> None:
    """台帳に1行残す（同じスクリプトの行が既にあれば足さない）。"""
    ledger = Path(jra_protocol._LEDGER_PATH)
    script = f"scripts/{_here.name}"
    if ledger.exists() and script in ledger.read_text():
        print(f"\n台帳に {script} の行が既にある。追記しない（冪等）")
        return
    decision = f"v29（ラップ7列）の {later.quarter} 再確認（事前登録 §9）"
    note = (
        f"{later.quarter} 単独 Δ={alone['delta_logloss']:+.5f} "
        f"[{alone['ci95'][0]:+.5f}, {alone['ci95'][1]:+.5f}] {alone['n_races']}R / "
        f"Q3+{later.quarter[-2:]} Δ={pooled['delta_logloss']:+.5f} "
        f"[{pooled['ci95'][0]:+.5f}, {pooled['ci95'][1]:+.5f}] {pooled['n_races']}R。**{action}**"
    )
    jra_protocol.record_test_usage(decision, script, note)
    print(f"\n🔴 台帳へ追記した: {ledger}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--rehearsal", action="store_true", help="1四半期前へずらした試走（VAL のみ・台帳に書かない）")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--data-start", default="20230101")
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--out", default=None)
    p.add_argument("--cache", default=None, help="データセット pickle（ラップ特徴込み）")
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    earlier, later = REHEARSAL if args.rehearsal else REAL
    hard_stop = hard_stop_after(later)
    if not args.rehearsal:
        check_window_closed(later, datetime.date.today(), jra_protocol.TEST_START)
    out_path = (
        Path(args.out)
        if args.out
        else (OUT_PATH.with_name(OUT_PATH.stem + "_rehearsal.json") if args.rehearsal else OUT_PATH)
    )
    logger.info("脚: %s → %s / hard_stop=%s / %s", earlier.quarter, later.quarter, hard_stop, jra_protocol.describe())

    cache = Path(args.cache) if args.cache else None
    lap_meta: dict = {}
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info("データセットをキャッシュから読込: %s", cache)
    else:
        df = ab.build_dataset(args.data_start, later.eval_end)
        assert_no_test_rows(df, hard_stop)
        df, lap_meta = lab.attach_lap_features(df, later.eval_end)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache)
    assert_no_test_rows(df, hard_stop)
    df = df.reset_index(drop=True)

    legs: list[tuple[Leg, dict[str, pd.DataFrame], dict]] = []
    for leg in (earlier, later):
        logger.info("=== %s の脚（学習 < %s） ===", leg.quarter, leg.eval_start)
        evs, info = fit_condition(
            df,
            seeds,
            args.valid_days,
            with_place_head=False,
            eval_start=leg.eval_start,
            eval_end=leg.eval_end,
            quarter=leg.quarter,
        )
        legs.append((leg, evs, info))

    by_leg = {leg.quarter: win_delta(evs, args.bootstrap) for leg, evs, _ in legs}
    pooled = win_delta(pool_legs([evs for _, evs, _ in legs]), args.bootstrap)

    # Q3 の脚は §8 と同じ計算のはず。ずれていたら作り（build_dataset 等）が変わっている
    stored = json.loads(Q3_JSON.read_text())["results"]["primary"]
    repro = check_q3_reproduces(by_leg[Q3.quarter], stored)
    if repro:
        raise SystemExit(f"🔴 Q3 の脚が §8 を再現しない: {repro}。判定に進まない")
    logger.info("Q3 の脚は §8 を再現した（許容 %.0e）", REPRO_TOL)

    alone = by_leg[later.quarter]
    action = q4_action(alone, pooled)

    if not args.rehearsal:
        record_usage(later, alone, pooled, action)  # 結果を表示する前に残す

    print("\n" + "=" * 96)
    print(f"  単勝 多項対数損失  lap − v28（負＝改善）  {'【試走】' if args.rehearsal else ''}")
    print("=" * 96)
    for q, c in by_leg.items():
        print(f"  {q:<12} {_fmt(c)}")
    print(f"  {earlier.quarter[-2:]}+{later.quarter[-2:]} 合算   {_fmt(pooled)}")
    print(f"\n  §9 の対応: **{action}**")

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "preregistration": f"{PREREG} §9",
        "rehearsal": args.rehearsal,
        "protocol": jra_protocol.describe(),
        "legs": [
            {"quarter": leg.quarter, "eval_start": leg.eval_start, "eval_end": leg.eval_end, "fit_info": info}
            for leg, _, info in legs
        ],
        "hard_stop": hard_stop,
        "arms": {a: {"n_features": len(c)} for a, c in ARMS.items()},
        "seeds": seeds,
        "bootstrap": args.bootstrap,
        "boot_seed": BOOT_SEED,
        "valid_days": args.valid_days,
        "lap_meta": lap_meta,
        "q3_reproduces_section8": True,
        "criteria": f"{later.quarter} 単独の点推定<0 → {ACTION_KEEP} / 合算 CI 上端<0 → {ACTION_KEEP_RECHECK} / "
        f"それ以外 → {ACTION_ROLLBACK}（§9）",
        "results": {"by_leg": by_leg, "pooled": pooled, "action": action},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {out_path}")


if __name__ == "__main__":
    main()
