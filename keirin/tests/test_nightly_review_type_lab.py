"""夜間レビュー（`scripts/nightly_review_type_lab.py`）の不変条件（2026-08-29 新設）。

## 守る不変条件

1. **判定を写経していない。** 決着クラス・軸信頼ゲート・看板・採点は
   すべて kiseki 側の正本をファイル読み込みで束縛する。写した瞬間、
   「画面の答え」と「夜のレビューの答え」が静かに食い違う
2. **参照分布はプランごとに引く。** 全体から引くと今日のプラン構成が比較から消える
3. **表示的中の定義は `SoldRace.net_hit`（払戻 >= 賭け金）と同じ。**
   素の的中で比べると、点数を増やしたときに「改善した」と誤読する
4. **台帳は同じ日を二度書かない。** 採点が進んでから再実行することがある
"""

from __future__ import annotations

import ast
import csv
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "nightly_review_type_lab.py"


def _load():
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("nightly_review_type_lab", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_判定は正本へ委譲している():
    """`_bind` で読む正本のパスが4つとも生きていること。"""
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    bound = [n.args[0].value for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_bind"]
    assert bound, "_bind の呼び出しが無い（正本の束縛をやめていないか）"
    for rel in bound:
        assert (REPO.parent / rel).exists(), f"正本が無い: {rel}"


def test_決着クラスをここで定義していない():
    """`FINISH_CLASSES` 相当の分類名を写経していないこと。"""
    src = SCRIPT.read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]        # モジュール docstring は説明なので除く
    for token in ("FINISH_CLASSES = ", "def finish_class("):
        assert token not in body, f"分類の定義を写経している: {token}"


def test_軸信頼ゲートの閾値を写経していない():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "AXIS_GATE_MIN = " not in src
    assert "_GATE.passes_axis_gate" in src


def test_参照分布はプランごとに引く():
    m = _load()
    pool = {"A_hit": [(10_000, 0)], "B_hit": [(10_000, 30_000)]}
    # A だけ 2件・B は 0件 → 必ず ROI 0%（B の当たりが混ざったら按分が壊れている）
    got = m._bootstrap(pool, {"A_hit": 2}, n_boot=50, seed=1)
    assert got and all(roi == 0.0 for roi, _ in got)
    got = m._bootstrap(pool, {"B_hit": 2}, n_boot=50, seed=1)
    assert got and all(roi == 3.0 for roi, _ in got)


def test_表示的中はガミを不的中として数える():
    m = _load()
    # 払戻 9,999 円 = 当たっているが賭け金 10,000 円を割る＝ガミ
    pool = {"A_hit": [(10_000, 9_999)]}
    got = m._bootstrap(pool, {"A_hit": 4}, n_boot=20, seed=1)
    assert got and all(hit == 0.0 for _, hit in got)
    pool = {"A_hit": [(10_000, 10_000)]}
    got = m._bootstrap(pool, {"A_hit": 4}, n_boot=20, seed=1)
    assert got and all(hit == 1.0 for _, hit in got)


def test_台帳は同じ日を上書きする(tmp_path, monkeypatch):
    m = _load()
    ledger = tmp_path / "ledger.csv"
    monkeypatch.setattr(m, "LEDGER", ledger)

    class _R:
        def __init__(self, plan, pay):
            self.race_key, self.rank_key, self.origin = "k", plan, None
            self.bet, self.payout = 10_000, pay
            self.hit, self.net_hit, self.n_points = pay > 0, pay >= 10_000, 5

    brk = {"per_plan": {}, "gami_by_plan": {}}
    m.append_ledger("2026-08-29", [_R("A_hit", 0)], brk)
    m.append_ledger("2026-08-29", [_R("A_hit", 50_000)], brk)
    rows = list(csv.DictReader(ledger.open(encoding="utf-8")))
    assert len(rows) == 1, "同じ日が二重に積まれている"
    assert rows[0]["payout"] == "50000", "再実行で新しい値に置き換わっていない"

    m.append_ledger("2026-08-30", [_R("A_hit", 0)], brk)
    rows = list(csv.DictReader(ledger.open(encoding="utf-8")))
    assert len(rows) == 2 and {r["date"] for r in rows} == {"2026-08-29", "2026-08-30"}
