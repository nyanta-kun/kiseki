"""後の波の開催を朝へ前倒しして入稿する経路の検査（2026-08-21 新設）。

波（`src/meeting_wave.py`）は「三連複の板が育つのを待つ」ために作った。その前提は
失効している——賭け金の配分は `landing_weights` が**予測オッズを最優先で単独採用**
するようになり、実測でも 2026-08-12 以降の夜の波の入稿は noon 34/34・
evening 67/67 が `predicted` で**板由来は1件も無い**。予想そのものは朝に当日
全開催ぶん出来ている（`wave_submit_wt.sh` は入稿だけを行う）。

ここで固定するのは次の3点:

1. 前倒しできるのは**三連複**の買い目だけ（三連単はダッチ配分に実際の板が要る）
2. 前倒しできるのは**予測オッズが買う点すべてに作れるレース**だけ
3. 前倒しを見送ったレースは `deferred_races` に入り、**下位ランクにも取らせない**
   （netkeirin は1レース1商品なので、下位が朝に取ると上位が自分の波で取れない）
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    """`scripts/netkeirin_submit_wt.py` を副作用なしで読む。"""
    spec = importlib.util.spec_from_file_location(
        "netkeirin_submit_wt_for_test", ROOT / "scripts" / "netkeirin_submit_wt.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def test_dutch_trifecta_is_pulled_forward_when_the_tf_board_exists(mod, monkeypatch):
    """ダッチ配分する三連単（7H1 / 7H2 / 9H1）は**三連単の予測盤面**で判定する。

    🔴 2026-08-26 に規則が変わった。それまでは「三連単は板でダッチするので
       前倒し不可」——朝は三連単の板がまず無く、揃わないと均等へ落ちて
       **券種の形が波によって変わる**からだった。いまは
       `src.odds_prediction_tf`（7車）で配分するので、盤面さえ作れれば
       朝でも形は変わらない。
    ⚠️ 9車は三連単の予測モデルが無い＝空 → 従来どおり自分の波まで待つ。
    """
    called = []
    monkeypatch.setattr(mod, "try_predicted_odds_for_legs",
                        lambda *a, **k: called.append(a) or {1: 5.0})
    monkeypatch.setattr(mod, "_predicted_tf_fill", lambda rk: {(1, 2, 3): 50.0})
    assert mod._can_pull_forward("20260821_46_05", True, 1, 2, [3, 4, 5]) is True
    assert not called, "三連単なら三連複の予測オッズは引かないはず"

    monkeypatch.setattr(mod, "_predicted_tf_fill", lambda rk: {})
    assert mod._can_pull_forward("20260821_46_05", True, 1, 2, [3, 4, 5]) is False


def test_pull_forward_requires_predicted_odds_for_every_leg(mod, monkeypatch):
    """買う点が1つでも欠けたら前倒ししない。

    一部だけ予測で埋めると、欠けた点の重みを別尺度で決めることになり比率が壊れる
    （`stake_allocation._usable_odds` と同じ思想）。
    """
    monkeypatch.setattr(mod, "try_predicted_odds_for_legs",
                        lambda rk, a1, a2, legs: {3: 8.0, 4: 9.0})
    assert mod._can_pull_forward("20260821_46_05", False, 1, 2, [3, 4, 5]) is False

    monkeypatch.setattr(mod, "try_predicted_odds_for_legs",
                        lambda rk, a1, a2, legs: {t: 8.0 for t in legs})
    assert mod._can_pull_forward("20260821_46_05", False, 1, 2, [3, 4, 5]) is True


def test_pull_forward_false_when_no_partners(mod, monkeypatch):
    """相手が空なら判定できない＝前倒ししない（自分の波へ残す）。"""
    monkeypatch.setattr(mod, "try_predicted_odds_for_legs",
                        lambda *a, **k: {})
    assert mod._can_pull_forward("20260821_46_05", False, 1, 2, []) is False


def test_deferred_races_are_excluded_from_later_ranks(mod):
    """見送ったレースを下位ランクが横取りしない（優先順位の保護）。

    RANK_ORDER の上位が「前倒しできない」と判断したレースを下位ランクが朝に
    取ると、13:00 / 18:00 に上位が来ても**1レース1商品**で取れなくなる。
    `_process_rank` は `deferred_races` を候補から外すこと。
    """
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    assert "if deferred_races:" in src, "deferred_races で候補を外していない"
    assert "deferred_races.add(base_key)" in src, "見送りを deferred_races へ記録していない"
    # main() から共有の集合が渡されていること（ランク間で共有されないと意味が無い）
    assert "deferred_races: set[str] = set()" in src
    assert "deferred_races=deferred_races" in src


def test_marquee_fill_uses_the_same_rule():
    """看板穴埋めも同じ規則で前倒しする。

    穴埋めの買い目は 7S / 9C（`RANK_BY_CARS`）＝どちらも三連複なので、条件は
    「予測オッズの盤面を作れるか」の1つだけ。ここが波固定のままだと、
    **ランクは朝に出るのに看板だけ夜まで出ない**という食い違いが起きる。
    """
    src = (ROOT / "scripts" / "submit_marquee_wt.py").read_text(encoding="utf-8")
    assert "_can_pull_forward" in src
    assert "predicted_trio_board" in src
