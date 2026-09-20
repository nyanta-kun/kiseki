"""実際に出走表に載っている車番（＝欠車でない車）を引く（2026-09-20 新設）。

## なぜ1か所にまとめるか

欠車（出走取消）は `wt_entries` から**行ごと消える**（`scraper/pipeline_wt.py`）。
そのため「買い目に載っている車番が出走表に無い」＝その目は**構造的に当たらない**
＝返還、という判定は `wt_entries` を引かないと作れない。

この引き方が採点経路ごとにコピーされると、**同じ商品の投資額が画面と Discord と
夜間レビューで食い違う**（2026-08-25 に一度やった型そのもの）。実際、監査
（`docs/AUDIT_2026_09_20.md` §8 item2）の修正では

  - `backend/src/api/keirin_router.py`（Web の実売集計）
  - `scripts/settle_type_lab_picks.py`（型ラボの採点）

の2か所に別々の実装が入り、**keirin 側の `src/sold_performance.py` を通る経路**
（夜間レビュー・前向き観測・CLI レポート・Discord の結果通知）だけが
旧来の「全損」のままという食い違いが生まれた。ここはその穴を塞ぐための共通部品。

🔴 **判定そのものは書かない。** ここは「出走している車番」を返すだけで、
   どの leg を返還にするかは正本
   （`backend/src/services/keirin_settlement.py::settle(valid_cars=)`）が決める。
🔴 **行が1件も無いレースは `None` 扱い**（呼び出し側が「判定しない」を選べるように、
   結果の辞書へキーを作らない）。出走表をまだ取り込めていないだけのレースを
   「全員欠車」と読むと、その日の投資額がゼロになって回収率が跳ね上がる。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from src.database import get_connection


def valid_cars_by_race(keys: Iterable[str]) -> dict[str, set[int]]:
    """{race_key: {出走している車番}}。出走表が1行も無いレースは**含めない**。"""
    ks = sorted({str(k) for k in keys if k})
    if not ks:
        return {}
    out: dict[str, set[int]] = defaultdict(set)
    with get_connection() as c:
        for i in range(0, len(ks), 900):
            chunk = ks[i:i + 900]
            q = ("SELECT race_key, frame_no FROM wt_entries "
                 f"WHERE race_key IN ({','.join('?' * len(chunk))})")
            for rk, fn in c.execute(q, chunk).fetchall():
                out[str(rk)].add(int(fn))
    return dict(out)
