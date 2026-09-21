"""netkeirin へ実際に入稿した賭け金を採点の正本として引く。

## なぜ必要か（2026-08-08 に見つかった実バグ）

入稿は**点ごとの傾斜配分**（`w=(1/朝オッズ)^0.5 × p3^0.5`）で金額を決めるのに、
採点側は**全点に同じ単価**を掛けていた:

    c7_pay = c7_trio_pay * c7_stake // 100     # c7_stake は1レース1個のスカラー

立川3R(2026-08-08) の実例: 的中した `3=5=7` の入稿額は 1,300円・三連複配当は
1,250円/100円なので払戻は **16,250円**。ところが記録は 10,000÷5点=2,000円で
計算した **25,000円** だった。

⚠️ **ずれ方に方向性がある。** 傾斜配分は高オッズの点ほど薄く張るので、
**高配当が当たったときほど過大に記録される**。`bet_detail` を持つ全13的中で
実測したところ 記録 219,330 / 正しい 186,810 ＝ **+17.4% 過大**で、
2日間のサマリー ROI は 0.750 → 0.698（約5pt甘い）だった。

さらに再構築（tail reconcile）は点ごとの配分を使うものの**入稿時ではない
オッズで配分し直す**ため、ライブ行・再構築行・実際に賭けた額の3つが併存していた。

## 方針

**`netkeirin_submissions.bet_detail` を賭け金の単一正本にする。**
実際に netkeirin へ出した金額そのものなので定義上ずれない。

⚠️ `bet_detail` は **2026-08-07 以降しか無い**（kiseki PR#77 で追加）。
それ以前と未入稿レースは復元不能なので、呼び出し側は None を受けたら
従来のスカラー単価へフォールバックする（過去の数字を作り変えない）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .result_top3 import TOP3_SQL, winning_trifectas, winning_trios

logger = logging.getLogger(__name__)


def payout_per_100(odds_value: float | None) -> int:
    """確定オッズ → 100円賭けたときの払戻金（公式は10円単位で切り捨て）。

    🔴 `src/evaluation/backtest_wt.py::_load_payouts_wt` と**同じ式**。
       あちらは pandas を引くので採点の軽い経路からは import せず、
       `tests/test_dead_heat_payout.py` が両者の一致を固定している。
    """
    if odds_value is None:
        return 0
    return round(float(odds_value) * 100) // 10 * 10


def dead_heat_extra_payout(conn: Any, race_key: str,
                           stakes: dict[Any, int], paid_key: Any) -> int:
    """**同着でもう一方の当たり目も買っていた**ぶんの払戻を足す。

    ## なぜ要るか（2026-09-21 に確定した実バグ）

    競輪には同着があり、3着が2車同着なら三連複の当たりは2通りになる。
    `result_top3.hit_trio` / `hit_trifecta` は**買った目のうち当たったものを1つ**
    しか返さず、`resolve_payout` も `winning_key` を単数で受ける設計だったため、
    **2通りとも買っていたレースで片方しか払戻が記録されていなかった**。

    実測: `picks_history` **13行**（7C×6 / 9C×3 / 7B / 7S / 9F）。
    確定事例 `20260822_31_03#9C`（3着が7番と9番の同着・`3=5=7 ¥5,400` と
    `3=5=9 ¥2,500` を両方入稿）は記録 **¥12,420 ↔ 正しくは ¥25,419**。

    ⚠️ **型ラボ（`settle_type_lab_picks.py`）と実売の正本
       （`backend/src/services/keirin_settlement.py`）は元から全当たり目を
       合算していて正しい。** 直すのは既存ランク経路だけ。

    ## 設計

    🔴 **呼び出し側（`notify_results_wt.py` の12箇所・backfill 14本）を
       1つも変えずに直せるよう、共通入口の `resolve_payout` の中で足す。**
       同着でないレースでは `wins` が1通りなので**返り値は必ず 0**＝
       既存の数字はビット単位で変わらない。

    Args:
        stakes: 入稿記録から作った {買い目: 賭け金}
        paid_key: すでに払戻を計上した当たり目（これは二重に数えない）
    """
    if not stakes:
        return 0
    ordered = isinstance(paid_key, tuple)
    fin = conn.execute(TOP3_SQL, (race_key,)).fetchall()
    wins = winning_trifectas(fin) if ordered else winning_trios(fin)
    others = [w for w in wins if w != paid_key and w in stakes]
    if not others:
        return 0

    market = "trifecta" if ordered else "trio"
    # 🔴 `combination` の書式を仮定しない。三連複も `-` 区切りで入っている
    #    （実測 `20260822_31_03` の trio は `3-5-7` / `3-5-9`）。`=` 決め打ちで
    #    引くと**1件も見つからないのに例外は出ず、静かに 0 円**になる。
    #    `_load_payouts_wt` と同じく**パースしてキーを作る**。
    rows = conn.execute(
        "SELECT combination, odds_value FROM wt_odds WHERE race_key = ? AND bet_type = ?",
        (race_key, market),
    ).fetchall()
    board: dict[Any, float] = {}
    for combo, odds_value in rows:
        parts = [x for x in re.split(r"[-=→]", str(combo)) if x != ""]
        try:
            nums = [int(x) for x in parts]
        except ValueError:
            continue
        board[tuple(nums) if ordered else frozenset(nums)] = odds_value

    extra = 0
    for w in others:
        if w not in board:
            logger.warning(
                f"同着のもう一方の払戻が引けない {race_key} {market} {sorted(w)}")
            continue
        extra += payout_per_100(board[w]) * stakes[w] // 100
    return extra

# bet_detail の bet_type 表記。netkeirin の商品名に合わせた日本語が入る。
TRIO = "3連複"
TRIFECTA = "3連単"

_SPLIT_RE = re.compile(r"[=\-]")


def _combo_key(bet_type: str, combo: str) -> frozenset[int] | tuple[int, ...] | None:
    """買い目文字列を照合キーへ変換する。

    三連複は順序を持たない（`frozenset`）、三連単は順序を持つ（`tuple`）。
    ここを取り違えると「当たっているのに拾えない」「別の点の金額を拾う」になる。
    """
    parts = [int(x) for x in _SPLIT_RE.split(combo) if x.isdigit()]
    if len(parts) != 3:
        return None
    if bet_type.startswith(TRIFECTA):
        return tuple(parts)
    return frozenset(parts)


def load_submitted_bet(conn: Any, race_key: str, rank_key: str) -> dict | None:
    """入稿記録（bet_detail）を読む。無ければ None。

    Args:
        race_key: サフィックス無しのレースキー（`20260808_28_03`）
        rank_key: `7C` / `7H1` など（picks_history の `#` 以降と同じ）
    """
    try:
        row = conn.execute(
            "SELECT bet_detail FROM netkeirin_submissions WHERE race_key=? AND rank_key=?",
            (race_key, rank_key),
        ).fetchone()
    except Exception as e:  # テーブルが無い環境（旧DB）でも採点自体は続ける
        logger.debug(f"netkeirin_submissions 参照失敗 {race_key}#{rank_key}: {e}")
        return None

    if not row or not row[0]:
        return None
    raw = row[0]
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as e:
        logger.warning(f"bet_detail のJSONが壊れている {race_key}#{rank_key}: {e}")
        return None


def submitted_stakes(
    conn: Any, race_key: str, rank_key: str
) -> tuple[dict[Any, int], int] | None:
    """実際に入稿した {買い目キー: 金額} と合計投資額を返す。

    Returns:
        ({frozenset|tuple: stake}, total) または None（入稿記録なし）
    """
    bet = load_submitted_bet(conn, race_key, rank_key)
    if not bet:
        return None

    stakes: dict[Any, int] = {}
    for line in bet.get("lines") or []:
        key = _combo_key(str(line.get("bet_type") or ""), str(line.get("combo") or ""))
        stake = line.get("stake")
        if key is None or not isinstance(stake, int):
            continue
        stakes[key] = stake

    if not stakes:
        return None

    total = bet.get("total")
    if not isinstance(total, int) or total <= 0:
        total = sum(stakes.values())
    return stakes, total


def resolve_payout(
    conn: Any,
    race_key: str,
    rank_key: str,
    *,
    hit: bool,
    winning_key: frozenset[int] | tuple[int, ...],
    odds_payout: int,
    fallback_stake: int,
    n_combos: int,
) -> tuple[int, int]:
    """(払戻, 投資額) を決める。採点各ランクの共通入口。

    入稿記録があればそれを使い、無ければ従来どおり単価×点数へフォールバックする。
    **不的中でも投資額は入稿記録の合計を優先する**（傾斜配分では端数の寄せ方で
    単価×点数と合計が一致しないため）。
    """
    found = submitted_stakes(conn, race_key, rank_key)
    if found is not None:
        stakes, total = found
        if not hit:
            return 0, total
        stake = stakes.get(winning_key)
        if stake is not None:
            # 🔴 同着では当たり目が複数ある。**買っていれば全部払い戻される**ので、
            #    もう一方のぶんもここで足す（同着でなければ 0 が返る）。
            pay = odds_payout * stake // 100
            return pay + dead_heat_extra_payout(conn, race_key, stakes, winning_key), total
        # 入稿記録はあるが的中点が無い（欠車での組み替え等）。黙って0にしない。
        logger.warning(
            f"入稿記録に的中点が無い {race_key}#{rank_key} winning={sorted(winning_key)}"
        )

    pay = odds_payout * fallback_stake // 100 if hit else 0
    return pay, n_combos * fallback_stake


def payout_from_submitted(
    conn: Any,
    race_key: str,
    rank_key: str,
    winning_key: frozenset[int] | tuple[int, ...],
    odds_payout: int,
) -> tuple[int, int] | None:
    """入稿額で払戻と投資額を計算する。

    Args:
        winning_key: 的中した買い目（三連複=frozenset / 三連単=tuple）
        odds_payout: その券種の100円あたり確定払戻

    Returns:
        (払戻, 投資額) または None（入稿記録が無い＝呼び出し側でフォールバック）
    """
    found = submitted_stakes(conn, race_key, rank_key)
    if found is None:
        return None
    stakes, total = found

    stake = stakes.get(winning_key)
    if stake is None:
        # 入稿記録はあるが的中点が含まれない。欠車での組み替え等で起こりうるので、
        # 黙って0円にせずフォールバックさせる（取りこぼしを無言にしない）。
        logger.warning(
            f"入稿記録に的中点が無い {race_key}#{rank_key} winning={sorted(winning_key)}"
        )
        return None

    # 🔴 同着のもう一方も買っていれば足す（`resolve_payout` と同じ扱い）。
    pay = odds_payout * stake // 100
    return pay + dead_heat_extra_payout(conn, race_key, stakes, winning_key), total
