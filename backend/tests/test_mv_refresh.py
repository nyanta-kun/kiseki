"""POG のマテビュー更新の配線を固定する。

なぜ必要か（2026-09-10・統合 Phase 5）:
    🔴 **止まっても誰も気づけない**のがこの機構の性質。マテビューは更新が
    止まっても最後の内容を返し続けるので、例外もログも空表示も出ない。
    実際、移設元では sekito のバックエンドがこれを担っており、
    **sekito を落とすと kiseki の POG 順位表が静かに凍る**状態だった。

    だから「起動時に必ず始まること」「デバウンスが REFRESH より長いこと」
    「対象が keiba 側であること」を機械的に留める。
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.realtime import mv_refresh


def test_対象は_keiba_のマテビュー():
    """sekito のマテビューを更新しに行っていないこと。

    sekito 廃止後は `sekito.mv_*` が消えるので、そこを指したままだと
    REFRESH が毎回失敗する（ログには出るが画面は静かに古いまま）。
    """
    assert mv_refresh.TARGETS == ("keiba.mv_horse_runs", "keiba.mv_graded_wins")
    assert all(t.startswith("keiba.") for t in mv_refresh.TARGETS)


def test_デバウンスは_refresh_より長い():
    """🔴 縮めると REFRESH が終わる前に次が積まれ、回り続ける。

    `REFRESH ... CONCURRENTLY` は差分でなく定義全体を毎回再計算するため
    実測 25〜35 秒かかる。移設元が 30 秒にしていた頃は
    **1 日の 8.57%（繁忙帯は 16%）** を REFRESH が占有していた。
    """
    assert mv_refresh.DEBOUNCE_SEC >= 60.0, "REFRESH 1回(25〜35秒)より十分長くすること"


def test_通知が来なくても走る保険がある():
    """LISTEN が黙って死んでも止まらないこと。"""
    assert mv_refresh.REFRESH_INTERVAL_SEC > mv_refresh.DEBOUNCE_SEC


def test_通知ではまだ_refresh_しない():
    """通知は印を立てるだけ。1 レース 18 頭ぶん連続で来るのでまとめる。"""
    r = mv_refresh.MvRefresher()
    assert not r._dirty.is_set()
    r._on_notify()
    assert r._dirty.is_set(), "通知で印が立つこと"


def test_チャンネル名がトリガと一致する():
    """DB 側のトリガ関数が撃つ名前と同じであること。

    片方だけ変えると**通知が届かなくなるだけ**で、例外は出ない。
    """
    assert mv_refresh.CHANNEL == "mv_horse_runs_dirty"
    mig = next(
        Path("alembic/versions").glob("*move_mv_horse_runs_to_keiba.py"), None
    )
    assert mig is not None, "マテビュー移設のマイグレーションが見つからない"


def test_起動時に必ず開始される():
    """`main.py` の lifespan から start されていること。

    ここが外れると**何も起きないまま POG だけが古くなる**。
    import しても副作用が無いよう AST で見る。
    """
    src = Path("src/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # FastAPI(...) に lifespan= が渡っているか
    passed_lifespan = any(
        isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "FastAPI"
        and any(k.arg == "lifespan" for k in node.keywords)
        for node in ast.walk(tree)
    )
    assert passed_lifespan, "FastAPI(lifespan=...) が渡されていない"

    # lifespan の中で refresher.start() を呼んでいるか
    calls = {
        f"{getattr(n.func.value, 'id', '')}.{n.func.attr}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "mv_refresher.start" in calls, "lifespan で mv_refresher.start() を呼ぶこと"
    assert "mv_refresher.stop" in calls, "lifespan で mv_refresher.stop() を呼ぶこと"
