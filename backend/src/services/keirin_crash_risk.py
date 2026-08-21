"""レース信頼度指標（落車リスク）の算出（2026-08-21 新設）。

## なぜこの指標か

落車は「軸が飛ぶ＝即外れ」に直結する唯一の事象だが、**個人単位では予測が難しい**
（`dnf_rate_180` 単体の落車予測 AUC 0.5459＝ほぼコイン投げ）。一方で 2026-08-21 の
実測では、**縮約した性向を1レース分そろえると意味のある差が出る**:

    そのレースの出走者の性向の平均 Q1(安全) → Q4(危険) で
      軸2車のどちらかが落車する率  1.56% → 3.11%（**約2倍**）
      出走者本人の落車率           0.97% → 1.48%（自分の性向で層別しても単調）

⚠️ **個人の落車性向は弱いが実在する**（split-half 相関 r=+0.21〜0.25）。
   2026-08-04 の記録にある「落車しやすい選手という安定した性質は存在しない」は、
   基準率 1.2% の稀事象を AUC で測ったことによる言い過ぎ。

🔴 **ただし「危険なレースを外す」ゲートには使えない**（2026-08-21 検証・不採用）。
   7C ゲート通過 6,425R を四分位で割ると:

   | | 二軸的中 | 軸落車 | ROI |
   |---|---|---|---|
   | Q1(安全) | 65.8% | 1.56% | 71.6% |
   | Q4(危険) | 64.0% | 3.11% | **78.1%** |

   軸落車は指標どおり増えるのに **二軸的中はほぼ動かず**（確認窓では Q2 が最高で
   単調性も消える）、**ROI はむしろ危険側が高い**（配当が付くため）。
   危険帯を除外すると「最も回収率の高い四分位を捨てる」ことになる。

   → **判断材料として出す**（承認画面の表示）。自動で落とさない。

## 定義

各選手の性向を **point-in-time**（そのレースより前の出走のみ）で作り、稀事象なので
経験ベイズで全体平均へ縮約する:

    risk_i = (過去の落車数 + K * p0) / (過去の出走数 + K)
    レース指標 = mean(risk_i)

K は「走数が K のとき全体平均と半々」になる強さ。K=200 は実測（sd 0.457pt）で
選手間の差を潰さず、かつ数走の選手が極端な値を持たない点として選んだ。

⚠️ **標準ライブラリ以外を import しないこと。** keirin 側は自分の venv から
   このファイルを直接読み込む可能性がある（`keirin_marquee.py` と同じ運用）。
"""

from __future__ import annotations

#: 経験ベイズの縮約の強さ（走数がこれと同じとき 全体平均と半々になる）
SHRINK_K = 200.0

#: 全体の落車率（7車・2024-01〜 実測 1.19%）。履歴が無い選手はここへ倒れる。
BASE_RATE = 0.0119

#: 表示の区分（レース指標の実測四分位・2026-01〜 7C ゲート通過 6,425R）。
#: Q1 0.97% 未満 / Q4 1.24% 以上 あたりが境目。
BAND_LOW = 0.0100
BAND_HIGH = 0.0130


def rider_risk(prior_starts: int, prior_dnf: int,
               k: float = SHRINK_K, base: float = BASE_RATE) -> float:
    """1選手の落車性向（そのレースより前の実績のみで作ること）。

    prior_starts / prior_dnf は**当該レースを含めない**。含めると結果を見て
    予想することになる（このリポジトリが繰り返し踏んでいる型）。
    """
    if prior_starts < 0 or prior_dnf < 0:
        raise ValueError("負の実績は渡せません")
    return (prior_dnf + k * base) / (prior_starts + k)


def race_risk(riders: list[tuple[int, int]],
              k: float = SHRINK_K, base: float = BASE_RATE) -> float | None:
    """レースの信頼度指標 = 出走者の性向の平均。

    riders: [(prior_starts, prior_dnf), ...] 出走者ぶん。空なら None。

    ⚠️ **出走者全員を渡すこと。** 軸2車だけで測ると「自分が落車するか」に
       なってしまい、**巻き込まれ**（落車の 66.7% は2人以上出たレースで発生）を
       取りこぼす。
    """
    if not riders:
        return None
    return sum(rider_risk(s, d, k, base) for s, d in riders) / len(riders)


def risk_band(risk: float | None) -> str:
    """表示用の区分。`low`（安全）/ `mid` / `high`（危険）/ `unknown`。"""
    if risk is None:
        return "unknown"
    if risk < BAND_LOW:
        return "low"
    if risk >= BAND_HIGH:
        return "high"
    return "mid"
