#!/usr/bin/env python3
"""netkeirin の**未公開（公開待ち）件数**を JSON で出す（2026-08-16 新設）。

    python scripts/netkeirin_publish_wait.py
    → {"ok": true, "count": 3, "list": [...]}

確認画面（`/keirin/review`）が webhook 経由で呼ぶ。

## なぜ自前の記録だけでは足りないか

こちらの `netkeirin_submissions.status` は「入稿した（submitted）」「公開した
（published）」を持つが、**netkeirin の画面から人が直接公開すると記録は
submitted のまま**取り残される。実際 2026-08-16 に 35件が submitted のまま
netkeirin 側は公開待ち0件、という状態を観測している。

そこで **netkeirin 側の実数**（`action=get_wait`）も併せて出し、
2つの数字を画面へ並べる。食い違い自体が「画面外で操作された」という情報になる。

🔴 **読み取り専用**（`get_wait` に副作用は無い）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.netkeirin_client import NetkeirinClient  # noqa: E402


def main() -> int:
    try:
        count, items = NetkeirinClient(propose_only=False).count_wait()
    except Exception as e:  # noqa: BLE001 — 付随情報なので画面を落とさない
        print(json.dumps({"ok": False, "count": 0, "list": [], "message": str(e)},
                         ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "count": count, "list": items}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
