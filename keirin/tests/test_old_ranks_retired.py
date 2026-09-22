"""旧ランクが**本番の経路から消えている**ことを機械的に固定する（2026-09-22）。

## 背景

旧ランク16種（7S/7A/7B/7SS/7C/7M1/7H1/7H2/7T1/7T3/9S/9A/9C/9H1/S1…）は
2026-08-28 を最後に1件も入稿していない（`netkeirin_settings.enabled` が全て
false）。それでも候補生成・再構築・採点は毎日走り続けており、実測（2026-09-22・
VPS）で **1日あたり約2時間**を使っていた:

    07:00 wave-picks-wt                     7分16秒
    08:40 reconcile_walkforward_tail.sh     1時間37分
    16:00 evening_picks_wt.sh               十数分
    毎分  notify_prerace_wt.py              2026-08-28 以降は無出力

「9月も売っていたら高額が出ていたのでは」は測って否定してある（同日）:
候補のままだと 10万+ が8件に見えるが、1レース1商品の優先順位を当てると3件、
入稿ゲート（遡って適用できない）がさらに削る。同じ9月の型ラボ実売は
10万+ 7件・30万+ 1件（最高 713,880円）で、旧ランクは仮想の1,001商品でも
10万+ 3件・30万+ 0件（最高 255,480円）。旧ランクの**実売**1,050商品
（2026-07-24〜08-28）でも 10万+ は2件だけ。
詳細: `docs/type_lab/old_rank_revival_2026_09_11.md`

## この検査が守るもの

🔴 **「止めた」は放っておくと戻る。** 旧ランクの呼び出しは失敗しても
   `|| echo ...（継続）` で握り潰す設計なので、**復活しても誰も気づかない**
   （重くなるだけで例外もログも出ない）。だから文字列で固定する。
🔴 **出走表の指数の書き手は1つだけ。** 旧経路（`wave-picks-wt`）は `lgbm_wt`、
   現行（型ラボ）は `lgbm_wt_eval` と**別のモデル**なので、2つあると
   手元の実行で今日の画面の値が静かに入れ替わる。
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: cron から毎日走る本番の経路。ここに旧ランクが居てはいけない。
PRODUCTION_BATCHES = (
    "daily_picks_wt.sh",
    "previous_day_wt.sh",
    "intraday_results_wt.sh",
    "type_lab_daily.sh",
    "type_lab_wave.sh",
    "type_lab_settle.sh",
)

#: 旧ランク専用の実行体（過去分析の台としてファイルは残す）。
RETIRED_ENTRYPOINTS = (
    "wave-picks-wt",
    "write_candidates_wt.py",
    "reselect_7s_evening.py",
    "notify_results_wt.py",
    "monitor_wide_wt.py",
    "notify_prerace_wt.py",
    "build_7h1_candidates.py",
    "build_7h2_candidates.py",
    "build_9h1_candidates.py",
    "build_7t1_candidates.py",
    "build_7t3_candidates.py",
    "list_deferred_races_wt.py",
)

#: 手で回すときだけ許す退役バッチ。
GUARDED_SCRIPTS = (
    "evening_picks_wt.sh",
    "reconcile_walkforward_tail.sh",
    "backfill_missing_prerace_wt.py",
)


def _live_lines(name: str) -> list[str]:
    """コメントと空行を除いた実行行。"""
    text = (REPO / "scripts" / name).read_text(encoding="utf-8")
    return [ln for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


@pytest.mark.parametrize("batch", PRODUCTION_BATCHES)
def test_production_batches_do_not_call_old_ranks(batch: str) -> None:
    lines = _live_lines(batch)
    for token in RETIRED_ENTRYPOINTS:
        hit = [ln for ln in lines if token in ln]
        assert not hit, (
            f"{batch} が旧ランクの {token} を呼んでいます（2026-09-22 に破棄）: {hit}")


@pytest.mark.parametrize("name", GUARDED_SCRIPTS)
def test_retired_scripts_require_explicit_opt_in(name: str) -> None:
    """cron へ戻しても**そのままでは動かない**こと。"""
    text = (REPO / "scripts" / name).read_text(encoding="utf-8")
    assert "KEIRIN_ALLOW_OLD_RANKS" in text, (
        f"{name} に手動実行ガードがありません（cron へ戻すと黙って再開します）")


def test_only_one_live_writer_of_index_pct() -> None:
    """`wt_entries.pred_*_pct` を書く本番経路がちょうど1つであること。

    もう1つ `backfill_index_pct_wt.py` があるが、あちらは**過去の月**を
    月次 vintage モデルで埋める手動スクリプトで、当日は書かない。
    """
    writers = sorted(
        p.relative_to(REPO).as_posix()
        for p in list((REPO / "src").rglob("*.py")) + list((REPO / "scripts").rglob("*.py"))
        if "UPDATE wt_entries SET pred_win_pct" in p.read_text(encoding="utf-8")
    )
    assert writers == ["scripts/backfill_index_pct_wt.py",
                       "scripts/build_type_lab_picks.py"], writers


def test_morning_batch_writes_index_pct() -> None:
    """朝バッチが出走表の指数を書くこと。

    🔴 旧ランクを止めるとき**これを移し忘れると画面から率が消える**。
       止めたこと自体はログに出ないので、気づくのは利用者が見たとき。
    """
    src = (REPO / "scripts" / "type_lab_morning.py").read_text(encoding="utf-8")
    assert "predict_index_pct" in src and "save_index_pct" in src
    daily = (REPO / "scripts" / "type_lab_daily.sh").read_text(encoding="utf-8")
    assert "scripts/type_lab_morning.py" in daily


def test_day_features_are_built_once_per_process() -> None:
    """特徴量がプロセス内で使い回されること（朝バッチの所要を決める）。

    🔴 ここが効かないと `build_features_wt`（実測 約6分50秒）が
       7車・9車・型・指数で4回走り、朝バッチが 25分を超える。
    """
    src = (REPO / "scripts" / "build_type_lab_picks.py").read_text(encoding="utf-8")
    assert "_FEATURE_CACHE" in src, "特徴量のキャッシュが無い"
    assert "def build_day_features" in src
    # 生の呼び出しは build_day_features の中だけ（他から直接呼ぶとキャッシュを外れる）
    assert src.count("build_features_wt(load_raw_data_wt(") == 1, (
        "build_features_wt を直接呼んでいる箇所が複数あります"
        "（キャッシュを迂回して特徴量を作り直します）")
