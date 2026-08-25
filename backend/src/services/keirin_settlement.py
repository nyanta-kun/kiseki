"""入稿（netkeirin_submissions.bet_detail）の採点 — **判定の唯一の正本**。

## なぜ1本にするのか

同じ1レース1商品の結果が、2026-08-25 時点で **3つの画面でバラバラに出ていた**。

| 画面 | 出どころ | 実例（08-25 防府8R・7S） |
|---|---|---|
| Discord 成績報告 | `bet_detail` を採点 | 🎯 的中 15,200円 |
| 入稿確認 `/keirin/review` | `bet_detail` を採点（ただし別実装） | … 未確定 |
| 推奨一覧 `/keirin` | **`picks_history.hit`（ペーパー候補）** | ✗ 不的中 |

一覧が食い違ったのは、`picks_history` が「ランクの候補」であって
**売った商品ではない**ため。防府8R は看板の穴埋めで軸を 1・7 に組み替えて
入稿しており、候補（軸 7・2）とは別の買い目を売っていた。同じ現象は
2026-08-07〜25 の**売った295商品のうち53件（18%）**で起きていた。

さらに採点の実装自体も3つに分かれ、次の点でずれていた:

  - **同着**: 一覧・確認画面は「3着以内がちょうど3車」でないと採点せず、
    永久に「未確定」のまま集計からも落ちていた（08-21 立川11R の 7S は
    10,000円の外れが1日の回収率から消えていた）。Discord は着順の先頭3件を
    そのまま使うので、同着では当たり目を取り違えうる
  - **端数**: Web は 100円あたり払戻を10円未満切り捨て（実払戻との一致を
    2026-07-12 に検証済み）、Discord は切り捨てなし
  - **券種**: Web は買い目の区切り文字から券種を推測、Discord は `bet_type` を見る
  - **配当が引けないとき**: Web は「未採点」、Discord は**入稿時点のオッズ**で
    代用（発走までに動くので必ずずれる）

**採点はここだけに書く。** 呼び出し側（`api/keirin_router.py` の一覧・確認画面、
keirin 側の Discord 通知・成績レポート）は結果を読むだけにする。

## 決め方

- **券種は `bet_type` が正本**（`3連複` / `3連単`）。区切り文字から推測しない。
  実データは 4,163点すべてで `3連複`↔`=` / `3連単`↔`-` が一致しているが、
  推測に頼ると片方が崩れたときに**当たっているのに不的中**になる
- **当たり目は同着を展開する**（`keirin_result_top3.winning_combo_labels`）。
  3着同着なら三連複の当たりは2通り、1・2着の同着なら三連単が増える
- **払戻は確定配当（`wt_odds` の最終オッズ）だけ**。`bet_detail.odds` は
  入稿時点の値で発走までに動くので使わない。引けないときは
  `settled=False`（＝「まだ分からない」）にして、外れにも0円にもしない
- **`hit` と `settled` は別物**。的中は買い目と着順の一致だけで決まる。
  外れは着順だけで確定するので配当を待つ必要がない（`settled=True`）
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

if __package__:                                    # backend から通常 import
    from .keirin_result_top3 import winning_combo_labels
else:                                              # keirin 側からファイル指定で読み込み
    import importlib.util
    import sys
    from pathlib import Path

    _SIB = Path(__file__).resolve().with_name("keirin_result_top3.py")
    _NAME = "kiseki_keirin_result_top3"
    if _NAME in sys.modules:
        winning_combo_labels = sys.modules[_NAME].winning_combo_labels
    else:
        _spec = importlib.util.spec_from_file_location(_NAME, _SIB)
        if _spec is None or _spec.loader is None:  # pragma: no cover - 実質起きない
            raise ImportError(f"当たり目判定の正本を読み込めません: {_SIB}")
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_NAME] = _mod
        _spec.loader.exec_module(_mod)
        winning_combo_labels = _mod.winning_combo_labels

#: `bet_detail.lines[].bet_type` → 着順まで一致が要るか（三連単だけ True）
_ORDERED: dict[str, bool] = {"3連複": False, "3連単": True}
_SEP_RE = re.compile(r"[-=]")


@dataclass(frozen=True)
class Settlement:
    """1入稿（＝1商品）の採点結果。"""

    #: 投資額（`lines[].stake` の合計）。**採点できなくても出す**（発走前から表示する）
    bet: int
    payout: int
    #: 買い目が当たったか。**配当が引けたかとは無関係**
    hit: bool
    #: 採点が終わったか。**False は「外れ」ではなく「まだ分からない」**
    settled: bool
    n_combos: int
    #: 買い目を空白区切りで並べたもの（一覧の買い目行に使う）
    pred_combo: str | None
    #: 確定した当たり目（同着なら複数）。未確定なら空
    winning_combos: list[str] = field(default_factory=list)

    @property
    def net_hit(self) -> bool:
        """払戻が投資以上か。**netkeirin の表示的中率はこちら**（ガミは不的中）。"""
        return self.hit and self.payout >= self.bet


def payout_per_100(odds_value: Any) -> int | None:
    """`wt_odds.odds_value` → 100円あたりの確定払戻（10円未満切り捨て）。

    ⚠️ 切り捨てを外すと Discord と Web で1円〜数円ずれる。実払戻との一致は
       2026-07-12 に検証済みで、**切り捨てるほうが正しい**。
    """
    if odds_value is None:
        return None
    try:
        return int(round(float(odds_value) * 100)) // 10 * 10
    except (TypeError, ValueError):
        return None


def as_bet_detail(raw: Any) -> dict[str, Any] | None:
    """`netkeirin_submissions.bet_detail`（JSON文字列 / dict / None）を dict へ。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    if isinstance(raw, Mapping):
        return dict(raw)
    return None


def combo_label(combo: Any, ordered: bool) -> str | None:
    """買い目を当たり目と突き合わせられる表記へ揃える。

    三連複は車番昇順を `=` で、三連単は着順どおり `-` でつなぐ
    （`winning_combo_labels` と同じ書き方）。3車ぶん読めなければ None。
    """
    try:
        cars = [int(x) for x in _SEP_RE.split(str(combo).strip()) if x != ""]
    except (TypeError, ValueError):
        return None
    if len(cars) != 3:
        return None
    return "-".join(map(str, cars)) if ordered else "=".join(map(str, sorted(cars)))


def settle(
    bet_detail: Any,
    finishers: Iterable[Sequence[int]] | None,
    payouts: Mapping[str, int] | None = None,
) -> Settlement:
    """1入稿を採点する。

    Args:
        bet_detail: `netkeirin_submissions.bet_detail`（JSON文字列でも dict でも可）
        finishers: `(着順, 車番)` の並び。**3着以内の行をすべて渡すこと**
            （同着があると3件を超える）。未確定なら None / 空
        payouts: `{当たり目の表記: 100円あたりの確定払戻}`。
            表記は `combo_label` / `winning_combo_labels` と同じ
            （三連複 `1=2=4` / 三連単 `1-2-4`）。`payout_per_100` で作る

    🔴 引けない配当を `bet_detail.odds` で代用しない。入稿時点のオッズは
       発走までに動くので、払戻を過大にも過小にもする。
    """
    detail = as_bet_detail(bet_detail) or {}
    lines = detail.get("lines") or []
    labels: list[str | None] = []
    bet = 0
    for line in lines:
        try:
            bet += int(line.get("stake") or 0)
        except (TypeError, ValueError):
            pass
        ordered = _ORDERED.get(str(line.get("bet_type") or ""))
        # 未知の券種は**黙って外れにしない**。読めない行が1つでもあれば未採点。
        labels.append(None if ordered is None else combo_label(line.get("combo"), ordered))
    pred = " ".join(str(x.get("combo")) for x in lines) or None
    won = winning_combo_labels(finishers or [])

    base = Settlement(bet=bet, payout=0, hit=False, settled=False,
                      n_combos=len(lines), pred_combo=pred, winning_combos=won)
    if not lines or not won:
        return base                      # 買い目が無い / まだ着順が揃っていない
    if any(x is None for x in labels):
        return base                      # 読めない行がある（券種不明・車番3つでない）

    won_set = set(won)
    pay_map = payouts or {}
    payout = 0
    hit = False
    payout_known = True
    for line, label in zip(lines, labels):
        if label not in won_set:
            continue
        hit = True
        per100 = pay_map.get(label)
        if per100:
            payout += int(per100) * int(line.get("stake") or 0) // 100
        else:
            payout_known = False         # 当たっているのに配当が引けない＝まだ分からない
    return Settlement(bet=bet, payout=payout, hit=hit,
                      # 外れは着順だけで確定する（配当を待つ理由がない）
                      settled=(not hit) or payout_known,
                      n_combos=len(lines), pred_combo=pred, winning_combos=won)
