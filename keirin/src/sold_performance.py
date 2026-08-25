"""**実際に売った商品**の成績集計（2026-08-15 新設）。

## なぜ picks_history と別に要るのか

picks_history は**ペーパー成績**（各ランクが条件を満たした全レース）で、
netkeirin で実際に売れるのは **1レース1商品**だけ。両者は母集団が違う:

  - 売っていないのに picks_history にはある（他ランクに商品を譲ったレース）
  - 売ったのに picks_history には無い（**看板の穴埋め** 233件・全入稿の49%）

そのため picks_history をいくら足しても「いくら売って、いくら返ってきたか」は出ない。
`/stats` の「全入稿」トグル（`_manual_submission_buckets`）は
**picks_history + 穴埋め**の混成で、これはこれで
「1レースに1商品」を守るために *売った 7A の穴埋めではなく、売っていない 7T1 の
ペーパー行を計上する*ことがある（8月の穴埋め172件中83件がこの経路で落ちる）。

ここは情報源を **`netkeirin_submissions` + `bet_detail` だけ**に固定する。
1レース1商品なので二重計上が構造的に起こらない。

## 🔴 「的中」は2種類ある

netkeirin の**表示的中率は `n_hits_excl_garami`**（払戻＞賭け金）で、ガミ
（当たったのに損）を不的中として数える。素の的中率だけを見ると、
点数を増やしたときに「改善した」と誤読する（2026-08-15 に実際にやった）。
両方返すこと。

## ⚠️ 使えない期間がある

`bet_detail` の保存は **2026-08-07 開始**。それ以前の入稿は「売った事実」しか
残っておらず買い目も金額も復元できない。0円として足すと投資額を過小に見せるので
**集計から外し、件数だけ返す**（黙って落とすと完全な数字に見える）。

DB にも FastAPI にも依存しない純関数（`keirin_sales_analysis.py` と同じ方針）。

## 🔴 採点そのものは kiseki 側が正本（2026-08-25 一本化）

    backend/src/services/keirin_settlement.py

同じ商品の結果が Discord・入稿確認・推奨一覧で食い違っていたため、採点を1本に
まとめた。**ここに採点規則を書き直してはいけない**（書いた瞬間、Discord だけが
別の答えを出す状態へ戻る）。看板判定（`marquee.py`）と同じく、正本を
ファイル指定で読み込んで束縛する。
`tests/test_sold_performance.py::test_採点は正本へ委譲している` が機械的に見ている。
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence

# keirin/src/sold_performance.py → parents[2] が kiseki のリポジトリルート。
_CANONICAL = (Path(__file__).resolve().parents[2]
              / "backend" / "src" / "services" / "keirin_settlement.py")
_MODULE_NAME = "kiseki_keirin_settlement"


def _load_canonical() -> ModuleType:
    """kiseki 側の採点の正本をファイルから読み込む。

    ⚠️ `sys.path` に `backend/` を足す方式は使えない。keirin にも `src`
       パッケージがあり**名前が衝突する**ため。正本は標準ライブラリ以外を
       import しないので、ファイル指定の読み込みで安全に共有できる。
    ⚠️ 見つからないときは**黙って自前実装へ落ちない**。フォールバックは
       二重管理を静かに復活させ、ずれても誰も気づけない。
    """
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    if not _CANONICAL.exists():
        raise ImportError(
            f"採点の正本が見つかりません: {_CANONICAL}\n"
            "keirin は kiseki リポジトリ内（<kiseki>/keirin）で動かす前提です。"
        )
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _CANONICAL)
    if spec is None or spec.loader is None:        # pragma: no cover - 実質起きない
        raise ImportError(f"正本を読み込めません: {_CANONICAL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_canonical = _load_canonical()

Settlement = _canonical.Settlement
settle = _canonical.settle
payout_per_100 = _canonical.payout_per_100
_as_dict = _canonical.as_bet_detail
#: 確定着順（同着を含む）から当たり目の表記を作る。再実装しないこと。
winning_combo_labels = _canonical.winning_combo_labels


@dataclass
class SoldRace:
    """売った1レース分（＝1商品）。"""

    race_key: str
    race_date: str
    rank_key: str
    origin: str | None
    bet: int
    payout: int
    hit: bool
    #: 払戻 >= 賭け金。**netkeirin の表示的中はこちら**
    net_hit: bool
    n_points: int


@dataclass
class SoldSummary:
    n_races: int = 0
    n_hits: int = 0
    n_net_hits: int = 0
    bet: int = 0
    payout: int = 0
    #: `bet_detail` が無く集計できなかった件数（2026-08-07 以前）
    n_no_detail: int = 0
    payouts: list[int] = field(default_factory=list)

    @property
    def hit_rate(self) -> float | None:
        return self.n_hits / self.n_races if self.n_races else None

    @property
    def net_hit_rate(self) -> float | None:
        """netkeirin の表示的中率（ガミを不的中として数える）。"""
        return self.n_net_hits / self.n_races if self.n_races else None

    @property
    def roi(self) -> float | None:
        return self.payout / self.bet if self.bet else None

    @property
    def gami_rate(self) -> float | None:
        """当たったうちガミだった割合。"""
        return (self.n_hits - self.n_net_hits) / self.n_hits if self.n_hits else None

    @property
    def median_payout(self) -> int | None:
        """的中したレースの払戻の中央値（外れは含めない）。"""
        if not self.payouts:
            return None
        p = sorted(self.payouts)
        return p[len(p) // 2]


def settle_submission(
    bet_detail: Any,
    finishers: Iterable[Sequence[int]] | None,
    payouts: Mapping[str, int] | None = None,
) -> Settlement | None:
    """1入稿を採点する。**買い目が読めない入稿だけ None**。

    Args:
        bet_detail: `netkeirin_submissions.bet_detail`
        finishers: `(着順, 車番)` の並び。**3着以内の行をすべて渡すこと**
            （同着があると3件を超える）。未確定なら None
        payouts: `{当たり目の表記: 100円あたりの確定払戻}`。
            表記は三連複 `1=2=4` / 三連単 `1-2-4`（`payout_per_100` で作る）

    🔴 **採点が終わったかは `Settlement.settled` を見ること。** False は「外れ」では
       なく「まだ分からない」で、集計にも通知にも混ぜてはいけない。
    🔴 引けない配当を `bet_detail.odds`（入稿時点のオッズ）で代用しない。
       発走までに動くので払戻を過大にも過小にもする（実測 2026-08-15 松山9R で
       9.0倍→6.3倍・43%過大）。以前ここだけが代用しており、Web が「未確定」と
       出すレースに Discord が金額を出していた。
    """
    out = settle(bet_detail, finishers, payouts)
    return out if out.bet > 0 else None


def build_sold_races(
    submissions: Iterable[Mapping[str, Any]],
    finishes: Mapping[str, Sequence[Sequence[int]]],
    payouts: Mapping[str, Mapping[str, int]] | None = None,
) -> tuple[list[SoldRace], int]:
    """入稿の一覧から `SoldRace` を組み立てる。

    submissions の各要素に必要なキー:
      `race_key` / `race_date` / `rank_key` / `bet_detail`（`origin` は任意）
      **取消済み（deleted）は呼び出し側で除いておくこと**——商品ではないため。

    finishes: race_key → `(着順, 車番)` の並び（3着以内・同着なら4件以上）
    payouts:  race_key → `{当たり目の表記: 100円あたりの確定払戻}`

    returns (売れたレース, 採点できなかった件数)
    🔴 **採点が終わっていない入稿は「外れ」ではなく `skipped`** に数える。
       0円として足すと、当たっているレースが払戻0円で回収率に入る。
    """
    out: list[SoldRace] = []
    skipped = 0
    for s in submissions:
        rk = str(s.get("race_key") or "")
        got = settle_submission(
            s.get("bet_detail"), finishes.get(rk), (payouts or {}).get(rk))
        if got is None or not got.settled:
            skipped += 1
            continue
        out.append(SoldRace(
            race_key=rk, race_date=str(s.get("race_date") or ""),
            rank_key=str(s.get("rank_key") or ""), origin=s.get("origin"),
            bet=got.bet, payout=got.payout, hit=got.hit, net_hit=got.net_hit,
            n_points=got.n_combos,
        ))
    return out, skipped


def summarize(races: Iterable[SoldRace], n_no_detail: int = 0) -> SoldSummary:
    s = SoldSummary(n_no_detail=n_no_detail)
    for r in races:
        s.n_races += 1
        s.bet += r.bet
        s.payout += r.payout
        s.n_hits += int(r.hit)
        s.n_net_hits += int(r.net_hit)
        if r.hit:
            s.payouts.append(r.payout)
    return s


def group_by(races: Iterable[SoldRace], key: str) -> dict[str, SoldSummary]:
    """`rank_key` / `race_date` / `origin` 等で束ねる。"""
    buckets: dict[str, list[SoldRace]] = {}
    for r in races:
        buckets.setdefault(str(getattr(r, key, "") or "—"), []).append(r)
    return {k: summarize(v) for k, v in sorted(buckets.items())}
