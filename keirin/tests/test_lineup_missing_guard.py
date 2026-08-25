"""WT印・並びが取れていないレースを入稿しない（2026-08-26 新設）。

## 何があったか

2026-08-26、winticket が**熊本の全7レースで並び予想と AI 印を公開しないまま**
朝の入稿が走った。他の7場は同日すべて正常（印28〜48車・ライン3.3〜3.8本）で、
熊本だけが 印0車・ライン1本。8月のミッドナイト 492レースで印ゼロは
**この7レースだけ**。

欠けた2つは指数と予測オッズの**両方の入力**:
  - `prediction_mark` … `FEATURE_COLS_WT` の「winticket AI 印（市場人気の代理）」
  - `line_group` 系 … 指数の `line_leader_rp*` / `n_lines` / `n_senko`、
    予測オッズの `n_line_in` / `same_line_max` / `has_top_line` / `lead_in`

🔴 **欠測はエラーにならない。** 印なしは最弱（`NO_MARK=5`）、ライン無しは
   「全員が同じ1本のライン」として読まれ、指数もオッズもそれらしい値のまま
   静かにずれる。実際その日は3商品が出来上がり、うち1つが当日の
   「自信」レースだった。

ここで固定するのは4つ:
  1. 判定は**レース単位・全車ゼロのときだけ**（1人だけ印なしは正常）
  2. **ランクループと看板穴埋めの両方**に入っていること
     （2026-08-26 に落ちたのは看板穴埋めの経路）
  3. **レースを確保せずに抜ける**こと（後の波で再判定させる）
  4. 判定できないときは**出す側へ倒す**
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.entry_health import missing_market_inputs  # noqa: E402
from src.submission_skips import ALL_CODES, MISSING_LINEUP, label  # noqa: E402

SUBMIT = ROOT / "scripts" / "netkeirin_submit_wt.py"


def test_healthy_race_is_not_flagged():
    entries = [{"prediction_mark": 1, "line_group": 1},
               {"prediction_mark": 0, "line_group": 1},
               {"prediction_mark": 2, "line_group": 2}]
    assert missing_market_inputs(entries) is None


def test_one_rider_without_a_mark_is_normal():
    """印は上位数車にしか付かない。1人ゼロで落としてはいけない。"""
    entries = [{"prediction_mark": 3, "line_group": 1},
               {"prediction_mark": 0, "line_group": 2}]
    assert missing_market_inputs(entries) is None


def test_all_marks_zero_is_flagged():
    entries = [{"prediction_mark": 0, "line_group": 1},
               {"prediction_mark": 0, "line_group": 2}]
    assert missing_market_inputs(entries) == "WT印が全車ゼロ"


def test_single_line_group_is_flagged():
    """全員が同じ line_group ＝ 並びが取れていない（実データでは 0 で埋まる）。"""
    entries = [{"prediction_mark": 2, "line_group": 0},
               {"prediction_mark": 1, "line_group": 0}]
    assert missing_market_inputs(entries) == "並び（ライン）が未取得"


def test_kumamoto_20260826_shape_is_flagged():
    """当日の熊本の形（印ゼロ・ライン0）は両方の理由が出る。"""
    entries = [{"prediction_mark": 0, "line_group": 0} for _ in range(7)]
    got = missing_market_inputs(entries)
    assert got is not None and "WT印" in got and "並び" in got


def test_empty_entries_do_not_block():
    """🔴 分からないことを理由に商品を落とさない。"""
    assert missing_market_inputs([]) is None


def test_reason_code_is_registered():
    assert MISSING_LINEUP in ALL_CODES
    assert label(MISSING_LINEUP) == "並び未取得"
    assert len(label(MISSING_LINEUP)) <= 8, "バッジのラベルは8文字以内"


def _src() -> str:
    return SUBMIT.read_text(encoding="utf-8")


def test_guard_is_in_the_rank_loop():
    src = _src()
    i = src.index('for cand, gate_label in pending:')
    block = src[i:i + 1500]
    assert "_missing_market_inputs(race_key)" in block, "ランクループにガードが無い"
    assert "continue" in block[block.index("_missing_market_inputs("):], (
        "レースを確保したまま抜けている（後の波で再判定されない）")


def test_guard_is_in_the_marquee_path():
    """🔴 看板穴埋めにも掛ける。2026-08-26 に落ちたのはこの経路。"""
    src = _src()
    i = src.index("def _process_manual(")
    block = src[i:src.index("def ", i + 100)]
    assert "_missing_market_inputs(race_key)" in block, "看板穴埋めにガードが無い"
    assert SUBMIT_RETURN.search(block[block.index("_missing_market_inputs("):]), (
        "見送りで return していない")


SUBMIT_RETURN = re.compile(r"return 0, \[\]")


def test_guard_runs_before_building_the_product():
    """買い目を組む前に見ること（組んでから捨てるのは無駄で、副作用も残りうる）。"""
    src = _src()
    i = src.index("def _process_manual(")
    block = src[i:src.index("def ", i + 100)]
    assert block.index("_missing_market_inputs(") < block.index("_build_tilted_legs("), (
        "買い目を組んだ後にガードが来ている")
