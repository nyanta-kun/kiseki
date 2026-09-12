"""型（A〜F）を**全車数**ぶん出す経路を固定する（2026-09-12）。

型判定は 3着内率と並びしか見ないので車数に依らないのに、実際に走っていたのは
**商品を組む** `build_type_lab_picks.py` の中だけで、そこは 7車と9車しか回さない。
結果 5車・6車・8車のレースだけ `/keirin` の一覧から型が消えていた。

ここで固定するのは3つ:

1. `build_race_shapes._keys_of_date` が**車数で絞らない**こと
2. 日次・波の両バッチがこのスクリプトを呼ぶこと
   （朝に並び予想が未公開だったレースは波で組み直される）
3. 型判定の規則を**このスクリプトへ写していない**こと
   （正本は `src/type_lab.race_shape`）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SCRIPT = REPO / "scripts" / "build_race_shapes.py"


def _src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_keys_of_date_does_not_filter_by_car_count():
    src = _src()
    m = re.search(r"def _keys_of_date.*?\n\n\n", src, re.DOTALL)
    assert m, "_keys_of_date を見つけられなかった"
    body = m.group(0)
    assert "n_entries" not in body, (
        "その日の全レースを対象にすること。車数で絞ると"
        " 5車・6車・8車の型がまた消える")


def test_type_rule_is_not_duplicated_here():
    """型の境界や arare の加算を**ここに書かない**（正本は `src/type_lab`）。"""
    src = _src()
    assert "from src.type_lab import race_shape" in src, (
        "型判定は src.type_lab.race_shape を呼ぶこと")
    for forbidden in ("AXIS_SUM_FIRM", "BEHIND_MID", "arare ="):
        assert forbidden not in src, (
            f"型判定の規則 {forbidden!r} を写している。"
            " 二重管理になると一覧の型と売っている商品の型が食い違う")


def test_batches_build_shapes():
    for name in ("type_lab_daily.sh", "type_lab_wave.sh"):
        sh = (REPO / "scripts" / name).read_text(encoding="utf-8")
        assert "scripts/build_race_shapes.py" in sh, (
            f"{name} が build_race_shapes.py を呼んでいない")
        # 🔴 型の生成で入稿・採点を止めない（表示専用なので巻き添えにしない）。
        assert re.search(r"if ! \"\$PY\" scripts/build_race_shapes\.py", sh), (
            f"{name}: build_race_shapes.py の失敗で set -e が後続を止めている")


def test_shapes_table_exists_in_sqlite_fallback():
    db = (REPO / "src" / "database.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS race_shapes" in db
    # `keirin.` 接頭辞リストへの足し忘れは test_pg_schema_prefix.py が見る。
