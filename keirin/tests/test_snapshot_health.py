"""オッズスナップショットの見張りと h16 取得の配線を固定する（2026-10-07）。

欠けは後から埋められないうえ、2回とも誰にも気づかれなかった。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_odds_snapshot_health import (  # noqa: E402
    EXPECTED_TYPES, H16_FIRST_DAY, build_message, evaluate)

DAY = "2026-10-09"
FULL = {t: 100 for t in EXPECTED_TYPES}


def test_正常なら空():
    assert evaluate(DAY, 40, 36, 36, FULL) == []


def test_開催なしは判定しない():
    assert evaluate(DAY, 0, 0, 0, {}) == []


def test_種類が1つ欠けたら報告する():
    counts = {k: v for k, v in FULL.items() if k != "h14"}
    assert evaluate(DAY, 40, 36, 36, counts) == ["h14 が1件も無い"]


def test_0件の種類も欠けとして扱う():
    assert evaluate(DAY, 40, 36, 36, {**FULL, "h20": 0}) == ["h20 が1件も無い"]


def test_morningの被覆不足():
    p = evaluate(DAY, 40, 40, 35, FULL)  # 87.5%
    assert len(p) == 1 and "morning の被覆" in p[0]


def test_morningの被覆ちょうど90はOK():
    assert evaluate(DAY, 40, 40, 36, FULL) == []


def test_7車以上が0でも被覆では落ちない():
    assert evaluate(DAY, 3, 0, 0, FULL) == []


def test_h16は導入日より前は判定しない():
    counts = {k: v for k, v in FULL.items() if k != "h16"}
    assert evaluate("2026-10-07", 40, 36, 36, counts) == []
    assert evaluate(H16_FIRST_DAY, 40, 36, 36, counts) == ["h16 が1件も無い"]


def test_eveningは見ない():
    assert "evening" not in EXPECTED_TYPES
    assert evaluate(DAY, 40, 36, 36, FULL) == []  # evening 無しで正常


def test_全部欠けは全部報告する():
    assert len(evaluate(DAY, 40, 36, 0, {})) == len(EXPECTED_TYPES) + 1


def test_メッセージに日付と内容が入る():
    m = build_message(DAY, ["h16 が1件も無い"])
    assert DAY in m and "h16 が1件も無い" in m


# --- 配線（文字列で固定。コメントだけの記述を拾わないよう実行行を見る）---

def _code(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def test_intraday_results_にh16のステップがある():
    code = "\n".join(_code(ROOT / "scripts" / "intraday_results_wt.sh"))
    assert 'CURRENT_HOUR" == "16"' in code
    assert "snapshot_intraday_odds_wt.py" in code
    assert "--type h16" in code
    assert "--skip-if-exists" in code
    # 失敗しても結果収集は止めない
    assert "h16 スナップショットに失敗（継続）" in code


def test_h16のステップは_KEIRIN_DB_URL_ガードの後ろにある():
    text = (ROOT / "scripts" / "intraday_results_wt.sh").read_text(encoding="utf-8")
    assert text.index("KEIRIN_DB_URL が未設定です") < text.index("--type h16")
    assert text.index("flock -n 200") < text.index("--type h16")


def test_nightly_review_が見張りを呼ぶ():
    code = "\n".join(_code(ROOT / "scripts" / "nightly_review.sh"))
    line = next(ln for ln in code.splitlines() if "check_odds_snapshot_health.py" in ln)
    assert '"$DAY"' in line
    # 失敗しても夜間レビューを止めない
    assert "||" in code.split(line, 1)[1].splitlines()[1]
