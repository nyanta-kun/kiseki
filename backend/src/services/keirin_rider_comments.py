"""出走表に出す選手コメントと「調子が良い」の判定（2026-10-01）。DB にも FastAPI にも依存しない純関数。

コメントは `keirin.rider_interviews`（winticket の前検日コメント・レース後コメント）。

## 何を出すか（1選手・1レースあたり）

1. **前検日コメント**（その開催のもの）
2. **このレースのレース後コメント**（レースが終わっていれば。振り返り用）
3. **前のレースのレース後コメント**（直近 `RECENT_POST_MAX` 件・`POST_MAX_DAYS` 日以内）

## 「調子が良い」（選手名を青字にする）

🔴 **発走前に公開されていたコメントだけ**で決める（このレースのレース後コメントは使わない）。
公開時刻は winticket 側の `updatedAt`（`src_updated_at`・本文の最終版の時刻）で見る。
その中で**いちばん新しいコメントの調子（condition）が `GOOD_CONDITION_MIN` 以上**なら青。

根拠（2026-10-01・142,624件の検証・`keirin/docs/type_lab/prereg_rider_condition_2026_10_01.md`）:
前検日で調子が良いと言った選手の3着内率は、現行モデルの予測より +1.3 / +1.6pt（探索/確認）、
確定オッズの予測より +1.2 / +1.4pt 高かった。後者は副表からの拾い出しなので前向きに再検証中。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

GOOD_CONDITION_MIN = 1
RECENT_POST_MAX = 2
POST_MAX_DAYS = 30


def _epoch(v: Any) -> float | None:
    """UNIX 秒・ISO 文字列・datetime のどれでも UNIX 秒にする。読めなければ None。"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=UTC)).timestamp()
    s = str(v)
    try:
        return float(s)
    except ValueError:
        pass
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).timestamp()


def comments_for_entry(
    comments: Sequence[Mapping[str, Any]], *, race_key: str, cup_id: str | None, race_start: Any
) -> tuple[list[dict], bool]:
    """1選手ぶんのコメント（その選手の行だけを渡す）から、表示する分と「調子が良い」を返す。

    Args:
        comments: `rider_interviews` の行（kind, race_key, cup_id, race_date, body,
                  src_updated_at, condition ...）。その選手のものだけ。
        race_key: 対象レース。
        cup_id: 対象レースの開催。
        race_start: 対象レースの発走時刻（UNIX 秒 or ISO）。不明なら None
                   （None のときは前検日コメントとこのレースより前のレース後コメントを「発走前」とみなす）。

    Returns:
        (表示するコメント, 調子が良いか)。表示の各要素は
        {"kind", "label", "race_date", "body", "condition", "before_race"}。

    >>> cs = [{"kind": "pre", "race_key": "r1", "cup_id": "C", "race_date": "2026-10-01",
    ...        "body": "仕上がり良い", "src_updated_at": 100, "condition": 1},
    ...       {"kind": "post", "race_key": "r2", "cup_id": "C", "race_date": "2026-10-02",
    ...        "body": "脚が重い", "src_updated_at": 300, "condition": -1}]
    >>> shown, good = comments_for_entry(cs, race_key="r3", cup_id="C", race_start=400)
    >>> [c["label"] for c in shown], good
    (['前検日', '前走後'], False)
    >>> comments_for_entry(cs, race_key="r3", cup_id="C", race_start=200)[1]   # r2 の談話は発走後
    True
    """
    start = _epoch(race_start)
    pre = [c for c in comments if c.get("kind") == "pre" and c.get("cup_id") == cup_id]
    own = [c for c in comments if c.get("kind") == "post" and c.get("race_key") == race_key]
    prev = [c for c in comments if c.get("kind") == "post" and c.get("race_key") != race_key]

    def published_before(c: Mapping[str, Any]) -> bool:
        t = _epoch(c.get("src_updated_at"))
        if start is None or t is None:
            return c.get("kind") == "pre" or str(c.get("race_key")) < race_key
        return t <= start

    # 前のレースのレース後コメント: 発走前に出ていたもの・新しい順・日数制限
    def recent(c: Mapping[str, Any]) -> bool:
        if start is None:
            return True
        t = _epoch(c.get("src_updated_at"))
        return t is None or t >= start - POST_MAX_DAYS * 86400

    prev_ok = sorted(
        [c for c in prev if published_before(c) and recent(c)],
        key=lambda c: _epoch(c.get("src_updated_at")) or 0,
        reverse=True,
    )

    before = [c for c in pre if published_before(c)] + prev_ok
    latest = max(before, key=lambda c: _epoch(c.get("src_updated_at")) or 0, default=None)
    good = bool(
        latest is not None and latest.get("condition") is not None and int(latest["condition"]) >= GOOD_CONDITION_MIN
    )

    def fmt(c: Mapping[str, Any], label: str) -> dict:
        return {
            "kind": c.get("kind"),
            "label": label,
            "race_date": str(c.get("race_date") or ""),
            "body": c.get("body") or "",
            "condition": c.get("condition"),
            "before_race": published_before(c) if label != "このレース後" else False,
        }

    shown = (
        [fmt(c, "前検日") for c in pre[:1]]
        + [fmt(c, "前走後") for c in prev_ok[:RECENT_POST_MAX]]
        + [fmt(c, "このレース後") for c in own[:1]]
    )
    return shown, good


def post_window_start(race_dates: Sequence[str]) -> str:
    """レース後コメントを引く日付の下限（対象日の最小 − `POST_MAX_DAYS` 日）。"""
    d0 = min(datetime.fromisoformat(str(d)[:10]) for d in race_dates)
    return (d0 - timedelta(days=POST_MAX_DAYS)).strftime("%Y-%m-%d")
