"""欠車（出走取消）の車番を含む買い目は返還として扱う（2026-09-20 監査 item2）。

## なぜ要るか

買い目を組んだ後に出走取消が出ると、その車番を含む leg は**構造的に当たらない**
（出走していない車番が3着以内に入ることはない）。本来は返還されるのに、旧採点は
単に「外れた leg」として `budget` の一部＝全損に計上していた。

実測（2026-08-27〜09-19・`type_lab_picks(mode='live')` 6,601行）:
**9行・6レース・25,400円**。例: `20260830_13_06` の `B_hit` は 4番が
`wt_entries` に存在しない（7車立てのはずが6行）のに、その4番を含む 9,900円ぶんを
全損として ROI の分母へ入れていた。同じ買い目は
`netkeirin_submissions`（実際に売った商品）にも一字一句同じ形で存在する。

🔴 **動くのは投資額（分母）だけ。** 的中判定・払戻は元から無関係なので変えない。
🔴 **出走表が引けないときは何もしない。** 「全部欠車」と読んで全額返還にすると、
   収集が遅れた日の ROI が跳ね上がる（fail-safe の向きは「判定しない」側）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "settle_type_lab_picks", REPO / "scripts" / "settle_type_lab_picks.py")
STL = importlib.util.module_from_spec(_spec)
sys.modules["settle_type_lab_picks"] = STL
_spec.loader.exec_module(STL)


def _legs(*pairs) -> list[dict]:
    return [{"combo": c, "stake": s} for c, s in pairs]


def test_欠車を含む買い目のstakeだけを返還に数える():
    legs = _legs(("2-4-1", 2400), ("2-1-4", 4900), ("2-4-3", 2600), ("5-2-3", 100))
    # 4番が欠車（実データ 20260830_13_06 と同じ形）
    assert STL._void_stake(legs, {1, 2, 3, 5, 6, 7}) == 9900


def test_欠車が無ければゼロ():
    legs = _legs(("1=2=3", 5000), ("1=2=5", 5000))
    assert STL._void_stake(legs, {1, 2, 3, 4, 5, 6, 7}) == 0


def test_出走表を引けないときは判定しない():
    """🔴 None は「全員欠車」ではない。返還 0 で従来どおり扱う。"""
    legs = _legs(("2-4-1", 2400))
    assert STL._void_stake(legs, None) == 0


def test_三連複でも同じ判定になる():
    """区切り文字（= / -）に依らず車番だけを見る。"""
    assert STL._void_stake(_legs(("1=4=5", 3300)), {1, 2, 3, 5, 6, 7}) == 3300


def test_全部が欠車を含めば全額が返還される():
    legs = _legs(("1-4-5", 5000), ("4-1-5", 5000))
    assert STL._void_stake(legs, {1, 2, 3, 5, 6, 7}) == 10000


def test_列がまだ無くても採点は続ける():
    """🔴 デプロイは `git pull` → `alembic upgrade` の順で、その間だけ列が無い。

    15分おきのこの cron がその窓に当たっても、**採点そのものは進める**
    （返還の記録だけ諦める）。落とすとその回の採点が丸ごと飛ぶ。
    """
    src = (REPO / "scripts" / "settle_type_lab_picks.py").read_text(encoding="utf-8")
    assert "def _has_void_refund()" in src
    # 列が無い側の UPDATE には void_refund を入れない
    assert '", void_refund = ? " if has_void else " "' in src
