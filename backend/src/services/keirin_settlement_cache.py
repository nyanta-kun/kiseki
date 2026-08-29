"""売った商品の採点結果を **入稿の行へ焼き付ける**ためのキー計算（純関数）。

## なぜ要るのか

`/keirin` のサマリーは当日・当月・当年の3期間を出す。当年ぶんは
`_fetch_settled_submissions` が **入稿1,091件をその場で採点し直す**造りで、
1リクエストあたり `wt_entries`（592MB）へ 3,209回・`wt_odds`（**7.2GB**）へ
2,138回のインデックス参照を投げていた。

この DB の `shared_buffers` は **128MB**（`wt_odds` の 1.8%）しかなく、
中央・地方の作業でページが押し出されるとここが全部ランダム IO になる。
実測（2026-08-29・`EXPLAIN (ANALYZE, BUFFERS)`）:

| 状態 | 当年ぶんの採点に要る参照 | 実測 |
|---|---|---|
| 温 | 同じ | 172ms |
| 冷（read=2,321 blocks） | 同じ | **1,480ms** |

本番の `/keirin/summary` は温 0.48〜0.79秒 / 冷 **1.4〜7.0秒**で振れており、
これがユーザーの見る「サマリーだけ遅い」の正体だった。

## 焼き付けてよい理由

**着順と確定配当が入った後の採点結果は二度と変わらない。**
`settle()` は `bet_detail`（入稿の瞬間に保存された事実）と着順・確定配当だけで
決まり、`settled=True` はその3つが揃ったことを意味する。

## 焼き付けてはいけない条件（このモジュールが守る）

🔴 **`settled=False` を焼かない。** 「まだ分からない」は結果ではない。
   焼くと当たっているレースが永久に「外れ・払戻0円」で固定される
   （`keirin_settlement` の docstring にある実害そのもの）。

🔴 **採点ロジックか `bet_detail` が変わったキャッシュは使わない。**
   両方を `fingerprint()` に畳み込み、**行に保存した値と一致するときだけ**読む。
   一致しなければ黙って実採点へ落ちる（fail-safe: 遅くなるだけで値は正しい）。

   - 採点ロジック … `SETTLE_VERSION`。`keirin_settlement` の**意味**を変えたら上げる。
     上げ忘れの検知は `tests/test_keirin_settlement_cache.py` が
     `keirin_settlement.py` の AST（docstring・コメントを除いた構文木）と
     突き合わせて行う。コメントの手直しでは落ちない
   - 入稿の中身 … `bet_detail` の本文そのもの。keirin 側の再入稿は
     `ON CONFLICT DO UPDATE` で `bet_detail` を書き換えうるが、
     **キャッシュ列は INSERT の列に並ばないので残る**。指紋が無いと
     「古い買い目の採点結果」を新しい買い目の成績として出してしまう
"""

from __future__ import annotations

import hashlib
from typing import Any

#: 採点ロジックの世代。`keirin_settlement.settle()` の**意味**を変えたら上げること。
#: 上げると全行の指紋が変わり、キャッシュは自動的に作り直される（値は常に正しい）。
SETTLE_VERSION = 1

#: 指紋の列幅（`netkeirin_submissions.settled_fp`）。
FINGERPRINT_LEN = 32


def fingerprint(bet_detail: str | None, version: int = SETTLE_VERSION) -> str:
    """採点結果を再利用してよいかを決める指紋。

    採点の入力のうち **あとから変わりうるもの**（採点ロジックの世代と
    `bet_detail`）だけを畳み込む。着順・確定配当は `settled=True` の時点で
    確定しているので入れない。
    """
    src = f"{version}\x00{bet_detail or ''}"
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:FINGERPRINT_LEN]


def cached_settlement(row: Any, bet_detail: str | None) -> dict[str, Any] | None:
    """行に焼かれた採点結果。使えないときは None（＝実採点へ落とす）。

    Args:
        row: `settled_fp` / `settled_bet` / `settled_payout` / `settled_hit` /
             `settled_n_combos` を持つ `netkeirin_submissions` の行
        bet_detail: その行の現在の `bet_detail`（指紋の照合に使う）

    🔴 **1つでも欠けていたら使わない。** 部分的に埋まった行を信じると
       投資0円・払戻0円の「外れ」を作ってしまう。
    """
    fp = _get(row, "settled_fp")
    if not fp or fp != fingerprint(bet_detail):
        return None
    bet, payout = _get(row, "settled_bet"), _get(row, "settled_payout")
    hit, n_combos = _get(row, "settled_hit"), _get(row, "settled_n_combos")
    if bet is None or payout is None or hit is None or n_combos is None:
        return None
    bet, payout = int(bet), int(payout)
    if bet <= 0:
        # 買い目が記録されていない入稿（2026-08-07 以前）は集計に入れない側の行。
        # 焼かれること自体が想定外なので使わない。
        return None
    hit = bool(hit)
    return {
        "bet": bet,
        "payout": payout,
        "hit": hit,
        # netkeirin の表示的中率はこちら（ガミ＝払戻<賭け金 を不的中と数える）。
        # 保存はしない —— `hit` と金額から一意に決まるので列を増やすと
        # 食い違う余地だけが増える。
        "net_hit": hit and payout >= bet,
        "n_combos": int(n_combos),
    }


def is_cacheable(settled: bool, bet: int) -> bool:
    """この採点結果を行へ焼いてよいか。

    🔴 未採点（`settled=False`）と買い目なし（`bet<=0`）は焼かない。
       前者は「まだ分からない」、後者は集計から外す側の行で、どちらも
       「結果」ではない。
    """
    return bool(settled) and bet > 0


def _get(row: Any, key: str) -> Any:
    """RowMapping / dict / 属性アクセスのどれでも読めるようにする。"""
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return getattr(row, key, None)
