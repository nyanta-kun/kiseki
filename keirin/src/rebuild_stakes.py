"""過去分の再構築で「入稿と同じ傾斜配分」を再現する（2026-08-07 新設）。

## なぜ要るのか

2026-08-07 に netkeirin 入稿を均等割りから**想定着地オッズに応じた傾斜配分**へ
変えた（`src/stake_allocation.py`）。picks_history の過去行は均等割りのままなので、
**新旧が混ざった記録**になっている（[[feedback_full_period_migration]]）。

## 🔴 最終オッズで配分してはいけない

再構築時には最終オッズが手元にあるので、それで配分すると数字は良くなる。
実測（7車 28,415R・2024-07〜2026-08）:

| 配分の基準 | 実質的中率 | 性質 |
|---|---|---|
| 均等（旧記録） | 25.86% | — |
| **モデル p3 のみ** | **33.10%** | 全期間で入手可能・先読みなし |
| 朝オッズ×モデル | 33.16% | 本番と同じ規則 |
| 最終オッズ | **47.67%** | **先読み。実運用では到達不能** |

最終オッズ版は本番より **14.5pt** 高く出る。これはモデルの実力ではなく
「発走時点のオッズを知っていたこと」による。記録に残すと後日
「以前は48%出ていた」と誤読する。**本番と同じ規則だけを使う。**

## 使う規則（本番 `stake_allocation.landing_weights` と同一）

    w = (1/朝オッズ)^0.5 × p3^0.5   … 買う点すべてに朝オッズがある場合
    w = p3                          … それ以外（本番の欠損時フォールバック）

朝オッズは 2026-06-08 以降しか無く、過去全体では 2.4% のレースにしか無い。
実測でも blend − model は **+0.01pt [−0.02, +0.05]** で区別できないが、
**本番と同じ規則を使うことで再構築行とライブ行の継ぎ目を作らない**ことに意味がある
（再構築はライブ行を上書きするので、規則が違うと上書きのたびに数字が動く）。
"""
from __future__ import annotations

import json
import re

from src.database import get_connection
from src.stake_allocation import BUDGET_DEFAULT, tilted_stakes

_SEP_RE = re.compile(r"[-=]")


def load_morning_boards(race_keys: list[str]) -> dict[str, dict[frozenset[int], float]]:
    """race_key → {3車の組: 朝の三連複オッズ}。無い期間は空になる。

    ⚠️ **区切りは表で違う**（`wt_odds` は '1=2=3' / `wt_odds_snapshot` は '1-2-3'）。
    ⚠️ 9999.9 は winticket の「オッズ未確定」センチネルなので採らない。
    """
    out: dict[str, dict[frozenset[int], float]] = {}
    keys = sorted(set(race_keys))
    if not keys:
        return out
    with get_connection() as conn:
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            ph = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT race_key, combination, odds_value FROM wt_odds_snapshot "
                f"WHERE snapshot_type = 'morning' AND bet_type = 'trio' "
                f"AND race_key IN ({ph})",
                chunk,
            ).fetchall()
            for r in rows:
                od = r["odds_value"]
                if od is None or not (0 < float(od) < 9000):
                    continue
                try:
                    key = frozenset(int(x) for x in _SEP_RE.split(str(r["combination"])))
                except ValueError:
                    continue
                if len(key) == 3:
                    out.setdefault(r["race_key"], {})[key] = float(od)
    return out


def load_submitted_stakes(
    race_keys: list[str], rank_label: str,
) -> dict[str, dict[frozenset[int], int]]:
    """race_key → {3車の組: **実際に入稿した賭け金**}。無ければそのレースは入らない。

    ## なぜ要るのか（2026-08-24・実測が起点）

    記録側（本モジュール）は 2026-08-07 の規則「朝オッズ×p3」で組み直しているが、
    **入稿側は 2026-08-11 に「予測オッズの 1/オッズ 単独」へ移った**
    （`stake_allocation.landing_weights`）。以来ずっと別の配分で記録している。

    実測（2026-08-16〜・実入稿と突合できた 107件）:

        記録側(picks_history) 投資 1,070,000円 / 払戻 677,530円 / **ROI 63.3%**
        実入稿(bet_detail)    投資 1,070,000円 / 払戻 835,113円 / **ROI 78.0%**
                                                            → **差 −14.7pt**

    的中39件のうち **35件(90%)** で当たった目の賭け金が食い違っていた。
    ＝ **Web に出ている実績が、実際に売った商品を説明していない。**

    ## 🔴 予測オッズを再現するのではなく、記録された事実を使う

    予測オッズで組み直す案は採らない。三連複のオッズ予測モデルは
    `train_end: 2026-08-04` で、それ以前へ当てると **model-vintage look-ahead**
    になる（`stakes_for_combos` 内の警告と同じ理由）。
    **`bet_detail` は入稿時に保存された事実**なのでモデルを介さず、先読みの余地が無い。

    ⚠️ `bet_detail` の保存は **2026-08-07 開始**。それ以前は空で返るので、
       呼び出し側は従来どおりモデル規則へ落ちる（＝継ぎ目はそこに1つだけ残る）。
    ⚠️ **取消済み（`deleted_at`）も含める。** 取り消したのは「売る/売らない」の判断で
       あって、その商品の買い目の定義は入稿時に確定している。記録側は
       「モデルが推奨した買い目」を残す場所なので、取消を理由に別の配分へ戻すと
       同じレースが取消の有無で違う金額になる。
    """
    out: dict[str, dict[frozenset[int], int]] = {}
    keys = sorted(set(race_keys))
    if not keys:
        return out
    with get_connection() as conn:
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            ph = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT race_key, bet_detail FROM netkeirin_submissions "
                f"WHERE rank_key = ? AND bet_detail IS NOT NULL "
                f"AND race_key IN ({ph})",
                [rank_label, *chunk],
            ).fetchall()
            for r in rows:
                try:
                    lines = (json.loads(r["bet_detail"]) or {}).get("lines") or []
                except (TypeError, ValueError):
                    continue
                got: dict[frozenset[int], int] = {}
                for ln in lines:
                    # 🔴 三連複だけを採る。三連単は目の形（順序つき）が違うので
                    #    frozenset へ畳むと別の目と衝突する。
                    if ln.get("bet_type") != "3連複":
                        got = {}
                        break
                    try:
                        combo = frozenset(int(x) for x in _SEP_RE.split(str(ln["combo"])))
                        stake = int(ln["stake"])
                    except (KeyError, TypeError, ValueError):
                        got = {}
                        break
                    if len(combo) != 3 or stake <= 0:
                        got = {}
                        break
                    got[combo] = stake
                if got:
                    out[r["race_key"]] = got
    return out


def stakes_for_combos(
    axis1: int,
    axis2: int,
    combos: list[frozenset[int]],
    top3_probs: dict[int, float],
    board: dict[frozenset[int], float] | None = None,
    budget: int = BUDGET_DEFAULT,
    submitted: dict[frozenset[int], int] | None = None,
) -> dict[frozenset[int], int]:
    """買う目 → 賭け金。入稿時と同じ配分を再現する。

    combos は三連複・軸2車ながしの目（{axis1, axis2, 3列目}）。
    top3_probs は **その時点の vintage 予測**（0-1 スケール）。
    board は朝オッズ盤面（無ければ p3 単独へ落ちる＝本番と同じ）。

    submitted は **実際に入稿した賭け金**（`load_submitted_stakes`）。
    🔴 **あれば最優先で採る**（2026-08-24）。記録側の規則は 2026-08-07 のままで、
       入稿側が 2026-08-11 に予測オッズへ移って以来ずれていた（実測 ROI −14.7pt）。
       再現しようとせず**記録された事実をそのまま使う**のが、定義上ずれない唯一の形。
    🔴 **目の集合が完全に一致するときだけ使う。** 欠車や再入稿で買い目が違えば
       別の商品なので、部分的に混ぜると点数も合計額も壊れる。
    """
    # 🔴 **p3 が無ければ落とす。黙って均等へ落としてはいけない。**
    #    本番の `landing_weights` は p3 欠損時に均等へフォールバックするが、
    #    それは「入稿時に予測が読めなかった」ための救済。再構築では p3 は
    #    必ず手元にあるので、**空なら呼び出し側のバグ**である。
    #    2026-08-07: 実際に7ランク中5つで候補dictへの `top3_probs` 登録が漏れ、
    #    11時間の再構築が丸ごと均等配分で走った（実質的中率が +0.22pt しか
    #    動かず気づいた）。無言のフォールバックは検知できない。
    if not top3_probs:
        raise ValueError(
            "top3_probs が空です。候補dictへ 'top3_probs' を載せ忘れていませんか"
            "（再構築では p3 は必ず取れるはずで、空なら呼び出し側のバグ）")
    # 🔴 実際に入稿した配分があればそれを使う（**目の集合が完全一致するときだけ**）。
    #    ここを先に見るので、以下のモデル規則は「入稿の記録が無い分」だけに効く。
    if submitted and set(submitted) == set(combos):
        return {c: int(submitted[c]) for c in combos}

    thirds = [next(iter(c - {axis1, axis2})) for c in combos]
    missing = [t for t in thirds if not top3_probs.get(t)]
    if missing:
        raise ValueError(f"top3_probs に買う相手の値がありません: {missing}")
    morning = None
    if board:
        got = {t: board.get(frozenset({axis1, axis2, t})) for t in thirds}
        if all(v for v in got.values()):
            morning = got
    # 🔴 ここに `src.odds_prediction` の予測オッズを渡してはいけない（2026-08-11）。
    #    本番の入稿経路（netkeirin_submit_wt._build_tilted_legs）は渡しているが、
    #    再構築は **その日の時点で得られた情報だけ**で組み直すためのもの。
    #    オッズ予測モデルは全期間で1回学習しており、過去レースへ遡って当てると
    #    model-vintage look-ahead になる（[[keirin_highpay_payout_ceiling_2026_08_06]]・
    #    [[chihou_survivor_bias_audit_2026_07_23]] と同型）。
    #    再構築で使いたくなったら、まず vintage 別モデルを用意すること。
    stakes, _ = tilted_stakes(thirds, morning, top3_probs, budget=budget)
    return {frozenset({axis1, axis2, t}): stakes[t] for t in thirds}
