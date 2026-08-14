"""RANK_7H1（穴推奨・本命バスト型）の不変条件テスト。

守りたいのは3つ:
  1. **購入額が必ず 10,000円以下・100円単位**（実購入の制約）
  2. **本命ラインを買い目から必ず落とす**（本命が飛ぶとき番手も共倒れするため）
  3. **選別が絶対閾値で行われる**（日次の相対順位に戻すと件数が半減する）
"""
from __future__ import annotations

import pytest

from src.preprocessing import favbust_features as ff
from src import strategy_wt as sw


def _entry(frame, line_group, line_pos, is_leader, line_size, rp, style="逃"):
    return {"frame_no": frame, "line_group": line_group, "line_pos": line_pos,
            "is_line_leader": is_leader, "line_size": line_size,
            "race_point": rp, "style": style, "prediction_mark": None,
            "n_lines": 3, "player_class": "S級", "prefecture": "東京",
            "pred_win_pct": 10.0, "pred_top3_pct": 30.0}


@pytest.fixture
def board():
    """本命=1（ライン先頭・A）／番手=2／3番手=3、別ラインB(4,5)・C(6)、単騎7。"""
    return [
        _entry(1, "A", 1, 1, 3, 110.0), _entry(2, "A", 2, 0, 3, 105.0),
        _entry(3, "A", 3, 0, 3, 100.0),
        _entry(4, "B", 1, 1, 2, 108.0), _entry(5, "B", 2, 0, 2, 102.0),
        _entry(6, "C", 1, 1, 2, 99.0), _entry(7, None, 0, 0, 1, 98.0),
    ]


def test_roles_identify_fav_line(board):
    roles = ff.roles_of(board, fav=1)
    assert roles[2] == ff.ROLE_FAV_MATE
    assert roles[3] == ff.ROLE_FAV_THIRD
    # B(4,5) は得点合計210でCより強い → 4 が最強別ライン先頭
    assert roles[4] == ff.ROLE_LEAD_TOP
    assert roles[5] == ff.ROLE_OTHER_MATE
    assert roles[6] == ff.ROLE_LEAD_OTHER
    assert roles[7] == ff.ROLE_SOLO
    assert 1 not in roles          # 本命自身は含まない


def test_fav_line_is_excluded_from_pool(board):
    """本命ラインの番手・3番手は購入プールに入らない。"""
    roles = ff.roles_of(board, fav=1)
    others = [4, 2, 5, 6, 3, 7]                     # モデル3着内率順の想定
    pool = sw.rank_7h1_pool(others, roles)
    assert 2 not in pool and 3 not in pool
    assert set(pool) == {4, 5, 6, 7}


def test_legs_shape_and_line_exclusion(board):
    """🔴 三連単一本化（2026-08-15）。三連複BOX は組まない。"""
    roles = ff.roles_of(board, fav=1)
    others = [4, 2, 5, 6, 3, 7]
    tf = sw.rank_7h1_build_legs(others, roles)
    # 1着=別ライン先頭(4)固定 × 2着=プールr1r2 × 3着=残り5車
    assert len(tf) == 8
    assert all(t.startswith("4-") for t in tf), "1着が別ライン先頭に固定されていない"
    # 3着は総流しなので本命ラインも入りうる（設計どおり）
    assert any(t.endswith("-2") or t.endswith("-3") for t in tf)
    # 2着に本命ラインは入らない（プール＝本命ラインを落とした車から選ぶ）
    assert all(int(t.split("-")[1]) not in (2, 3) for t in tf), "2着に本命ラインが混入"


def test_no_lead_top_means_no_legs():
    """別ラインの先頭が居なければ買い目を組まない（全員単騎など）。

    ⚠️ 一本化前はここで三連複BOXだけを返しており、`rank_7h1_daily_select` が
       `legs_trio` と `legs_tf` の**両方**を要求することで除外していた。
       いまは空リストを返すこと自体が除外の唯一の手段。
    """
    solos = [_entry(i, None, 0, 0, 1, 100.0) for i in range(1, 8)]
    roles = ff.roles_of(solos, fav=1)
    assert sw.rank_7h1_build_legs([2, 3, 4, 5, 6, 7], roles) == []


@pytest.mark.parametrize("n_legs", [1, 3, 6, 8, 14])
def test_stakes_never_exceed_cap_and_are_100yen_units(n_legs):
    u, total = sw.rank_7h1_stakes(n_legs)
    assert total <= sw.RANK_7H1_BUDGET_CAP, "1レースの購入上限を超えている"
    assert u % sw.RANK_7H1_UNIT == 0 and u >= sw.RANK_7H1_UNIT
    assert total == u * n_legs


def test_stakes_follow_the_shared_budget_rule():
    """🔴 賭け金は全ランク共通の `unit_stake`（1レース1万円 ÷ 点数）。

    一本化前は 7H1 だけが `RANK_7H1_TF_UNIT`(900円) という専用の単価を持ち、
    記録側と入稿側で二重管理になって Web の実績が実際の商品を説明しない
    状態になったことがある（2026-08-07〜08-08）。単価は1箇所で決める。
    """
    assert sw.rank_7h1_stakes(8) == (1200, 9600)      # 7車立ての通常形＝8点
    for n in (1, 3, 6, 8, 14):
        assert sw.rank_7h1_stakes(n)[0] == sw.unit_stake(n)


def _cand(gap, bust, **kw):
    d = {"n_entries": 7, "gap12": gap, "bust_prob": bust, "legs_tf": ["1-2-3"]}
    d.update(kw)
    return d


def test_daily_select_uses_absolute_threshold():
    """絶対閾値で切る。**日次の相対順位に戻してはいけない**（件数が半減する）。"""
    below = sw.RANK_7H1_BUST_PROB_MIN - 0.01
    above = sw.RANK_7H1_BUST_PROB_MIN + 0.01
    # 全件が閾値未満なら 0件（「最低1件は出す」にしてはいけない）
    assert sw.rank_7h1_daily_select([_cand(0.30, below) for _ in range(20)]) == []
    # 閾値以上は件数に関わらず全部通る
    got = sw.rank_7h1_daily_select([_cand(0.30, above) for _ in range(20)])
    assert len(got) == 20


def test_daily_select_gates():
    above = sw.RANK_7H1_BUST_PROB_MIN + 0.05
    gap_ng = sw.RANK_7H1_GAP_MIN - 0.01
    assert sw.rank_7h1_daily_select([_cand(gap_ng, above)]) == [], "抜け度ゲートが効いていない"
    assert sw.rank_7h1_daily_select([_cand(0.30, above, n_entries=9)]) == [], "7車限定が効いていない"
    assert sw.rank_7h1_daily_select([_cand(0.30, above, legs_tf=[])]) == [], "買い目未成立が通っている"


def test_favbust_feature_cols_are_unique_and_sized():
    cols = ff.FAVBUST_FEATURE_COLS
    assert len(cols) == len(set(cols)), "特徴量名が重複している"
    assert len(cols) == len(ff.RACE_FEATURE_COLS) + len(ff.FAV_FEATURE_COLS)


def test_fav_features_returns_none_when_axis1_is_not_honmei(board):
    """軸1（1着率最上位）が WT◎ でなければ母集団外＝None を返す。"""
    preds = {i: (0.5, 0.5, 0.1) for i in range(1, 8)}
    preds[4] = (0.9, 0.9, 0.1)                 # 軸1は4番
    board[0]["prediction_mark"] = 1            # ◎は1番 → 不一致
    assert ff.fav_features(board, preds) is None
    board[0]["prediction_mark"] = None
    board[3]["prediction_mark"] = 1            # ◎も4番 → 一致
    got = ff.fav_features(board, preds)
    assert got is not None and got["_fav"] == 4


# ── 発走前のライブ判定（盤面・欠車の扱い）───────────────────────────────


def _lookup(perm_cars):
    """指定車で作れる全順列の三連単オッズ辞書（盤面のダミー）。"""
    from itertools import permutations

    from scripts.notify_prerace_wt import _parse_combo_key
    return {_parse_combo_key("-".join(map(str, p)), True): 100.0
            for p in permutations(perm_cars, 3)}


@pytest.fixture
def cand_7h1():
    return {"fav": 1, "venue_name": "T", "race_no": 1, "fav_name": "X",
            "gap12": 0.25, "bust_prob": 0.35,
            "legs_tf": [f"4-{a}-{c}" for a in (2, 5)
                        for c in (2, 3, 5, 6, 7) if c != a][:8]}


def test_judge_buys_when_board_is_complete(cand_7h1):
    from scripts.notify_prerace_wt import judge_rank_7h1
    decision, detail = judge_rank_7h1(cand_7h1, _lookup([1, 2, 3, 4, 5, 6, 7]))
    assert decision == "buy"
    assert detail["bet_amount"] <= sw.RANK_7H1_BUDGET_CAP
    assert detail["dropped_tf"] == 0
    # 🔴 一本化後は三連複のキーを持たない（採点側もこれを前提にしている）
    assert "legs_trio" not in detail and "stake_trio" not in detail


def test_judge_skips_when_first_place_car_is_scratched(cand_7h1):
    """三連単の1着固定車が盤面に無い＝レース無効（見送り）。"""
    from scripts.notify_prerace_wt import judge_rank_7h1
    decision, detail = judge_rank_7h1(cand_7h1, _lookup([1, 2, 3, 5, 6, 7]))  # 4番欠車
    assert decision == "skip"
    assert "1着固定" in (detail["skip_reason"] or "")


def test_judge_skips_when_fav_itself_is_scratched(cand_7h1):
    """🔴 本命（＝バストすると読んだ相手）自身が欠車したら見送ること。

    買い目は設計上 fav を一切含まない（本命ラインを丸ごと落とす）ので、
    fav が消えても組み合わせは残り6車で成立してしまい、**そのまま "buy" に
    なっていた**（2026-08-08 レビューで検出。他ランクは全て「軸が盤面に不在」を
    明示的に skip 扱いにしているのに 7H1 だけ防御が無かった）。

    7H1 の選別は「7車ちょうどの盤面で本命1車が沈む」というレース構造の予測に
    依存しており、fav 自身が欠車した時点で前提が崩れる（実質6車レース＝
    favbust モデルの較正が想定していない状況）。
    """
    from scripts.notify_prerace_wt import judge_rank_7h1
    decision, detail = judge_rank_7h1(cand_7h1, _lookup([2, 3, 4, 5, 6, 7]))  # 1番欠車
    assert decision == "skip"
    assert "本命" in (detail["skip_reason"] or "")


def test_judge_still_buys_when_fav_is_present(cand_7h1):
    """本命が居るなら従来どおり買うこと（上の追加ガードで買えなくならない）。"""
    from scripts.notify_prerace_wt import judge_rank_7h1
    assert judge_rank_7h1(cand_7h1, _lookup([1, 2, 3, 4, 5, 6, 7]))[0] == "buy"


def test_judge_drops_scratched_partners_and_restakes(cand_7h1):
    """相手が欠けた目だけ落とし、残った点数で賭け金を張り直す。"""
    from scripts.notify_prerace_wt import judge_rank_7h1
    decision, detail = judge_rank_7h1(cand_7h1, _lookup([1, 2, 3, 4, 5, 6]))  # 7番欠車
    assert decision == "buy"
    assert detail["dropped_tf"] > 0
    assert detail["stake_tf"] * len(detail["legs_tf"]) == detail["bet_amount"]
    assert detail["bet_amount"] <= sw.RANK_7H1_BUDGET_CAP


def test_judge_returns_unknown_without_board(cand_7h1):
    """盤面が取れていないときは skip ではなく『不明』（次回再試行）。"""
    from scripts.notify_prerace_wt import judge_rank_7h1
    assert judge_rank_7h1(cand_7h1, {})[0] == "不明"


# ── 判定記録 → 採点の受け渡し ─────────────────────────────────────────


def test_decision_payload_carries_everything_scoring_needs(cand_7h1):
    """`_save_decision` へ渡す detail に採点必須キーが全て残ること。

    採点（notify_results_wt の _slot=="seven_7h1"）は判定記録から
    legs_tf / stake_tf / bet_amount を読む。1つでも間引くと**黙って採点
    できなくなる**（実装時に legs_tf を除外していて実際に踏んだ）。
    """
    from scripts.notify_prerace_wt import judge_rank_7h1
    _decision, detail = judge_rank_7h1(cand_7h1, _lookup([1, 2, 3, 4, 5, 6, 7]))
    for k in ("legs_tf", "stake_tf", "bet_amount"):
        assert k in detail and detail[k], f"採点に必要な {k} が detail に無い"


def test_7h1_suffix_is_registered_for_overwrite_protection():
    """`#7H1` が picks_history 上書き保護のサフィックス集合に入っていること。"""
    from scripts.notify_results_wt import _PAPER_SUFFIXES
    assert "#7H1" in _PAPER_SUFFIXES
    # 他ランクのサフィックスと取り違えないこと（キー切り出しは _key[:-4]）
    assert "race_20260806_12_01#7H1"[:-4] == "race_20260806_12_01"


def test_reconcile_covers_7h1_once_rebuild_exists():
    """`rebuild_7h1_walkforward_pg.py` を作ったら tail reconcile へ登録すること。

    登録漏れがあると当月の picks_history が rebuild行 と live行 の混在になる
    （2026-08-06 に 7A/7B で実際に発生）。逆に rebuild が無いうちに登録すると
    cron が毎朝失敗するため、**存在するのに未登録**のときだけ落とす。

    ⚠️ 判定は `reconcile_walkforward_tail.sh` の **for 行のパース**で行う。
       以前は全文の `in` 判定だったため、同ファイルのコメントに書かれた
       "7h1:7H1"（＝「実装したらここへ足すこと」という TODO そのもの）を
       拾ってしまい、**未登録のまま PASS していた**（2026-08-08 検出）。
       安全網が安全網として機能していなかったので、二度と全文一致に戻さない。
    """
    from tests.reconcile_spec import rebuild_scripts, reconcile_specs
    registered = reconcile_specs()
    if "7h1" in rebuild_scripts():
        assert registered.get("7h1") == "7H1", (
            "rebuild_7h1_walkforward_pg.py があるのに "
            f"reconcile_walkforward_tail.sh の for 行へ未登録（登録済み: {sorted(registered)}）")


def test_reconcile_covers_every_rebuild_script():
    """存在する `rebuild_*_walkforward_pg.py` は全て tail reconcile へ登録すること。

    7H1 の登録漏れ（2026-08-08 検出）はランク固有の事故ではなく
    「ランクを増やすたびに手書きリストへ足し忘れる」という反復パターンで、
    このリポジトリでは RANK_ORDER・CURRENT_PAPER_RANKS でも同型の事故が
    起きている。ランク名を1つずつ検査するテストを増やしても次のランクで
    また漏れるので、**存在するスクリプト全件**を対象に検査する。

    意図的に除外するものは `_INTENTIONALLY_UNREGISTERED` へ理由付きで書く。
    """
    from tests.reconcile_spec import (
        _INTENTIONALLY_UNREGISTERED, rebuild_scripts, reconcile_specs,
    )
    missing = rebuild_scripts() - set(reconcile_specs()) - set(_INTENTIONALLY_UNREGISTERED)
    assert not missing, (
        f"rebuild スクリプトがあるのに tail reconcile へ未登録: {sorted(missing)}。"
        " 登録するか、除外理由を tests/reconcile_spec._INTENTIONALLY_UNREGISTERED へ書くこと")


def test_reconcile_has_no_abolished_rank():
    """🔴 廃止したランクが tail reconcile に残っていないこと。

    残すと**廃止したランクの行が毎晩 picks_history に書き戻される**。
    2026-08-14 に 9S/9A を全廃した際、reconcile からの除去が漏れた
    （置換が空振りしたのに気づけなかった）。rebuild スクリプトの存在では
    検知できない——スクリプト自体は残置するため。
    """
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    from src.strategy_wt import ABOLISHED_PAPER_RANKS
    from tests.reconcile_spec import reconcile_specs
    registered = {label for label in reconcile_specs()}
    abolished = {s.rank.replace("RANK_", "") for s in ABOLISHED_PAPER_RANKS}
    leaked = registered & abolished
    assert not leaked, (
        f"廃止済みランクが tail reconcile に登録されています: {sorted(leaked)}。"
        " 毎晩 picks_history へ書き戻されます")


def test_reconcile_registration_has_no_dangling_entry():
    """逆に、実体の無いスクリプトを登録していないこと（毎朝 cron が失敗する）。"""
    from tests.reconcile_spec import rebuild_scripts, reconcile_specs
    dangling = set(reconcile_specs()) - rebuild_scripts()
    assert not dangling, (
        f"tail reconcile が存在しない rebuild スクリプトを呼んでいる: {sorted(dangling)}")


def test_evening_pass_rebuilds_7h1_before_early_exit():
    """夕方の第2パスで 7H1 再生成が **早期 exit より前**にあること。

    `evening_picks_wt.sh` は「朝に◎◯未公開だったレースが0件」なら
    `exit 0` して以降を丸ごと飛ばす（実際 2026-08-06 は0件だった）。
    7H1 の再生成をその後ろに置くと**夕方に一度も走らない**。
    """
    from pathlib import Path
    sh = (Path(__file__).resolve().parent.parent / "scripts"
          / "evening_picks_wt.sh").read_text(encoding="utf-8")
    i_build = sh.find("build_7h1_candidates.py")
    i_exit = sh.find("再算出の対象なし")
    assert i_build != -1, "evening_picks_wt.sh に 7H1 の再生成が無い"
    assert i_exit != -1
    assert i_build < i_exit, "7H1 の再生成が早期exitより後ろにある（夕方に走らない）"
