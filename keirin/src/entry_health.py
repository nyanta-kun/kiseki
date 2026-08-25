"""出走表の入力が揃っているかを見る（2026-08-26 新設）。

## なぜ要るのか

2026-08-26、**熊本の全7レースで winticket の「並び予想」と「AI印」が
公開されないまま**朝の入稿が走り、3商品（うち1つは当日の「自信」レース）が
出来上がった。ユーザー指摘「2車に指数が集中している場合、現在のような
想定オッズはつきません」の正体がこれ。

この2つは**指数（p3/pw）と予測オッズの両方の入力**:

- `prediction_mark` … `FEATURE_COLS_WT` の「winticket AI 印（市場人気の代理変数）」
- `line_group` / `line_size` / `line_pos` / `is_line_leader` / `n_lines` …
  指数側は `line_leader_rp*` `line_rp_spread` `n_lines` `line_frac` `n_senko`、
  予測オッズ側は `n_line_in` `same_line_max` `has_top_line` `lead_in` `lpos_sum`

欠けると 0（印なし）・全員同ライン という**学習データにほぼ存在しない入力**になる。
2026年の7車レース 15,421本のうち「印が全車ゼロ」は **5本**、「ライン1本」は
**10本**しかなく、そのうち7本が当日の熊本だった＝実質 out-of-distribution。

🔴 **モデルは欠測を欠測として扱わない。** 印なしは `NO_MARK=5`（最弱）、
   ライン無しは「全員が同じ1本のライン」として読まれる。エラーは出ず、
   指数もオッズも**それらしい値のまま静かにずれる**。

## 何をするか

入稿の前にこれを見て、欠けていたら**その回は見送る**（`missing_lineup`）。
レースを確保せずに抜けるので、**同じ開催の後の波（13:00 / 18:00）で再判定される**
——熊本のようなミッドナイトは、その頃には公開されていることがある。

⚠️ 判定は「レース単位」。1人だけ印が無いのは正常（印は上位数車にしか付かない）。
   **全車ゼロ**のときだけ「取れていない」と見なす。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def missing_market_inputs(entries: Iterable[Mapping[str, Any]]) -> str | None:
    """WT印・並びが取れていないなら理由の文字列、揃っていれば None。

    entries: `wt_entries` の行（`prediction_mark` と `line_group` を見る）

    >>> ok = [{"prediction_mark": 1, "line_group": 1},
    ...       {"prediction_mark": 0, "line_group": 2}]
    >>> missing_market_inputs(ok) is None
    True
    >>> missing_market_inputs([{"prediction_mark": 0, "line_group": 1},
    ...                        {"prediction_mark": 0, "line_group": 2}])
    'WT印が全車ゼロ'
    >>> missing_market_inputs([{"prediction_mark": 2, "line_group": 0},
    ...                        {"prediction_mark": 1, "line_group": 0}])
    '並び（ライン）が未取得'
    >>> missing_market_inputs([]) is None
    True
    """
    rows = list(entries)
    if not rows:
        # 出走表そのものが無いのは別の経路（候補が作れない）で落ちる。
        # ここで落とすと「分からない」を理由に商品を消すことになる。
        return None
    missing = []
    if not any(int(r.get("prediction_mark") or 0) > 0 for r in rows):
        missing.append("WT印が全車ゼロ")
    groups = {int(r.get("line_group") or 0) for r in rows}
    if len(groups) <= 1:
        missing.append("並び（ライン）が未取得")
    return " / ".join(missing) if missing else None
