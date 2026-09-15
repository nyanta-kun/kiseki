"""サマリーの「直近で売っているプラン」判定（2026-09-15）。

ユーザー要望「サマリーに大量のモデルを表示していますが、無効にしたモデルに
ついては表示、集計から通常表示では外してください」。

「無効」の定義はユーザーが選んだ **直近で売っていないもの**。入稿設定
（`netkeirin_settings.enabled`）は見ない —— 2026-09-15 の段商品への差し替えで
旧型ラボプラン（A_hit〜F_big）は設定上 ON のまま残っており、設定では
「もう売っていない」を表せないため。

DB にも FastAPI にも依存しない純関数だけを置く（SQL は router 側）。
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

#: 商品の差し替え日（7車を段商品 T_firm/T_mid/T_axis/T_upset へ切り替えた日）。
#: 🔴 **これより前の実売は「直近で売っている」の根拠にしない。** 差し替え前は
#:    旧型ラボプランを毎日売っていたので、14日窓をそのまま遡ると差し替え後
#:    2週間は売るのをやめたプランが「有効」に数えられ、要望の効果が出ない。
PRODUCT_SWITCH_DATE = date(2026, 9, 15)

#: 「直近」の長さ（基準日を含む日数）。
#: 9車の商品は開催が無い日には出ないので1日では短すぎる。2週間あれば
#: 通常の開催サイクル（3〜4日開催が週に複数場）で一度は売る機会がある。
ACTIVE_WINDOW_DAYS = 14


def active_window(today: date) -> tuple[date, date]:
    """「直近で売っているか」を見る期間（両端含む）を返す。

    終点は基準日。始点は `today − (ACTIVE_WINDOW_DAYS − 1)` と差し替え日の遅い方。
    基準日が差し替え日より前なら（過去日を見ている）差し替えは考慮しない。
    """
    start = today - timedelta(days=ACTIVE_WINDOW_DAYS - 1)
    if today >= PRODUCT_SWITCH_DATE:
        start = max(start, PRODUCT_SWITCH_DATE)
    return start, today


def resolve_active(sold_labels: Iterable[str]) -> frozenset[str] | None:
    """窓内に売った表示ラベルの集合を返す。**1件も無ければ None（＝絞らない）**。

    🔴 fail-open。差し替え当日の朝（入稿前）に空集合で絞ると、サマリーの
       合計もランク別も全部消えて「壊れた」画面になる。既存の
       `visible_rank_labels` と同じ思想。
    """
    labels = frozenset(sold_labels)
    return labels or None


def is_active(label: str, active: frozenset[str] | None) -> bool:
    """集計・表示に含めてよいか。`active is None`（絞らない）なら常に True。

    ⚠️ `A_hit@9` のような車数つきキーは車数を外して判定する。
    """
    if active is None:
        return True
    return label.split("@", 1)[0] in active


def inactive_labels(order: Iterable[str], active: frozenset[str] | None) -> list[str]:
    """`order` のうち非アクティブなラベルを順序を保って返す（絞らないなら空）。"""
    if active is None:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for label in order:
        if label in seen or label in active:
            continue
        seen.add(label)
        out.append(label)
    return out
