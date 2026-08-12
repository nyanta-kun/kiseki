"""複勝バックテストの母集団ガード（DB にも FastAPI にも依存しない純関数）。

## なぜ必要か

`chihou.race_results.place_odds` の欠損は **ランダムではなく着順と相関している**。
HR 払戻（`chihou.race_payouts`）由来の補完は **1〜3着馬にしか値を与えない**ためで、
着外馬まで埋まるのは `chihou.odds_history`（2026-04-07 以降）がある期間だけ。

実測（ばんえい除く・完走馬）:

| 期間 | 1-3着の充足 | 4着以下の充足 |
|---|---|---|
| 2024-01 〜 2025-12 | 0.4% | 0.0% |
| 2026-01 〜 2026-03 | **98.3%** | **0.0%** |
| 2026-04 〜         | 99.8%  | 93.3% |

バックテスト側は `df[df["place_odds"].notna()]` で NULL 行を落としてから
`ROI = Σ(的中馬の複勝オッズ) / len(valid)` を計算していた。
2026-01〜03 ではこの絞り込みが **「4着以下を全部捨てて1〜3着だけ残す」** 操作と
等価になり、母集団の的中率が定義上ほぼ 100%、ROI は複勝オッズの平均値
（2〜5倍）に化ける。

⚠️ **払戻を過去へバックフィルすると、この壊れ方が 2024〜2025 にも広がる**
（HR は 1〜3着しか持たないので同じ形の欠損が作られる）。
バックフィルの前に本ガードを通すこと。

## 何をするか

**レース単位で「全出走馬の複勝オッズが揃っているか」を見て、揃っていないレースを
母集団ごと落とす。** 期間で切るのではなく充足そのものを見るので、
データが増えても自動的に正しく振る舞う。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# レース内で place_odds が埋まっている割合がこれ未満なら、そのレースを母集団から外す。
# 1.0 にしないのは、正常な期間でも取消馬などで数頭欠けることがあるため。
MIN_RACE_PLACE_ODDS_COVERAGE = 0.9

# 1-3着 と 4着以下 の充足率の差がこれを超えたら「着順と相関した欠損」とみなす。
MAX_FINISH_POSITION_SKEW = 0.25


@dataclass(frozen=True)
class PlaceOddsAudit:
    """複勝オッズ充足の監査結果。"""

    n_rows_before: int
    n_rows_after: int
    n_races_before: int
    n_races_after: int
    top3_fill_before: float
    rest_fill_before: float
    skew_before: float

    @property
    def is_skewed_before(self) -> bool:
        """絞り込み前の母集団に着順相関した欠損があったか。"""
        return self.skew_before > MAX_FINISH_POSITION_SKEW

    def format(self) -> str:
        lines = [
            f"  複勝オッズ充足: 1-3着 {self.top3_fill_before*100:5.1f}% / "
            f"4着以下 {self.rest_fill_before*100:5.1f}%  (差 {self.skew_before*100:5.1f}pt)",
            f"  母集団: {self.n_races_before:,}R {self.n_rows_before:,}行 → "
            f"{self.n_races_after:,}R {self.n_rows_after:,}行",
        ]
        if self.is_skewed_before:
            lines.append(
                "  ⚠️ 欠損が着順と相関している（HR払戻由来で1〜3着しか埋まっていない期間を含む）。"
                "\n     ガードが無ければ的中率もROIも大幅に上振れしていた。"
            )
        return "\n".join(lines)


def _fill_rates(df: pd.DataFrame) -> tuple[float, float]:
    """(1-3着の充足率, 4着以下の充足率) を返す。"""
    fp = pd.to_numeric(df["finish_position"], errors="coerce")
    top3 = df[fp.between(1, 3, inclusive="both")]
    rest = df[fp > 3]
    top3_fill = float(top3["place_odds"].notna().mean()) if len(top3) else 0.0
    rest_fill = float(rest["place_odds"].notna().mean()) if len(rest) else 0.0
    return top3_fill, rest_fill


def filter_races_with_full_place_odds(
    df: pd.DataFrame,
    *,
    race_id_col: str = "race_id",
    min_coverage: float = MIN_RACE_PLACE_ODDS_COVERAGE,
) -> tuple[pd.DataFrame, PlaceOddsAudit]:
    """複勝オッズが全出走馬ぶん揃っているレースだけに絞り込む。

    Args:
        df: 少なくとも race_id_col / finish_position / place_odds を持つ DataFrame。
            **1レースの全出走馬**が入っていること（絞り込み済みの候補馬だけを
            渡すと充足率が正しく測れない）。
        race_id_col: レース識別列名。
        min_coverage: レース内の place_odds 充足率の下限。

    Returns:
        (絞り込み後の DataFrame, 監査結果)
    """
    if df.empty:
        empty_audit = PlaceOddsAudit(0, 0, 0, 0, 0.0, 0.0, 0.0)
        return df, empty_audit

    top3_fill, rest_fill = _fill_rates(df)
    coverage = df.groupby(race_id_col)["place_odds"].apply(lambda s: s.notna().mean())
    keep_races = coverage[coverage >= min_coverage].index
    filtered = df[df[race_id_col].isin(keep_races)]

    audit = PlaceOddsAudit(
        n_rows_before=len(df),
        n_rows_after=len(filtered),
        n_races_before=int(df[race_id_col].nunique()),
        n_races_after=int(filtered[race_id_col].nunique()),
        top3_fill_before=top3_fill,
        rest_fill_before=rest_fill,
        skew_before=abs(top3_fill - rest_fill),
    )
    return filtered, audit
