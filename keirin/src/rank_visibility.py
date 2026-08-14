"""「いま運用中のランク」の判定（2026-08-14 新設）。

## 背景（実際に届いた誤通知）

`netkeirin_settings.enabled` は**入稿を止めるだけ**で、ライブ判定・picks_history への
記録・Discord 通知は動き続けていた。そのため 9H1（enabled=false）の不的中通知が
毎レース届き、「廃止したはずのモデルの通知が来る」状態になっていた（ユーザー指摘）。

kiseki Web は `_enabled_rank_labels()` で同じフラグを見て非表示にしているので、
**Discord だけが食い違っていた**。ここを単一の判定に寄せる。

🔴 **fail-open**（読めなければ全ランクを運用中とみなす）。
   DB が読めない・列が無いといった理由で通知を止めると、
   **何も届かないまま誰も気づかない**。止めるのは運用者が明示的に
   OFF にしたときだけにする。
"""
from __future__ import annotations

from src.database import get_connection


def disabled_rank_names() -> set[str]:
    """入稿 OFF のランクの**内部rank名**（`RANK_9H1` 等）を返す。

    読めなければ空集合（＝何も止めない）。
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT rank_key FROM netkeirin_settings WHERE enabled = ?", (False,),
            ).fetchall()
    except Exception:                       # noqa: BLE001 — 通知を止めない
        return set()
    out = set()
    for r in rows:
        key = r["rank_key"] if not isinstance(r, (tuple, list)) else r[0]
        if not key or key == "_global":
            continue
        out.add(f"RANK_{key}")
    return out


def is_operating(rank: str | None, disabled: set[str] | None = None) -> bool:
    """その内部rank名が運用中か。

    disabled: `disabled_rank_names()` の結果。レースごとに引き直さないよう
      呼び出し側で1回だけ取って渡す。
    """
    if not rank:
        return True
    off = disabled_rank_names() if disabled is None else disabled
    return rank not in off
