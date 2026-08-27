"""型ラボ — レースの型判定と、型ごとの買い目・配分の**唯一の正本**（2026-08-27 新設）。

## なにをするモジュールか

レースを A〜F の6型に分け、型ごとに決めた構成で三連単／三連複の買い目と賭け金を作る。
**ペーパー検証（過去）と実地検証（当日）の両方がこの1ファイルを呼ぶ**ので、
「検証した商品と違うものを売る」ことが構造的に起きない。

設計と全実測: `keirin/docs/type_lab/SUMMARY.md`（+ `type_a.md`〜`type_f.md`）。
🔴 **既存ランク（7C/7S/7M1/7T1…）には一切触らない。** 別テーブル
`keirin.type_lab_picks` へ書き、既存の一覧・統計・入稿には出さない。

## 6層 → 型

    ① 軸の堅さ  axis_sum = 3着内率 上位2車の合計（境界 1.44 = RANK_7C_P3_SUM_MIN）→ 的中率
    ②〜⑥ 荒れ度 arare（下記の加算）→ 配当

      arare = +1 if 指数1位のライン人数==2 else (-1 if >=4 else 0)      # ③ ライン構成
            + (-1 if 先頭の遅れ率 >= 11% else +1)                       # ④ ラインの維持
            + (+2 if 先頭の脚質 == '追' else 0)                          # ⑥a 並びの妥当性
            + (開催日目 - 2)                                            # ⑤ 開催日目
            + (+1 if 番手の競走得点 > 先頭の競走得点 else 0)              # ⑥c 並びの妥当性

    型 = (堅い: A arare<=-1 / B ==0 / C >=1) / (混戦: D <=-1 / E ==0 / F >=1)

## 型ごとの構成（`PLANS`）

🔴 **順序の入れ替えを何点買うかは型で逆になる**（SUMMARY 追補 B）。
   鉄板(A)は着順まで読めるので1順序だけ、大混戦(F)は3車が当たっても順序が読めないので
   6順列すべてが最良（確認窓 ROI 66.8% → 79.2%）。
🔴 **配分はダッチ（∝1/予測オッズ）か信頼度傾斜**。均等は全型でガミ 12〜66%。
   信頼度傾斜の床 m は「当たったら最低 m 倍」の安全余裕で、**予測が中央 0.87 倍に下振れる
   （勝者の呪い）ので m=1.0 では実ガミが消えない**。既定は 1.3。

## 車数（2026-08-28 に 9車を追加）

**型判定は車数で分けない**（同じ型なら車数が違ってもそろい率が揃い、プールしても
A 67.16% → F 38.37% と単調。境界 1.44 も据え置き）。分かれるのは**売るもの**だけで、
それは `plans_for()` が車数と種別を見て決める。7車の挙動は変えていない。
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

BUDGET = 10_000
UNIT = 100

#: 軸の堅さの境界。7C/7M1 が共有する定数と同じ値を使う（別の値を持たせない）。
AXIS_SUM_FIRM = 1.44
#: 先頭の遅れ率の中央値（2025-01〜2026-08 の 7車 実測）。
BEHIND_MID = 11.0
#: 信頼度傾斜の既定の床（＝当たったら最低この倍率を予測ベースで確保する）。
DEFAULT_FLOOR_MULT = 1.3



# ───────────────────────────── 型判定 ─────────────────────────────

@dataclass(frozen=True)
class RaceShape:
    """1レースの型と、その根拠。"""
    type_label: str          # 'A'..'F'
    axis_sum: float
    arare: int
    gap: float
    firm: bool
    order: tuple[int, ...]   # 3着内率の降順（車番）


def _line_members(line_group: Mapping[int, object], car: int) -> list[int]:
    g = line_group.get(car)
    if g is None or str(g) in ("", "0"):
        return []
    return [c for c, v in line_group.items() if v == g]


def race_shape(top3_probs: Mapping[int, float], line_group: Mapping[int, object],
               line_pos: Mapping[int, object], style: Mapping[int, str],
               race_point: Mapping[int, float], behind_pct: Mapping[int, float],
               day_index: int) -> RaceShape | None:
    """レースの型を返す。3着内率が3車未満なら None。

    top3_probs: {車番: 3着内率 0-1}。**0-100 のパーセントを渡さないこと**
    behind_pct: {車番: ex_left_behind_pct（0-100 のパーセント）}
    """
    if not top3_probs or len(top3_probs) < 3:
        return None
    order = tuple(sorted(top3_probs, key=lambda c: (-float(top3_probs[c]), c)))
    axis_sum = float(top3_probs[order[0]]) + float(top3_probs[order[1]])
    others = order[2:]
    gap = 0.0
    if len(others) >= 5:
        gap = ((float(top3_probs[others[0]]) + float(top3_probs[others[1]])) / 2
               - sum(float(top3_probs[c]) for c in others[2:5]) / 3)

    mem = _line_members(line_group, order[0])
    lead = next((c for c in mem if str(line_pos.get(c)) == "1"), None)
    second = next((c for c in mem if str(line_pos.get(c)) == "2"), None)
    size = len(mem) if mem else 1

    s = 1 if size == 2 else (-1 if size >= 4 else 0)
    if lead is not None:
        s += -1 if float(behind_pct.get(lead, 0.0)) >= BEHIND_MID else 1
        s += 2 if str(style.get(lead, "")) == "追" else 0
    s += int(day_index or 0) - 2
    if (lead is not None and second is not None
            and float(race_point.get(second, 0.0)) > float(race_point.get(lead, 0.0))):
        s += 1

    firm = axis_sum >= AXIS_SUM_FIRM
    if firm:
        label = "A" if s <= -1 else ("B" if s == 0 else "C")
    else:
        label = "D" if s <= -1 else ("E" if s == 0 else "F")
    return RaceShape(label, axis_sum, s, gap, firm, order)


# ───────────────────────────── 買い目 ─────────────────────────────

@dataclass(frozen=True)
class Plan:
    """型ごとの買い方。`PLANS` の値。"""
    key: str                 # 'A_hit' など（テーブルの plan_key）
    type_label: str
    bet_type: str            # 'trifecta' | 'trio'
    structure: str           # 下記 build_legs の分岐
    n_partners: int          # 相手（3着列など）に使う車数
    min_odds: float = 0.0    # 予測オッズの下限（帯）
    max_legs: int = 0        # 上限点数（0=なし）
    sigma_max: float = 0.0   # Σ(1/予測オッズ) の上限（0=なし）
    alloc: str = "conf"      # 'conf' | 'dutch'
    floor_mult: float = DEFAULT_FLOOR_MULT
    note: str = ""


#: 🔴 **この辞書が商品の定義そのもの**。値を変えたら `rule_version()` が変わり、
#:    `type_lab_picks.rule_version` で新旧の行が自動的に分かれる。
PLANS: dict[str, Plan] = {
    # 型A 鉄板 — 着順まで読めるので1順序だけ。
    "A_hit": Plan("A_hit", "A", "trifecta", "fixed12", 3, alloc="conf",
                  note="1着=軸1・2着=軸2 固定で3着へ3車流し（表示的中を取る）"),
    "A_pay": Plan("A_pay", "A", "trifecta", "axis1_second2", 3, alloc="conf",
                  note="1着=軸1固定・2着を2車・3着流し（払戻を取る）"),
    # 型B 堅い・中 — 確率上位を Σ(1/予測) の床まで積む。
    "B_hit": Plan("B_hit", "B", "trifecta", "prob_top", 0, max_legs=8,
                  sigma_max=1 / 3.0, alloc="dutch",
                  note="確率上位から想定平均払戻 30,000円を割らない点数まで"),
    # 型C 堅いが崩れ筋 — 帯を切って点数を増やす側。
    "C_hit": Plan("C_hit", "C", "trifecta", "prob_top", 0, min_odds=20.0,
                  max_legs=12, alloc="dutch",
                  note="予測20倍以上から確率上位12点"),
    # 型D 混戦・軸あり — 唯一の三連複。最人気の相手を外す。
    "D_hit": Plan("D_hit", "D", "trio", "axis2_drop_fav", 4, alloc="dutch",
                  note="軸2車＋相手4点（相手5車のうち最人気1車を外す）"),
    # 型E 混戦・中 — 高い帯で点数を広げる。
    "E_hit": Plan("E_hit", "E", "trifecta", "prob_top", 0, min_odds=30.0,
                  max_legs=14, alloc="dutch",
                  note="予測30倍以上から確率上位14点"),
    # 型F 大混戦 — 3車は当たっても順序が読めないので6順列すべて。
    "F_hit": Plan("F_hit", "F", "trifecta", "all6", 2, alloc="conf",
                  note="軸2車＋相手2車の3車ぶん、6順列すべて（12点）"),
    "F_pay": Plan("F_pay", "F", "trifecta", "axis1_second2", 2, alloc="conf",
                  note="1着=軸1固定・2着を2車・3着流し（一撃を取る）"),
}


#: 9車で型F を売る条件。**決勝だけ・`F_hit` だけ**（2026-08-28 実投入）。
#:
#: 🔴 9車は**型F が母集団の 55〜59%** を占め、そこが弱い
#:    （型F 11.9件/日・表示的中 11.55%・ROI 65.1%）。全8プランのままだと
#:    ROI 69.8%/72.8% と**両窓とも壁の下**になる。型F を外すと 5.6件/日・
#:    表示的中 22.89%・ROI 83.0%/89.1%。
#: 🔴 打ち手（相手を増やす／荒れ度で切る／1着固定フォーメーション）は3通りとも壁を
#:    超えない。同じフォーメーションが**7車では ROI 77.0%** なので、
#:    形ではなく **9車の型F という母集団が薄い**。
#: 🟢 例外が決勝。**9車の決勝は 100% が型F** で、現行 `F_hit` のまま
#:    表示的中 14〜21%・払戻中央 3.5〜5.2万円と型F 全体より良い側にある
#:    （⚠️ 20か月で 66件・CI [24,174] なので**収支は判定できない**。
#:    「看板レースは必ず出す」方針と衝突しないから残す、という判断）。
#: 🔴 **「決勝」は完全一致で見ること。** `"決勝" in race_type` は準決勝も拾う
#:    （CLAUDE.md の既知の罠。9車の準決勝は確認窓 ROI 50.3% で反転する）。
#:
#: 実測: `keirin/docs/type_lab/carcount_2026_08_27.md`（2026-08-28 追記）
NINE_CAR_TYPE_F_RACE_TYPES = ("決勝",)
NINE_CAR_TYPE_F_PLANS = ("F_hit",)


def plans_for(type_label: str, n_entries: int = 7,
              race_type: str | None = None) -> list[Plan]:
    """その型で売る買い方。**車数と種別で売らないものを外す**。

    7車は `PLANS` をそのまま返す（実投入前と同じ挙動）。9車だけ、型F を
    `NINE_CAR_TYPE_F_*` の条件へ絞る。

    >>> [p.key for p in plans_for("F")]
    ['F_hit', 'F_pay']
    >>> [p.key for p in plans_for("F", 9, "決勝")]
    ['F_hit']
    >>> plans_for("F", 9, "準決勝")
    []
    >>> [p.key for p in plans_for("A", 9, "特選")]
    ['A_hit', 'A_pay']
    """
    plans = [p for p in PLANS.values() if p.type_label == type_label]
    if n_entries == 9 and type_label == "F":
        if str(race_type or "") not in NINE_CAR_TYPE_F_RACE_TYPES:
            return []
        return [p for p in plans if p.key in NINE_CAR_TYPE_F_PLANS]
    return plans


def build_legs(shape: RaceShape, plan: Plan,
               pred_odds: Mapping[tuple[int, ...] | frozenset, float],
               probs: Mapping[tuple[int, ...] | frozenset, float],
               ) -> list[tuple[int, ...]] | list[frozenset] | None:
    """買い目のリスト。組めなければ None。

    pred_odds / probs のキーは三連単なら (1着,2着,3着) のタプル、三連複なら frozenset。
    """
    order = list(shape.order)
    a1, a2 = order[0], order[1]
    rest = order[2:]

    if plan.bet_type == "trio":
        if plan.structure != "axis2_drop_fav":
            return None
        cs = [frozenset({a1, a2, c}) for c in rest]
        cs = [c for c in cs if _pos(pred_odds.get(c))]
        if len(cs) <= plan.n_partners:
            return None
        cs.sort(key=lambda c: float(pred_odds[c]))          # 予測オッズ昇順＝人気順
        cs = cs[1:]                                          # 最人気を1点外す
        cs.sort(key=lambda c: -float(probs.get(c, 0.0)))
        out = cs[:plan.n_partners]
        return out if len(out) == plan.n_partners else None

    # ── 三連単 ──
    if plan.structure == "fixed12":
        out = [(a1, a2, c) for c in rest[:plan.n_partners]]
    elif plan.structure == "axis1_second2":
        seconds = [a2, order[2]] if len(order) > 2 else [a2]
        pool = [c for c in rest if c not in seconds][:plan.n_partners]
        out = [(a1, s, c) for s in seconds for c in pool]
    elif plan.structure == "all6":
        pool = rest[:plan.n_partners]
        out = []
        for c in pool:
            trio = (a1, a2, c)
            out.extend(p for p in itertools.permutations(trio))
    elif plan.structure == "prob_top":
        cand = [k for k, v in pred_odds.items()
                if _pos(v) and float(v) >= plan.min_odds and len(set(k)) == 3]
        cand.sort(key=lambda k: -float(probs.get(k, 0.0)))
        out = []
        s = 0.0
        for k in cand:
            o = float(pred_odds[k])
            if plan.sigma_max and s + 1.0 / o > plan.sigma_max:
                continue
            out.append(tuple(k))
            s += 1.0 / o
            if plan.max_legs and len(out) >= plan.max_legs:
                break
        if plan.sigma_max and len(out) < 2:
            return None
    else:
        return None

    out = [tuple(c) for c in out if len(set(c)) == 3 and _pos(pred_odds.get(tuple(c)))]
    return out or None


def _pos(v) -> bool:
    try:
        return v is not None and float(v) > 0
    except (TypeError, ValueError):
        return False


# ───────────────────────────── 配分 ─────────────────────────────

def allocate(legs: Sequence, pred_odds: Mapping, probs: Mapping, plan: Plan,
             budget: int = BUDGET, unit: int = UNIT) -> dict | None:
    """買い目 -> 賭け金。組めなければ None。

    'dutch' … 賭け金 ∝ 1/予測オッズ（**払戻を全点で揃える**）
    'conf'  … 各点に floor = 予算×floor_mult ÷ 予測オッズ を置き、残りを**確率に比例**して配る
              （一番期待していない点は floor のまま＝最低 floor_mult 倍・自信のある点ほど厚い）
    """
    k = len(legs)
    n_units = budget // unit
    if k == 0 or k > n_units:
        return None
    o = [float(pred_odds[c]) for c in legs]
    if any(x <= 0 for x in o):
        return None

    if plan.alloc == "dutch":
        w = [1.0 / x for x in o]
        units = _proportional(w, n_units)
    else:
        floor = [max(int(math.ceil(budget * plan.floor_mult / x / unit)), 1) for x in o]
        if sum(floor) > n_units:
            return None
        rest = n_units - sum(floor)
        p = [max(float(probs.get(c, 0.0)), 0.0) for c in legs]
        add = _proportional(p, rest) if rest > 0 and sum(p) > 0 else [0] * k
        if rest > 0 and sum(p) <= 0:
            add = _proportional([1.0] * k, rest)
        units = [f + a for f, a in zip(floor, add)]
    return {c: u * unit for c, u in zip(legs, units)}


def _proportional(w: Sequence[float], n_units: int) -> list[int]:
    """重み w で n_units を配る（各要素0以上・合計ちょうど n_units）。"""
    k = len(w)
    if n_units <= 0:
        return [0] * k
    tot = sum(w)
    if tot <= 0:
        base = [n_units // k] * k
        for i in range(n_units - sum(base)):
            base[i] += 1
        return base
    units = [int(n_units * x / tot) for x in w]
    while sum(units) < n_units:
        i = max(range(k), key=lambda j: n_units * w[j] / tot - units[j])
        units[i] += 1
    return units


def mean_expected_payout(stakes: Mapping, pred_odds: Mapping) -> float:
    """入稿する買い目の想定払戻の**平均**（円）。表示と検証で同じ値を使う。"""
    if not stakes:
        return 0.0
    return sum(stakes[c] * float(pred_odds[c]) for c in stakes) / len(stakes)


def min_expected_payout(stakes: Mapping, pred_odds: Mapping) -> float:
    """買った点の**最低**想定払戻（円）。信頼度傾斜の床がここに出る。"""
    if not stakes:
        return 0.0
    return min(stakes[c] * float(pred_odds[c]) for c in stakes)


def rule_version(n_entries: int = 7) -> str:
    """`PLANS` と主要定数から導く版。値を変えると自動で別世代になる。

    🔴 **7車のハッシュは車数の規則を含めない。** 含めると 9車を足しただけで
       既存の 7車 53,017行と新しい行の版が割れ、「規則が変わった」と誤読される
       （7車の買い方は一切変えていない）。9車だけ `NINE_CAR_TYPE_F_*` を混ぜて
       別世代にする — `paper9` の行は**全8プランで作った**ので、決勝限定へ絞った
       今の規則とは実際に別物だから。
    """
    import hashlib
    import json
    payload: dict = (
        {k: [v.bet_type, v.structure, v.n_partners, v.min_odds, v.max_legs,
             round(v.sigma_max, 6), v.alloc, v.floor_mult] for k, v in sorted(PLANS.items())}
        | {"_axis": AXIS_SUM_FIRM, "_behind": BEHIND_MID, "_budget": BUDGET})
    if n_entries == 9:
        payload["_sell9"] = [list(NINE_CAR_TYPE_F_RACE_TYPES),
                             list(NINE_CAR_TYPE_F_PLANS)]
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]
