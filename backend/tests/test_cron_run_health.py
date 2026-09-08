"""cron ジョブの死活監視を固定する。

なぜ必要か（2026-09-08）:
    統合 Phase 2 で sekito のスクレイプジョブをすべて kiseki の host cron へ移した
    結果、`check_scrape_supply.py` の②が `sekito.script_requests` を見続けて
    **対象 0 件になり、何が失敗しても OK を返す**状態になった。

    ②は「netkeiba-index が毎日 10 分でタイムアウト kill されている」ことを
    **唯一検知していた**チェックだった。作り直したので、その検知力が
    保たれていることをここで固定する。
"""

from __future__ import annotations

from datetime import date

import pytest

from src.utils.cron_run import RunRecord, record

# --------------------------------------------------------------------------
# 記録側
# --------------------------------------------------------------------------

def test_正常終了なら終了コード0(monkeypatch):
    saved: list[RunRecord] = []
    monkeypatch.setattr("src.utils.cron_run._finish", saved.append)
    monkeypatch.setattr("src.utils.cron_run.SyncSessionLocal", _fake_session_factory())

    with record("dummy") as run:
        run.summary = "対象36 成功36"

    assert saved[0].exit_code == 0
    assert saved[0].summary == "対象36 成功36"


def test_例外で抜けたら終了コード1にして再送出する(monkeypatch):
    saved: list[RunRecord] = []
    monkeypatch.setattr("src.utils.cron_run._finish", saved.append)
    monkeypatch.setattr("src.utils.cron_run.SyncSessionLocal", _fake_session_factory())

    with pytest.raises(RuntimeError):
        with record("dummy"):
            raise RuntimeError("失敗")

    assert saved[0].exit_code == 1


def test_記録に失敗してもジョブは落とさない(monkeypatch):
    """🔴 監視のための記録が本体を壊したら本末転倒。"""
    class _Broken:
        def __enter__(self):
            raise RuntimeError("DB に繋がらない")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("src.utils.cron_run.SyncSessionLocal", lambda: _Broken())

    with record("dummy") as run:
        run.summary = "本体は動く"

    assert run.summary == "本体は動く"


def _fake_session_factory():
    class _Result:
        def scalar(self):
            return 1

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **kw):
            return _Result()

        def commit(self):
            pass

    return lambda: _Session()


# --------------------------------------------------------------------------
# 監視側
# --------------------------------------------------------------------------

def _check(rows, target: date):
    """`check_job_failures` をスタブ DB で回して Report を返す。"""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_supply", Path(__file__).parents[1] / "scripts" / "check_scrape_supply.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class _Session:
        def execute(self, *a, **kw):
            class _R:
                def all(self_inner):
                    return rows
            return _R()

    rep = mod.Report()
    mod.check_job_failures(_Session(), target, rep)
    return rep


# 穴ぐさとパドックは土日月のみ走る。曜日で挙動が変わるので実日付で固定する。
MONDAY = date(2026, 9, 7)    # weekday=0 月
TUESDAY = date(2026, 9, 8)   # weekday=1 火 — 穴ぐさは走らない日


def test_期待したジョブが1回も起動していなければWARN():
    """🔴 「失敗が無い」だけを見ると、起動しなかった日に必ず OK を返す。

    旧②はまさにそれで、切替後に対象 0 件になっても OK を返し続けていた。
    """
    rep = _check([], MONDAY)
    assert any("未実行" in w for w in rep.warns), rep.warns
    # 警告文は人が読む説明（ラベル）を出す。ジョブ名だけだと何が止まったか伝わらない
    assert any("netkeiba タイム指数" in w for w in rep.warns), rep.warns
    assert any("吉馬" in w for w in rep.warns), rep.warns
    assert any("穴ぐさ" in w for w in rep.warns), rep.warns


def test_終了記録が無ければWARN():
    """途中で死んだ場合。netkeiba-index の 10 分タイムアウト kill がこれ。"""
    rows = [("scrape_netkeiba_index", 1, 0, 1, None),
            ("scrape_kichiuma", 2, 0, 0, "対象36 成功36"),
            ("scrape_anagusa", 1, 0, 0, "ピック55件")]
    rep = _check(rows, MONDAY)
    assert any("終了記録なし" in w and "scrape_netkeiba_index" in w for w in rep.warns), rep.warns


def test_異常終了はWARN():
    rows = [("scrape_netkeiba_index", 1, 1, 0, None),
            ("scrape_kichiuma", 2, 0, 0, None),
            ("scrape_anagusa", 1, 0, 0, None)]
    rep = _check(rows, MONDAY)
    assert any("ジョブ失敗" in w and "scrape_netkeiba_index" in w for w in rep.warns), rep.warns


def test_全部正常ならWARNなし():
    rows = [("scrape_netkeiba_index", 1, 0, 0, "対象79 指数成功79"),
            ("scrape_kichiuma", 2, 0, 0, "対象79 成功79"),
            ("sync_sekito_races", 1, 0, 0, "kaisai10 JRA0 NAR126"),
            ("scrape_anagusa", 1, 0, 0, "ピック55件")]
    rep = _check(rows, MONDAY)
    assert rep.warns == [], rep.warns


def test_走らない曜日のジョブは未実行を咎めない():
    """穴ぐさは土日月のみ。火曜に無くても異常ではない。"""
    rows = [("scrape_netkeiba_index", 1, 0, 0, None),
            ("scrape_kichiuma", 2, 0, 0, None),
            ("sync_sekito_races", 1, 0, 0, None)]
    rep = _check(rows, TUESDAY)
    assert rep.warns == [], rep.warns


def test_供給同期が止まったら気づける():
    """🔴 2026-09-07 に「不要」と判断して止め、9/9 まで気づけなかった穴。

    sekito.races が凍ると sekito のサイトと POG の出走通知が静かに空になる。
    例外は出ないので、監視の「当日 1 回も起動していない」だけが網になる。
    """
    rows = [("scrape_netkeiba_index", 1, 0, 0, None),
            ("scrape_kichiuma", 2, 0, 0, None),
            ("scrape_anagusa", 1, 0, 0, None)]
    rep = _check(rows, MONDAY)
    assert any("sync_sekito_races" in w or "レース供給" in w for w in rep.warns), rep.warns


def test_期待リストに無いジョブの異常も拾う():
    """埋め戻しのような一時的なジョブも、失敗すれば見えるようにする。"""
    rows = [("scrape_netkeiba_index", 1, 0, 0, None),
            ("scrape_kichiuma", 2, 0, 0, None),
            ("scrape_anagusa", 1, 0, 0, None),
            ("backfill_netkeiba:time_index", 1, 1, 0, None)]
    rep = _check(rows, MONDAY)
    assert any("backfill_netkeiba" in w for w in rep.warns), rep.warns


# --------------------------------------------------------------------------
# 記録しない実行
# --------------------------------------------------------------------------

def test_試し打ちや件数確認は記録対象から外れている():
    """🔴 `--dry-run` / `--count` / `--status` を記録すると監視が鈍る。

    手で叩いた回数が cron_runs に残ると、監視から見て「今日はジョブが走った」
    ことになり、**本物の未実行を見逃す**。②の 1 番目のチェック
    （1 回も起動していない）が効かなくなる。

    2026-09-08 に実際に `--count` の実行が summary なしで記録されていたので直した。
    """
    from pathlib import Path

    scripts = Path(__file__).parents[1] / "scripts"
    targets = {
        "scrape_netkeiba_index.py": "args.dry_run",
        "scrape_netkeiba_paddock.py": "args.dry_run or args.list",
        "scrape_anagusa.py": "args.dry_run",
        "scrape_kichiuma.py": "args.dry_run",
        "check_ip_restriction.py": "args.status or args.check",
        "backfill_netkeiba.py": "args.count or args.dry_run",
    }
    for name, cond in targets.items():
        text = (scripts / name).read_text(encoding="utf-8")
        assert "nullcontext" in text, f"{name} に記録スキップが無い"
        for token in cond.replace(" or ", " ").split():
            assert token in text, f"{name} の条件に {token} が無い"
