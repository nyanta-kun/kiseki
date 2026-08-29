"""Discord の件数と確認画面の件数を一致させる（2026-08-23・実害の回帰）。

## 実際に起きたこと

2026-08-23 朝の Discord:

    [netkeirin入稿案] 2026-08-23（午前）: 7S6件・9C8件・7C3件・7T1 3件・7M1 5件（計25件）

同時刻の `/keirin/review`: **45件**。

DB を見ると内訳は

    origin='rank'         25件（7S6 / 9C8 / 7C3 / 7T1 3 / 7M1 5）  ← Discord の数
    origin='marquee_fill' 20件（7S18 / 9C2）                        ← どこにも出ていない

原因は**実行順**。`netkeirin_submit_wt.py` が通知を送った**後**に
`submit_marquee_wt.py` が走るので、穴埋めぶんが件数に入らない。
さらに穴埋め側は承認制のとき1通も送らない設計（2026-08-14 のユーザー判断）だった
ため、20件が Discord に現れる経路が**どこにも無かった**。

## 直し方の方針

🔴 **通知は1通のまま**（2026-08-14 の判断を変えない）。増やすのではなく、
   既にある1通の件数を正しくする。ランク入稿は `--defer-notify` で集計を
   JSON へ保留し、穴埋めが自分の件数を足して送る。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import submit_marquee_wt  # noqa: E402


def _notice(tmp: Path, **over) -> str:
    p = tmp / "notice.json"
    payload = dict(target_date="2026-08-23", session_jp="午前", propose_only=True,
                   breakdown={"7S": 6, "9C": 8, "7C": 3, "7T1": 3, "7M1": 5},
                   total=25, failures=[])
    payload.update(over)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _send(path: str, done: list[str], failed: list[str] | None = None):
    sent: list[str] = []
    with patch("src.notify.discord.send",
               side_effect=lambda c, channel=None: sent.append(c)):
        ok = submit_marquee_wt._send_merged_notice(
            path, "2026-08-23", done, failed or [])
    return ok, sent


def test_total_includes_marquee_fill(tmp_path):
    """🔴 **これが本体**。25 + 20 = 45 が出ること。"""
    done = [f"立川{i}R(7S)" for i in range(18)] + ["松山1R(9C)", "松山2R(9C)"]
    ok, sent = _send(_notice(tmp_path), done)
    assert ok and len(sent) == 1, "1通だけ送ること"
    assert "計45件" in sent[0], f"確認画面の件数と一致しない: {sent[0]}"
    assert "看板穴埋め" in sent[0], "穴埋めぶんが内訳に出ていない"
    assert "7S18件" in sent[0] and "9C2件" in sent[0]


def test_still_one_message(tmp_path):
    """🔴 通知を増やさない（2026-08-14 のユーザー判断）。"""
    _, sent = _send(_notice(tmp_path), ["立川3R(7C)"])
    assert len(sent) == 1


def test_wording_stays_proposal_under_approval(tmp_path):
    """承認制では「入稿案」であって「自動入稿」ではない。"""
    _, sent = _send(_notice(tmp_path), ["立川3R(7C)"])
    assert "[netkeirin入稿案]" in sent[0]
    assert "自動入稿" not in sent[0]
    assert "承認するまで netkeirin へは出ません" in sent[0]


def test_failures_from_both_sides_are_merged(tmp_path):
    path = _notice(tmp_path, failures=["西武園11R(7H1): 別ランクが同じレースを入稿済みのためスキップ"])
    _, sent = _send(path, ["立川3R(7C)"], ["高知5R(9C)"])
    assert "入稿失敗 2件" in sent[0]
    assert "看板穴埋め" in sent[0]


def test_missing_file_falls_back(tmp_path):
    """⚠️ 保留ファイルが無い日（手動実行・ランク入稿が落ちた日）は従来経路へ。"""
    ok, sent = _send(str(tmp_path / "none.json"), ["立川3R(7C)"])
    assert ok is False and sent == []


def test_notice_is_deleted_after_send(tmp_path):
    """🔴 消さないと翌日また同じ内容を送る。"""
    p = _notice(tmp_path)
    _send(p, ["立川3R(7C)"])
    assert not Path(p).exists()


def test_zero_fill_still_sends_rank_notice(tmp_path):
    """穴埋めが0件でも、保留した通知は必ず送る（送らないと1通も出ない）。"""
    ok, sent = _send(_notice(tmp_path), [])
    assert ok and len(sent) == 1
    assert "計25件" in sent[0]
    assert "看板穴埋め" not in sent[0]


def test_rank_of_parses_label():
    assert submit_marquee_wt._rank_of("立川3R(7C)") == "7C"
    assert submit_marquee_wt._rank_of("こわれた") == ""


def test_shell_passes_the_same_path_to_both():
    """🔴 シェルが**同じパス**を両方へ渡していること。

    片方だけだと保留したまま誰も送らず、Discord が沈黙する。
    """
    # 🔴 朝（daily_picks_wt.sh）は 2026-08-30 に旧ランク入稿・看板穴埋めごと
    #    外したので対象外（PR #380）。残るのは昼・夕の波だけ。
    for name in ("wave_submit_wt.sh",):
        src = (REPO / "scripts" / name).read_text(encoding="utf-8")
        assert "NOTICE_JSON=" in src, f"{name}: 一時ファイルの定義が無い"
        assert src.count('--defer-notify "$NOTICE_JSON"') == 2, (
            f"{name}: ランク入稿と看板穴埋めの両方へ渡すこと")


def test_writer_and_reader_agree_on_payload_keys(tmp_path):
    """🔴 **書き手と読み手の受け渡しを固定する。**

    `netkeirin_submit_wt._write_deferred_notice` が書いた JSON を
    `submit_marquee_wt._send_merged_notice` がそのまま読めること。
    ここが食い違っても**例外は出ず、件数だけが静かに間違う**（今回の実害と同じ型）。
    """
    from scripts.netkeirin_submit_wt import _write_deferred_notice

    p = tmp_path / "roundtrip.json"
    _write_deferred_notice(str(p), dict(
        target_date="2026-08-23", session_jp="午前", propose_only=True,
        breakdown={"7S": 6, "9C": 8}, total=14, failures=["西武園11R(7H1): 重複"]))
    ok, sent = _send(str(p), ["立川1R(7S)", "立川2R(7S)"])
    assert ok and len(sent) == 1
    assert "計16件" in sent[0]          # 14 + 2
    assert "7S6件" in sent[0] and "9C8件" in sent[0]
    assert "看板穴埋め 7S2件" in sent[0]
    assert "2026-08-23（午前）" in sent[0]
    assert "入稿失敗 1件" in sent[0]


def test_deferred_notice_write_failure_does_not_raise(tmp_path):
    """🔴 通知の保留に失敗しても入稿は落とさない。"""
    from scripts.netkeirin_submit_wt import _write_deferred_notice

    # 書けない場所を渡す（ディレクトリを指定）
    _write_deferred_notice(str(tmp_path), dict(total=1))
