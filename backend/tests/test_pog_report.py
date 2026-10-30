"""POG 週報（`pog_report` / `notify_pog_report`）のテスト。"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from src.services import pog_report as pr  # noqa: E402

# 2026-10-30 に取得した掲示板 API の応答（形だけ残して短くしたもの）
BBS_PAYLOAD = {
    "status": "OK",
    "data": {
        "count": "1629",
        "list": [
            {
                "nickname": "a",
                "comment": "次走は東スポ杯に向かうらしい",
                "datetime": "2026/10/29 7:56",
                "is_hidden_comment": "0",
            },
            {"nickname": "b", "comment": "左前の骨折が判明", "datetime": "2026/10/28 22:41", "is_hidden_comment": "0"},
            {"nickname": "c", "comment": "非表示の投稿", "datetime": "2026/10/28 20:00", "is_hidden_comment": "1"},
            {"nickname": "d", "comment": "ずっと前の話", "datetime": "2026/09/01 10:00", "is_hidden_comment": "0"},
        ],
    },
}

# 2026-10-30 に取得した「次走・近況情報」の本文（タグを落とした後）
STATUS_PLAN = (
    "次走情報 近況情報 2026/11/23(月) 東京スポーツ杯２歳Ｓ "
    "※netkeiba独自の想定のため、公式発表と異なる場合がございます netkeiba さん(66905) 26/10/09"
)


class TestBbs:
    def test_classify(self) -> None:
        assert pr.classify_bbs("左前の骨折が判明") == "故障"
        assert pr.classify_bbs("次走は東スポ杯") == "次走"
        assert pr.classify_bbs("坂路で一杯に追われた") == "調教"
        assert pr.classify_bbs("かわいい") == ""

    def test_kaihi_is_not_injury(self) -> None:
        """「回避」だけで故障にしない（2026-10-30 の試走で 2 件とも誤判定だった）。"""
        assert pr.classify_bbs("ルメール騎手が海外に行くなら回避でも構わない") != "故障"
        assert pr.classify_bbs("シグレ回避は痛い") != "故障"

    def test_parse_payload_skips_hidden(self) -> None:
        posts = pr.parse_bbs_payload(BBS_PAYLOAD)
        assert [p.user for p in posts] == ["a", "b", "d"]
        assert posts[0].posted_at == datetime(2026, 10, 29, 7, 56)

    @pytest.mark.parametrize("payload", [None, "html", {"data": "x"}, {"data": {"list": "x"}}])
    def test_parse_payload_tolerates_other_shapes(self, payload: object) -> None:
        assert pr.parse_bbs_payload(payload) == []

    def test_recent_and_summary(self) -> None:
        posts = pr.recent_posts(pr.parse_bbs_payload(BBS_PAYLOAD), datetime(2026, 10, 30, 20, 30), 7)
        assert len(posts) == 2
        s = pr.summarize_bbs(posts)
        assert s is not None
        # 新しい「次走」より重要度の高い「故障」を採る
        assert (s.count, s.label, s.when) == (2, "故障", "10/28")
        assert pr.summarize_bbs([]) is None

    def test_clean_keeps_wording(self) -> None:
        assert pr.clean_bbs(">>12   骨折  です") == "骨折 です"
        assert pr.clean_bbs("あ" * 200, limit=10) == "あ" * 9 + "…"


class TestStatus:
    def test_plan(self) -> None:
        s = pr.parse_status(STATUS_PLAN)
        assert s.plan == "2026/11/23(月) 東京スポーツ杯２歳Ｓ"
        assert s.state == "" and s.entry == ""

    def test_rest_and_back(self) -> None:
        s = pr.parse_status("休養のため放牧 26/09/25")
        assert (s.state, s.note, s.since) == ("放牧", "放牧中(9/25〜)", date(2026, 9, 25))
        s = pr.parse_status("休養のため放牧 26/08/01 放牧から帰厩 26/10/01")
        assert (s.state, s.since) == ("帰厩", date(2026, 10, 1))

    def test_entry(self) -> None:
        s = pr.parse_status("2026/11/08(日) 京王杯２歳Ｓ 上記レースに特別登録")
        assert s.state == "出走" and "特別登録" in s.entry

    def test_reconcile_drops_stale_rest(self) -> None:
        """放牧日より後に走っていたら放牧扱いをやめる（帰厩が書き込まれない馬がいる）。"""
        rest = pr.parse_status("休養のため放牧 26/09/25")
        assert pr.reconcile_status(rest, date(2026, 10, 24)).state == ""
        assert pr.reconcile_status(rest, date(2026, 9, 1)).state == "放牧"
        assert pr.reconcile_status(rest, None).state == "放牧"


def _entry(horse_id: str, day: date, src: str, time: str = "10:00") -> pr.EntryRow:
    return pr.EntryRow(
        day=day,
        horse_id=horse_id,
        horse=f"馬{horse_id}",
        owner="松",
        place="東京1R",
        race="2歳未勝利",
        cond="芝1600m",
        time=time,
        jockey="",
        src=src,
    )


def test_merge_prefers_confirmed() -> None:
    """同じ馬・同じ日は出馬表を採り、水曜の想定と二重に載せない。"""
    sat = date(2026, 10, 31)
    merged = pr.merge_entries([_entry("1", sat, "fix", "12:00")], [_entry("1", sat, "nk"), _entry("2", sat, "nk")])
    assert [(e.horse_id, e.src) for e in merged] == [("2", "nk"), ("1", "fix")]


def _report(**kw: object) -> pr.Report:
    base = dict(
        title="赤兎週報",
        subtitle="2026年度 赤兎POG",
        issued_at=datetime(2026, 10, 28, 20, 30),
        horses=[],
        this_week=[],
        last_week=[],
        standings=[],
        bbs_days=7,
    )
    base.update(kw)
    return pr.Report(**base)  # type: ignore[arg-type]


def test_html_escapes_bbs_text() -> None:
    """掲示板の原文をそのまま載せるので、HTML は必ずエスケープする。"""
    h = pr.HorseInfo(
        horse_id="1",
        name="テスト",
        owner="松",
        bbs=pr.BbsSummary(count=1, label="故障", when="10/28", text="<script>x</script>"),
    )
    html = pr.build_html(_report(horses=[h]))
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
    assert "赤兎週報" in html


def test_discord_content() -> None:
    sat = date(2026, 10, 31)
    h = pr.HorseInfo(
        horse_id="1", name="骨折馬", owner="渡", bbs=pr.BbsSummary(count=1, label="故障", when="10/28", text="骨折")
    )
    text = pr.build_discord_content(_report(horses=[h], this_week=[_entry("2", sat, "nk")]))
    assert text.startswith("📰 **赤兎週報**")
    assert "（想定）" in text
    assert "骨折馬［渡］" in text
    many = [_entry(str(i), sat, "fix") for i in range(200)]
    assert len(pr.build_discord_content(_report(this_week=many))) <= 1900


class TestSendThenRecord:
    """🔴 送信が成功してから記録する（先に記録すると失敗した号が二度と送られない）。"""

    def _run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sent_ok: bool) -> tuple[str, list]:
        import notify_pog_report as m

        recorded: list = []
        monkeypatch.setattr(m.io, "already_sent", lambda *a: False)
        monkeypatch.setattr(m, "build_report", lambda *a: _report())

        def fake_pdf(html: str, out: Path) -> Path:
            out.write_bytes(b"%PDF-1.4")
            return out

        monkeypatch.setattr(m.io, "render_pdf", fake_pdf)
        monkeypatch.setattr(m.io, "send_discord_file", lambda *a: sent_ok)
        monkeypatch.setattr(m.io, "record_sent", lambda *a: recorded.append(a))
        group = {"id": 22, "year": 2026, "name": "赤兎", "discord_webhook_url": "https://x"}
        status = m.run_group(None, group, m.Options(out=tmp_path / "a.pdf"), datetime(2026, 10, 28, 20, 30))
        return status, recorded

    def test_failed_send_is_not_recorded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        status, recorded = self._run(monkeypatch, tmp_path, sent_ok=False)
        assert status == "failed" and recorded == []

    def test_successful_send_is_recorded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        status, recorded = self._run(monkeypatch, tmp_path, sent_ok=True)
        assert status == "sent" and len(recorded) == 1


class TestWorkouts:
    def _w(self, day: date, t4: float, place: str = "美浦坂路") -> pr.Workout:
        return pr.Workout(day=day, place=place, distance="4F", times=(t4, 40.0, 26.0, 12.9), time_4f=t4)

    def test_window_and_order(self) -> None:
        """レース前 14 日・新しい順・最大 3 本。レース当日以降は載せない。"""
        race = date(2026, 10, 31)
        ws = [self._w(date(2026, 10, d), 55.0) for d in (10, 17, 25, 27, 28, 31)]
        picked = pr.pick_workouts(ws, race)
        assert [w.day.day for w in picked] == [28, 27, 25]
        assert pr.pick_workouts([self._w(date(2026, 10, 16), 55.0)], race) == ()

    def test_fastest_by_4f(self) -> None:
        ws = (self._w(date(2026, 10, 28), 56.0), self._w(date(2026, 10, 25), 54.5))
        best = pr.fastest(ws)
        assert best is not None and best.day.day == 25
        assert pr.fastest(()) is None

    def test_label(self) -> None:
        w = pr.Workout(
            day=date(2026, 10, 28), place="美浦W", distance="6F", times=(84.8, 67.7, 52.7, 38.6, 11.8), time_4f=52.7
        )
        assert w.label() == "10/28 美浦W 6F 84.8-67.7-52.7-38.6-11.8"

    def test_wood_place(self) -> None:
        """トレセン 0=美浦 / 1=栗東（厩舎の所属で確認済み）。"""
        assert pr.wood_place("0", "3") == "美浦W"
        assert pr.wood_place("1", "2") == "栗東CW"
        assert pr.wood_place("1", "4") == "栗東ウッド"

    def test_card_shows_workouts(self) -> None:
        e = _entry("1", date(2026, 10, 31), "fix")
        bare = pr.build_html(_report(this_week=[e]))
        assert "直近の時計なし" in bare
        from dataclasses import replace

        ws = (self._w(date(2026, 10, 28), 56.0), self._w(date(2026, 10, 25), 54.5))
        html = pr.build_html(_report(this_week=[replace(e, workouts=ws)]))
        assert '<span class="wk wk-b">10/25 美浦坂路 4F 54.5' in html
