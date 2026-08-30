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

## 🔴 2026-08-30: 朝（`daily_picks_wt.sh`）は対象外になった

型ラボ全面移行にともない、朝のバッチからは**旧ランクの入稿と看板穴埋めを外した**
（ユーザー決定・PR #380）。旧ランクは全て `enabled=false` で、穴埋めは存在しない
7S を呼んで全滅していた（8/30「埋まらなかった21件」）。看板の担保は型ラボ側
（9車の型F を決勝以外も売る・PR #379）へ移した。

したがってここで順序を守るのは**昼・夕の波（`wave_submit_wt.sh`）だけ**。
朝については「もう呼んでいないこと」を `test_daily_batch_no_longer_submits_legacy`
で逆向きに固定する（消したはずのものが復活したら落ちる）。
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


@pytest.mark.parametrize("path", [WAVE], ids=["wave_submit_wt"])
def test_wave_script_calls_marquee(path: Path):
    """各波のスクリプトが看板穴埋めを呼んでいること。"""
    lines = _code_lines(path)
    assert _index_of(lines, "submit_marquee_wt.py") >= 0, (
        f"{path.name} が submit_marquee_wt.py を呼んでいない。"
        "cron の独立エントリへ戻すと、波より先に走って看板を横取りしうる。")


@pytest.mark.parametrize("path", [WAVE], ids=["wave_submit_wt"])
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


@pytest.mark.parametrize("path", [WAVE], ids=["wave_submit_wt"])
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


def test_marquee_receives_the_same_session_as_the_ranks():
    """穴埋めの呼び出しが、**そのシェルがランク入稿へ渡したのと同じ波**を渡すこと。

    🔴 2026-08-19 に意味が反転した検査。それ以前は「セッションを渡さない」ことを
       守っていた（波ラベルは `netkeirin_submissions.session` に残す**記録**でしか
       なく、判定と記録が別経路になるのを避けたかったため）。

       いまは穴埋めが「自分の波の開催しか埋めない」ので、波ラベルは
       **どの開催を埋めるかの判定**そのもの。実行時刻から導かせると、バッチが
       境界（12時 / 18時）を跨いだ日にランクは morning・穴埋めは noon で走り、
       **ナイター開催を穴埋めがランクより先に取る**。実際 session='morning' の
       穴埋めに submitted_at 12:08 の実績がある。

    ⚠️ 壊れても例外は出ない。ずれるのは「バッチが遅れた日」だけなので、
       ふだんは何事も無く動いて見える。

    🔴 2026-08-30 から朝（`daily_picks_wt.sh`）は対象外（旧ランク入稿・穴埋めごと
       外した）。残るのは昼・夕の波だけ。
    """
    for path in (WAVE,):
        lines = _code_lines(path)
        i_m = _index_of(lines, "submit_marquee_wt.py")
        i_r = _index_of(lines, "netkeirin_submit_wt.py")
        # 🔴 `-1` をそのまま添字にすると**スクリプト最後の実行行**を掴み、
        #    「呼び出しが消えた」ことを「波が違う」と誤って報告する。
        assert i_m >= 0, f"{path.name}: 穴埋めの呼び出しが無い"
        assert i_r >= 0, f"{path.name}: ランク入稿の呼び出しが無い"
        marquee, ranks = lines[i_m], lines[i_r]
        assert "--session" in marquee, (
            f"{path.name}: 穴埋めへ --session を渡していない（実行行: {marquee}）")
        # `${SESSION}` と `$SESSION` は同じもの。表記差で通してしまわないよう正規化する。
        norm = lambda line: line.replace("${SESSION}", "$SESSION")  # noqa: E731
        ranks, marquee = norm(ranks), norm(marquee)
        want = next((tok for tok in ("$SESSION", "morning", "noon", "evening")
                     if tok in ranks), None)
        assert want is not None, (
            f"{path.name}: ランク入稿の波が読み取れない（実行行: {ranks}）")
        assert want in marquee, (
            f"{path.name}: ランクは {want} で走るのに穴埋めの波が違う"
            f"（ランク: {ranks} / 穴埋め: {marquee}）")


def test_daily_batch_no_longer_submits_legacy():
    """🔴 朝のバッチが旧ランク入稿・看板穴埋めを**呼ばないこと**（2026-08-30）。

    復活すると、型ラボが取るはずのレースを 1レース1商品の制約で横取りする
    （しかも旧ランクは全て無効なので、横取りした先で何も入稿しない）。
    """
    lines = _code_lines(DAILY)
    assert _index_of(lines, "netkeirin_submit_wt.py") == -1, \
        "朝のバッチに旧ランク入稿が復活しています"
    assert _index_of(lines, "submit_marquee_wt.py") == -1, \
        "朝のバッチに看板穴埋めが復活しています"


def test_daily_batch_runs_the_type_lab():
    """🔴 朝のバッチが型ラボを呼ぶこと（これが無いと当日の商品が出ない）。

    別 cron の時刻合わせにしないのは、型ラボが**当日データ収集の後**でなければ
    動かないため（自前でモデル推論するので `wave-picks-wt` には依存しないが
    `wt_entries` は要る）。実行順で保証する。
    """
    lines = _code_lines(DAILY)
    assert _index_of(lines, "type_lab_daily.sh") >= 0, \
        "朝のバッチが型ラボを呼んでいません"
