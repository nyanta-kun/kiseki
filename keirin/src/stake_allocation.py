"""買い目ごとの賭け金配分（netkeirin 入稿用・2026-08-07 新設）。

## なぜ均等割りをやめるのか

netkeirin の的中率は **ガミ（払戻 < 投資）を不的中として扱う**。1レース10,000円を
点数で均等割りすると「ガミにならない境界＝的中オッズが点数を超えること」になり、
5点買いなら 5.0倍未満の的中はすべて不的中表示になる。実測（2026-06-08〜08-06・
7車1,061レース）で **的中 52.0% に対し表示されるのは 25.1%＝的中の 51.8% がガミ**。

そこで**想定着地オッズに応じて配分**する。三連複・軸2車固定なら買う点の差は
3列目の車だけなので、点ごとの相対的な当たりやすさが分かれば全点の払戻をそろえられる。

    実質的中率 25.07% → 34.97%（+9.90pt / 95%CI [+7.63, +12.35] / P(差>0)=100%）
    ROI 81.8% → 79.6%（−2.27pt / CI [−8.08, +2.94] ＝ 有意でない）

## 想定着地オッズの作り方

朝8:00 の板は薄く、最終オッズとの中央値の比は 0.85（15%下振れ）で個別誤差も大きい。
一方で**一律の割引は比率を変えないので無意味**（配分に効くのは相対値だけ）。
使えるのは次の2つで、実測ではどちらも単独で同程度に効く:

| 重み | 実質的中% | 相対確率のL1誤差 |
|---|---|---|
| 均等 | 25.07 | 0.601 |
| モデルの3着内率 p3 のみ | 33.55 | 0.376 |
| 1/朝オッズ のみ | 34.78 | 0.339 |
| **相乗平均（λ=0.5）** | **35.25** | **0.330** |
| 最終オッズ（実装不能・上限） | 48.54 | 0 |

⚠️ **相乗平均が単独より有意に良いわけではない**（blend−morning は +0.19pt
[−0.94, +1.23]・P=60.4%）。両方を使うのは**どちらかが欠けても劣化しないため**で、
実際 朝オッズが買う点すべてに揃うのは **65%** しかない（残りは p3 単独で走る）。

⚠️ **レース単位のゲート（期待値の下限・保証下限）は入れない**。測ったが全部不採用。
   買う点の合成ブックは約0.65なので想定上の「ガミ無し保証」は96.4%のレースで成立し、
   それでも実ガミ率は32.8%。ガミの原因はレース水準ではなく**点ごとの相対誤差**で、
   下限はそれを見ていない。詳細は memory `keirin_netkeirin_gami_allocation_2026_08_07`。
"""
from __future__ import annotations

# 入稿の下限（2026-08-19・ユーザー判断）。**7C の三連複だけに掛ける。**
#
#   想定払戻(下限) = min_i (賭け金_i × 入稿時点の板オッズ_i) ÷ 予算
#
# 傾斜配分は払戻をそろえる向きに効くので、この最小値は「当たったとき最悪でも
# 何倍返るか」。1.0 を下回るレースは**どの目が来ても賭け金を割りうる**。
#
# 【なぜ入れるか】ユーザー方針「日中の硬いレースでも母数があれば売上がつき
# 的中率の積み上げにも効く、と考えていたが、的中率も圧倒的でなく売れていない。
# このレンジは入稿対象から除外する。**的中精度は同程度を確実に**」。
#
# 実測（`scripts/exp_7c_expected_payout_band.py`・7C三連複 505R・44日・
# 2026-06-08〜08-18＝朝の板がある全期間・確定オッズ採点）:
#
#   | 想定払戻(下限) | R | 素の的中 | 表示的中 | ROI | 無売上 | 購入/R |
#   |---|---|---|---|---|---|---|
#   | **〜1.0** | 68 | 75.0% | **27.9%** | 83.7% | **64.3%** | 0.50 |
#   | 1.0〜1.3 | 98 | 74.5% | **41.8%** | 91.5% | 58.3% | 0.67 |
#   | 1.3〜1.6 | 79 | 60.8% | 31.6% | 77.3% | 65.0% | 0.60 |
#   | 1.6〜2.0 | 68 | 57.4% | 35.3% | 82.6% | 33.3% | 1.50 |
#   | 2.0〜3.0 | 95 | 50.5% | 32.6% | 74.3% | 30.4% | 2.09 |
#   | 3.0以上 | 97 | 50.5% | 27.8% | 79.8% | 52.0% | 1.04 |
#
# **1.0未満だけが「売れない(無売上64.3%)」と「当たっても返らない
# (表示的中27.9% < 全体33.1%)」を両立**している。除外したときの残る側:
#
#   除外なし  素の的中 61.0% / 表示的中 33.1% / ROI 81.5%
#   **>= 1.0  除外13.5%  素の的中 58.8% / 表示的中 33.9% / ROI 81.2%**
#   >= 1.2   除外25.5%  素の的中 55.9% / 表示的中 **32.2%** / ROI 79.3%
#   >= 1.5   除外44.6%  素の的中 52.5% / 表示的中 **31.4%** / ROI 77.1%
#
# 🔴 **1.2 以上へ広げてはいけない。** 1.0〜1.3 の帯は表示的中 41.8% と全帯で最高で、
#    そこを落とすと「的中精度は同程度」というユーザー条件を破る。
#    売上（無売上率・購入/R）だけを見ると 1.5 まで切りたくなるが、**売上データは
#    2026-08-01 以降の帯あたり 12〜25件しか無い**。売上で閾値を切らないこと。
#
# ⚠️ 効果は小さい（表示的中 +0.8pt・n=505・44日）。朝の板は 2026-06-08 以降しか
#    無いのでこれ以上窓を広げられない。**しばらく様子見**の位置づけ（2026-08-19）。
# 【✂️ 7S へも入れる案は 2026-08-19 に測って不採用 — 再提案しない】
#   `scripts/exp_expected_payout_band_by_rank.py`（2026-06-08〜08-18・朝の板あり）:
#
#     RANK_7S（135R）想定払戻(下限)別
#       〜1.0      12R(8.9%)  素の的中 75.0% / 表示的中 **50.0%** / ROI **99.6%**
#       1.0〜1.3    8R        25.0% / 12.5% / 41.9%
#       1.3〜1.6   22R        27.3% / 27.3% / 37.8%
#       1.6〜2.0   42R        47.6% / 42.9% / 89.9%
#       2.0〜3.0   41R        26.8% / 26.8% / 49.7%
#       3.0以上    10R        50.0% / 50.0% / 120.6%
#       全体      135R        39.3% / 34.8% / 69.5%
#
#   🔴 **7C と逆向き。** 7S では 1.0未満帯が**全帯で最良**で、除外すると
#      表示的中 34.8→33.3%（−1.5pt）・ROI 69.5→66.6%（−2.9pt）と悪化する。
#      7S は `axis_sum <= 1.40` で軸2車の信頼が低い側（波乱寄り）を取るランクなので、
#      想定払戻が1.0を割るのは「板が薄くて安く見えている」ケースが多く、
#      7C の「本当に堅くて配当が付かない」レースとは意味が違う。
#      実際その帯は倍率中央 1.05 で当たっており、**予測が悲観的すぎた側**。
#   ⚠️ n=12 と小さい。3ヶ月ほど板が貯まったら再確認する価値はある。
#      ただし 7S で該当するのは 8.9% しかなく、入れても効果は小さい。
#
# ⚠️ **9車へ持ち込まないこと。** 測ったのは7車の三連複だけ。
#    このリポジトリは「7C の定数を9車へ移植できない」型の事故を起こしている。
#
# ═══════════════════════════════════════════════════════════════════════════
# 【2026-08-21 改定】1.0 → 1.5・7S にも適用（ユーザー方針）
# ═══════════════════════════════════════════════════════════════════════════
#
# ユーザー方針: **最低限の希望オッズは 1.5 倍**（予測オッズのブレを織り込んだ設定）。
# 的中率そのもの（ガミ込み）には意味が無く、件数は減ってよい。
#
# 🔴 **上の「7S では悪化するので持ち込むな」は撤回する。根拠が n=12 だった。**
#    あの測定は `_load_trio_board`（＝入稿時点の実オッズ板）で floor を出していたが、
#    **買う点が全部揃う板は実測 8.9% しかない**（[[keirin_odds_availability_by_posttime_2026_08_07]]）。
#    残りは `expected_payout_floor` が None を返して素通しするので、7S で判定できたのが
#    12件しか無かった。**ゲートがほぼ発火していなかった**のであって、
#    「7S では効かない」ことを示した測定ではない。
#
# 🟢 予測オッズ（`src.odds_prediction`）で測り直すと全レースで判定でき、
#    n が2桁増える。honest モデル（train_end 2025-12-31）・2026-01〜08-19 の実測:
#
#      ランク  足切りなし          1.4倍               1.6倍
#      7C     14.8%(8.26R/日)   17.9%(6.09R/日)     18.6%(5.31R/日)
#      7S     15.1%(5.37R/日)   17.2%(3.64R/日)     19.0%(2.36R/日)
#        ※ 数字は「払戻 >= 2×賭け金 で的中した率」。ROI は 7C 75.8→73.7 /
#          7S 80.8→82.2 で、**ROI は改善しない**（体験と件数の交換）。
#
#    残す側 vs 外す側の分離は 7C 掃引窓 +11.5pt(t=+6.3) / 確認窓 +8.3pt(t=+3.0)、
#    7S 掃引窓 +6.7pt(t=+2.4) / 確認窓 +6.6pt(t=+1.9)。**両窓で符号一致**。
#    詳細: [[keirin_n7_gami_cut_predicted_odds_2026_08_21]]
#
# ⚠️ **判定は必ず「配分に使ったのと同じ板」で行うこと。** 配分が予測オッズなら
#    判定も予測オッズ。別の板で測ると配分の想定と判定が食い違う。
#
# ⚠️ 看板レースは別経路（`submit_marquee_wt.py --marquee`）が**ゲートを通さずに**
#    埋めるので、この足切りで看板の推奨が消えることはない。
MIN_EXPECTED_PAYOUT_7C = 1.5
MIN_EXPECTED_PAYOUT_7S = 1.5

#: 想定払戻(下限)による足切りを適用するランクと閾値。
#: 🔴 ここに無いランクは足切りしない（未測定のランクへ黙って広げない）。
MIN_EXPECTED_PAYOUT_BY_RANK: dict[str, float] = {
    "7C": MIN_EXPECTED_PAYOUT_7C,
    "7S": MIN_EXPECTED_PAYOUT_7S,
}


def expected_payout_floor(
    stakes: dict[int, int], odds: dict[int, float], budget: int,
) -> float | None:
    """買った目の**最低**想定払戻倍率。判定不能なら None。

    stakes: {相手車番: 賭け金} / odds: {相手車番: その目の板オッズ}

    🔴 **1つでもオッズが欠けたら None を返す**（＝入稿する側へ倒す）。
       欠けた目が最低倍率だった可能性があるので、残りだけで最小値を取ると
       **実際より高く見積もって素通しする**。分からないことを理由に商品を
       落とさない方針なので、素通し自体は正しいが、**黙って過大評価しない**。
    """
    if not stakes or budget <= 0:
        return None
    vals = []
    for car, stake in stakes.items():
        o = odds.get(car)
        if not o or o <= 0 or not stake:
            return None
        vals.append(o * stake / budget)
    return min(vals) if vals else None


# ═══════════════════════════════════════════════════════════════════════════
# 1点でも安すぎる目があるレースは出さない（2026-08-22・ユーザー判断）
# ═══════════════════════════════════════════════════════════════════════════
# ユーザー方針: 「**買い目の1点でも予想オッズが 2.0 倍を切っているレースは
# 推奨から外す**」。理由は「掛金の半分を入れて元返しにしかならない目を売らない」。
#
# 🔴 **これは収支の改善策ではない。** 8/16〜8/21 の実売 228件を予測オッズで
#    引き直した実測（`scripts/exp_ev_title_and_low_odds_cut.py`）:
#
#      全件            228件 的中 35.1% ガミなし69 2倍+24 ROI 64.5%
#      残す(>=2.0倍)   219件 的中 33.8% ガミなし67 2倍+24 ROI 64.2%
#      落とす(<2.0倍)    9件 的中 66.7% ガミなし 2 2倍+ 0 ROI 71.0%
#
#    落ちる側は**当たりやすい側**（的中 66.7%）で、外すと的中率も ROI も下がる。
#    傾斜配分で一番厚く置く点＝一番来やすい点なので、その倍率が低いのは
#    「堅いレース」の印だから。
#
# 🟢 それでも採る理由: **落ちる9件は「2倍以上での的中」が1件も無い**。
#    KPI（[[keirin_7c7s_hit_experience_2026_08_20]]）で測ると損失ゼロで、
#    消えるのは「当たっても増えない的中」だけ。**説明性で採る規則**であって
#    「ROI が上がるから」と説明してはいけない。
#
# ⚠️ **効果は測れていない**（該当 9件・うち通常経路は 3件）。n が小さすぎる。
# ⚠️ `expected_payout_floor`（想定払戻の下限）とは**別の量**。あちらは
#    「賭け金 × オッズ ÷ 予算」で、7C/7S は既に 1.5 倍で足切りしている。
#    こちらは**生のオッズ**なので、5点買いなら 2.0 倍の点でも払戻は
#    2,000円 × 2.0 = 4,000円（＝下限 0.4倍）。両者は入れ替えられない。
#: 買い目1点あたりの予測オッズの下限。これ未満の点が1つでもあれば出さない。
MIN_POINT_ODDS = 2.0


def cheap_point_odds(odds: dict[int, float],
                     minimum: float = MIN_POINT_ODDS) -> float | None:
    """`minimum` 未満の目があればその**最低倍率**を返す（無ければ None）。

    odds: {相手車番: その目の予測オッズ}

    🔴 **1つでもオッズが欠けたら None**（＝入稿する側へ倒す）。
       `expected_payout_floor` と同じ思想で、欠けた目が最安だった可能性がある。
       分からないことを理由に商品を落とさない。

    >>> cheap_point_odds({1: 3.0, 2: 1.8})
    1.8
    >>> cheap_point_odds({1: 3.0, 2: 2.0}) is None
    True
    >>> cheap_point_odds({1: 3.0, 2: None}) is None
    True
    """
    if not odds:
        return None
    vals = []
    for o in odds.values():
        if not o or o <= 0:
            return None
        vals.append(float(o))
    lo = min(vals)
    return lo if lo < minimum else None


BUDGET_DEFAULT = 10_000
UNIT_DEFAULT = 100

# 朝オッズ側の指数。0=モデルのみ / 1=朝オッズのみ。
# 両窓（6月・7-8月）で最も安定していた 0.5（相乗平均）を採る。
# 差は有意でないので**単一レースや単月の結果でこの値を動かさないこと**。
LANDING_LAMBDA = 0.5

# 重みの出どころ。ログと dry-run 表示に使う（どの経路で走ったか分からないと
# 「朝オッズが取れていない」ことに気づけない）。
SOURCE_PREDICTED = "predicted"   # 構造モデルの予測オッズ（2026-08-11〜・最優先）
SOURCE_BLEND = "blend"
SOURCE_ODDS = "odds"
SOURCE_MODEL = "model"
SOURCE_EQUAL = "equal"


def _usable_odds(legs: list[int], odds: dict[int, float] | None) -> bool:
    """買う点**すべて**にオッズがあるときだけ使う。

    一部だけ使うと、欠けた点の重みを別の尺度で決めることになり比率が壊れる。
    """
    if not odds:
        return False
    return all(isinstance(odds.get(t), (int, float)) and odds.get(t, 0) > 0 for t in legs)


def _usable_probs(legs: list[int], probs: dict[int, float] | None) -> bool:
    if not probs:
        return False
    return all(isinstance(probs.get(t), (int, float)) and probs.get(t, 0) > 0 for t in legs)


def landing_weights(
    legs: list[int],
    morning_odds: dict[int, float] | None,
    top3_probs: dict[int, float] | None,
    lam: float = LANDING_LAMBDA,
    predicted_odds: dict[int, float] | None = None,
) -> tuple[dict[int, float], str]:
    """3列目の車ごとの重み（賭け金はこれに比例させる）と、その出どころを返す。

    legs:         買う相手（3列目）の車番
    morning_odds: {3列目の車番: その点の朝の三連複オッズ}。欠けていてよい
    top3_probs:   {車番: モデルの3着内率 0-1}。欠けていてよい

    predicted_odds: {3列目の車番: 構造モデルが予測した最終三連複オッズ}。
                  `src.odds_prediction` が出す。**あれば最優先で単独採用する**。

    重みは「その点の当たりやすさ」に比例する。賭け金をこれに比例させると
    全点の払戻がそろい、どの点で決まっても元返し以上になりやすくなる。

    🔴 **予測オッズは p3 と blend しない。** 予測オッズは重要度の約7割が p3 由来
       （`lp_pl` 44% + `lp_prod` 26%）なので、相乗平均を取ると p3 を二重計上して
       薄まる。honest 検証（7Cゲート内 4,670R・掃引/確認）の実質的中率:

           現行 blend(朝, p3)   30.49 / 30.64
           blend(予測, p3)      34.26 / 34.22
           **予測オッズ単独      39.64 / 37.99**

       朝の板より優先するのは、朝の板が買う点すべてに揃うのが **8.9%** しかなく、
       揃っても最終との ±2倍以内が 59.3%（予測オッズは 91.5%）だから。
       詳細は `src/odds_prediction.py` の冒頭。
    """
    if not legs:
        raise ValueError("legs が空です")
    if _usable_odds(legs, predicted_odds):
        return {t: 1.0 / predicted_odds[t] for t in legs}, SOURCE_PREDICTED
    has_o, has_p = _usable_odds(legs, morning_odds), _usable_probs(legs, top3_probs)
    if has_o and has_p:
        return ({t: (1.0 / morning_odds[t]) ** lam * top3_probs[t] ** (1.0 - lam)
                 for t in legs}, SOURCE_BLEND)
    if has_o:
        return {t: 1.0 / morning_odds[t] for t in legs}, SOURCE_ODDS
    if has_p:
        return {t: float(top3_probs[t]) for t in legs}, SOURCE_MODEL
    return {t: 1.0 for t in legs}, SOURCE_EQUAL


def allocate_budget(
    weights: dict[int, float],
    budget: int = BUDGET_DEFAULT,
    unit: int = UNIT_DEFAULT,
) -> dict[int, int]:
    """重み → 1点あたりの賭け金（unit の倍数・合計は必ず budget）。

    ⚠️ **どの点も必ず1単位以上を持つ**。比例配分だけだと極端に人気薄の点が
       0円になり、**買い目の集合が黙って変わってしまう**（点数が減る）。
       買う目を決めるのは配分の役目ではない。

    ⚠️ 端数の寄せ先は「想定払戻が最小の点」＝ **口数 / 重み が最小の点**。
       重みが 1/想定オッズ に比例するので、これはオッズを参照せずに決まる。
       最終オッズで決めると先読みになる（検証スクリプトで実際にやらかし、
       効果を 2pt 過大評価していた）。
    """
    if not weights:
        raise ValueError("weights が空です")
    n_units = budget // unit
    if n_units < len(weights):
        raise ValueError(f"予算 {budget} 円では {len(weights)} 点に配分できません")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("重みの合計が0以下です")

    # 全点に1口ずつ配ってから、残りを比例配分する
    units = {k: 1 for k in weights}
    rest = n_units - len(weights)
    share = {k: rest * w / total for k, w in weights.items()}
    for k, s in share.items():
        units[k] += int(s)
    for _ in range(n_units - sum(units.values())):
        units[min(units, key=lambda k: units[k] / max(weights[k], 1e-12))] += 1
    return {k: v * unit for k, v in units.items()}


def tilted_stakes(
    legs: list[int],
    morning_odds: dict[int, float] | None,
    top3_probs: dict[int, float] | None,
    budget: int = BUDGET_DEFAULT,
    unit: int = UNIT_DEFAULT,
    lam: float = LANDING_LAMBDA,
    predicted_odds: dict[int, float] | None = None,
) -> tuple[dict[int, int], str]:
    """買う相手ごとの賭け金と、重みの出どころを返す。"""
    weights, source = landing_weights(legs, morning_odds, top3_probs, lam,
                                      predicted_odds=predicted_odds)
    return allocate_budget(weights, budget, unit), source


def group_by_stake(stakes: dict[int, int]) -> list[tuple[int, list[int]]]:
    """同額の相手をまとめる。[(賭け金, [車番,…]), …] を賭け金の降順で返す。

    netkeirin の `kaime` は1行につき1つの `bet_money` しか持てないので、
    同額の相手は1行にまとめられる（行数が減って買い目が読みやすくなる）。
    """
    by_stake: dict[int, list[int]] = {}
    for car, stake in stakes.items():
        by_stake.setdefault(stake, []).append(car)
    return [(s, sorted(cars)) for s, cars in
            sorted(by_stake.items(), key=lambda kv: -kv[0])]
