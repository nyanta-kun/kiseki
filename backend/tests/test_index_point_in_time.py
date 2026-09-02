"""指数が「そのレースより後」のデータを集計していないことを見張る。

## なぜ要るか

サブ指数は過去走から作るので、集計に日付上限（`Race.date < before_date`）が
無いと**未来のデータが混ざる**。これは例外にならない。バックフィルで作った
過去行の指数が少し良くなるだけで、学習データにも評価にもそのまま入る。

学習は `calculated_indices` の遡及生成値を読む（`train_jra_out_rate.FETCH_SQL`
→ `SUBINDEX_SOURCE_SQL`）ので、**モデルを学習し直しても汚染は消えない**。
直すには指数側に日付境界を入れて全期間バックフィルし直す必要がある。

## 既知の違反（2026-09-02 時点）

`pedigree.py` と `frame_bias.py` は他10指数と違い日付条件を持たない。
実測した汚染の大きさ:

- 2024-04〜06 のレースについて、種牡馬統計の **平均 31.3%（中央値 27.3%）が
  そのレースより後**のデータ
- ただし勝率そのものの動きは中程度: as-of と全期間で
  **平均 |差| 0.0069（水準 0.081 に対し相対 8.5%）・相関 0.927**

→ 実在するが、モデルのエッジ不足を説明するほどの規模ではない（推測）。
再バックフィル＋再学習が要るので、次の四半期ローリングで扱う。

## このテストの役割と、その限界

**違反リストは増やせない。** 新しい指数が日付境界なしで過去成績を集計したら
落ちる。既知の2件を直したらリストから消すこと（消し忘れても落ちる）。

🔴 **これはファイル単位の粗い検査で、クエリ単位ではない。**
ファイル内に日付境界が1つでもあれば通るので、**複数クエリのうち1本だけ
境界を落とした**場合は捕まえられない（2026-09-02 に実際に確認した）。
捕まえられるのは「そのモジュールに日付境界が1つも無い」場合だけ——
つまり `pedigree.py` / `frame_bias.py` と同じ型の、新規モジュールが
最初から作法を知らずに書かれるケースである。

検査は壊れ方を1つ固定するものであって、目視や設計レビューの代わりにはならない。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_INDICES = Path(__file__).resolve().parents[1] / "src" / "indices"

# 🔴 ここに足してはいけない。減らすだけ。
# 直したらこのリストからも消す（残したままだと「違反が無い」側で落ちる）。
_KNOWN_VIOLATIONS = {
    "pedigree.py",
    "frame_bias.py",
}

# 集計の起点になる「他レースの成績」への参照
_AGGREGATES_PAST = re.compile(r"\bkeiba\.race_results\b|\bRaceResult\b")

# 日付上限の書き方（ORM / 生SQL の両方）
_DATE_BOUND = re.compile(
    r"Race\.date\s*<|ra\.date\s*<|r\.date\s*<|\bbefore_date\b|\bdate\s*<\s*:"
)

# 当該レース自身を引くだけのモジュール（過去集計をしていない）は対象外
_NOT_AGGREGATORS = {"composite.py", "buy_signal.py", "base.py", "__init__.py"}


def _index_modules() -> list[Path]:
    return sorted(
        p for p in _INDICES.glob("*.py")
        if p.name not in _NOT_AGGREGATORS and not p.name.startswith("chihou_")
    )


@pytest.mark.parametrize("path", _index_modules(), ids=lambda p: p.name)
def test_past_aggregation_has_date_bound(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    # コメント・docstring を落としてから見る（説明文で言及するのは自由）
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    if not _AGGREGATES_PAST.search(code):
        pytest.skip("過去成績を集計していない")

    has_bound = bool(_DATE_BOUND.search(code))
    known = path.name in _KNOWN_VIOLATIONS

    if known:
        assert not has_bound, (
            f"{path.name} に日付境界が入った。直したなら "
            "_KNOWN_VIOLATIONS から消すこと（この一覧は増やさない）"
        )
        pytest.xfail(f"{path.name} は既知の point-in-time 違反（docstring 参照）")

    assert has_bound, (
        f"{path.name} が日付上限なしで過去成績を集計している。"
        "`Race.date < before_date` 相当を入れること。"
        "未来のデータが混ざっても例外は出ず、バックフィルした過去行の指数が"
        "静かに良くなるだけなので気づけない"
    )
