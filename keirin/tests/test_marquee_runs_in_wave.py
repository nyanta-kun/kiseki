"""看板レースの穴埋めが「各波の中で・ランク入稿の後に」呼ばれることの回帰テスト。

## 背景

`submit_marquee_wt.py` は 2026-08-12 まで cron の独立エントリ
（07:20 / 13:20 / 18:20 ＝ 各波の20分後）で動いていた。これは
**「波が20分以内に終わる」という暗黙の仮定**に依存していて、朝のバッチは
ライン情報・race_point のリトライ（5分待機×2回）が入ると容易に超える。
そのとき穴埋めがランク入稿より**先**に走り、1レース1商品の制約により
**ランクが取るはずのレースを横取りする**。

しかもこれは失敗しない。横取りされたレースは「別ランクが入稿済み」として
静かに skip されるだけで、ログ上は正常に見える。

そこで各波のシェルから順に呼ぶ形へ移した。ここで守るのは2点:

1. 両方の波スクリプトが `submit_marquee_wt.py` を呼んでいること
2. その呼び出しが `netkeirin_submit_wt.py`（ランク入稿）**より後**にあること

⚠️ 順序が逆転しても例外は出ないので、テストでしか守れない。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "scripts" / "daily_picks_wt.sh"
WAVE = ROOT / "scripts" / "wave_submit_wt.sh"


def _code_lines(path: Path) -> list[str]:
    """コメント・空行を除いた実行行だけを返す（コメント中の言及に釣られないため）。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def _index_of(lines: list[str], script: str) -> int:
    for i, line in enumerate(lines):
        if f"scripts/{script}" in line:
            return i
    return -1


@pytest.mark.parametrize("path", [DAILY, WAVE], ids=["daily_picks_wt", "wave_submit_wt"])
def test_wave_script_calls_marquee(path: Path):
    """各波のスクリプトが看板穴埋めを呼んでいること。"""
    lines = _code_lines(path)
    assert _index_of(lines, "submit_marquee_wt.py") >= 0, (
        f"{path.name} が submit_marquee_wt.py を呼んでいない。"
        "cron の独立エントリへ戻すと、波より先に走って看板を横取りしうる。")


@pytest.mark.parametrize("path", [DAILY, WAVE], ids=["daily_picks_wt", "wave_submit_wt"])
def test_marquee_runs_after_rank_submission(path: Path):
    """穴埋めは**ランク入稿より後**であること（1レース1商品の横取り防止）。"""
    lines = _code_lines(path)
    rank_at = _index_of(lines, "netkeirin_submit_wt.py")
    marquee_at = _index_of(lines, "submit_marquee_wt.py")
    assert rank_at >= 0, f"{path.name} が netkeirin_submit_wt.py を呼んでいない"
    assert marquee_at > rank_at, (
        f"{path.name}: 看板穴埋め(行{marquee_at})がランク入稿(行{rank_at})より前にある。"
        "先に走ると、ランクが取るはずのレースを穴埋めが横取りする"
        "（横取りされても例外は出ず、ログ上は正常に見える）。")


@pytest.mark.parametrize("path", [DAILY, WAVE], ids=["daily_picks_wt", "wave_submit_wt"])
def test_marquee_is_passed_the_batch_date(path: Path):
    """穴埋めにバッチの対象日を渡していること。

    引数を省くと `submit_marquee_wt.py` は**実行時の日付**を使う。日跨ぎや
    日付を明示した再実行で、バッチが扱っている日と別の日を埋めにいく。
    """
    lines = _code_lines(path)
    idx = _index_of(lines, "submit_marquee_wt.py")
    assert idx >= 0
    assert re.search(r'"\$TODAY"', lines[idx]), (
        f"{path.name}: submit_marquee_wt.py に \"$TODAY\" を渡していない"
        f"（実行行: {lines[idx]}）")


def test_marquee_does_not_take_a_session_argument():
    """波ラベルを引数で上書きしていないこと。

    `submit_marquee_wt.py` は**実行時刻の時**から波ラベルを導き、同じ時刻を
    「ミッドナイトを evening まで待たせる」判定にも使っている。片方だけ引数で
    動かすと判定と記録がずれるので、セッションは渡さない設計にしてある。
    """
    for path in (DAILY, WAVE):
        lines = _code_lines(path)
        idx = _index_of(lines, "submit_marquee_wt.py")
        assert idx >= 0
        for banned in ("--session", "$SESSION", "morning", "noon", "evening"):
            assert banned not in lines[idx], (
                f"{path.name}: 穴埋めの呼び出しに波ラベル {banned} を渡している"
                f"（実行行: {lines[idx]}）")
