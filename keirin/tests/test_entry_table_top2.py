"""入稿コメントの選手成績表に 2着内率 を足したことの回帰テスト（2026-08-14）。

## 何を守るか

1. `pred_top2_pct` があれば **2着内率の列が出る**（ユーザー要望の本体）
2. 全件 NULL のときは **列ごと消える**（「―」だけの列を売り物へ載せない）
3. 正規化はレース内合計 = min(出走数, 2) × 100%（Web の `normTop2` と同じ目標値）

⚠️ `pred_top2_pct` は 2026-08-12 導入の列で、**過去分はバックフィルしていない**
   ／モデル未配布なら書かれない、の2通りで欠ける。欠けても入稿は止めない。
"""
from __future__ import annotations

import re
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.netkeirin_submit_wt import _build_entry_table  # noqa: E402


def _fake_conn(rows):
    class _Cur:
        def fetchall(self):
            return rows

    class _Conn:
        def execute(self, *_a, **_k):
            return _Cur()

    @contextmanager
    def _cm():
        yield _Conn()

    return _cm


def _rows(top2: list[float] | None):
    win = [30.0, 20.0, 10.0, 10.0, 10.0, 10.0, 10.0]
    top3 = [70.0, 60.0, 45.0, 40.0, 35.0, 30.0, 20.0]
    out = []
    for i in range(7):
        out.append({
            "frame_no": i + 1, "name": f"選手{i + 1}",
            "pred_win_pct": win[i],
            "pred_top2_pct": None if top2 is None else top2[i],
            "pred_top3_pct": top3[i],
        })
    return out


def _build(rows):
    with patch("scripts.netkeirin_submit_wt.get_connection", _fake_conn(rows)):
        return _build_entry_table("20260814_75_11", {1: "◎", 2: "○"})


def test_top2_column_is_shown_when_available():
    html = _build(_rows([50.0, 40.0, 25.0, 22.0, 20.0, 18.0, 15.0]))
    assert "<th>2着内率</th>" in html
    assert "【出走選手 1着率・2着内率・3着内率】" in html
    # 列順は 車番 / 印 / 選手名 / 1着率 / 2着内率 / 3着内率
    heads = re.findall(r"<th>([^<]+)</th>", html)
    assert heads == ["車番", "印", "選手名", "1着率", "2着内率", "3着内率"]


def test_top2_column_disappears_when_all_null():
    """🔴 欠けているときに『―』だけの列を売り物のコメントへ載せない。"""
    html = _build(_rows(None))
    assert "2着内率" not in html
    assert "【出走選手 1着率・3着内率】" in html
    heads = re.findall(r"<th>([^<]+)</th>", html)
    assert heads == ["車番", "印", "選手名", "1着率", "3着内率"]


def test_each_column_is_normalised_to_its_own_target():
    """レース内合計が 1着=100% / 2着内=200% / 3着内=300% になること。"""
    html = _build(_rows([50.0, 40.0, 25.0, 22.0, 20.0, 18.0, 15.0]))
    vals = [float(x) for x in re.findall(r'<td align="center">([0-9.]+)%</td>', html)]
    assert len(vals) == 21, f"7行×3列のはずが {len(vals)} 個"
    assert round(sum(vals[0::3])) == 100
    assert round(sum(vals[1::3])) == 200
    assert round(sum(vals[2::3])) == 300


def test_no_index_still_returns_none():
    """指数未算出（1着率が全件NULL）のレースは従来どおり None（表を省く）。"""
    rows = _rows(None)
    for r in rows:
        r["pred_win_pct"] = None
    assert _build(rows) is None
