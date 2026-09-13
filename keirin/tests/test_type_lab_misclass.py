"""外れの5分類（`DESIGN.md` 4.3）を行へ焼き付ける経路の回帰テスト（2026-09-11）。

## なぜ要るか

5分類は**打ち手が層ごとに正反対**なので、まとめて「表示的中が低い」と数えると
何も直せない。判定に要る「決着の目の予測オッズ」と「確率順位」は
**生成時のモデルにしか無く**、モデルを再学習すると別の値になる
（`p3_order` / `pw_ent` と同じ。`docs/type_lab/recent_drop_2026_09_11.md` は
この列が無かったため ④a/④b を割れず帯の近似で済ませている）。

🔴 **壊れても例外が出ない**経路なので、次の3つを機械的に固定する:

1. `classify_miss` の分岐（`DESIGN.md` 4.3 の表と1対1）
2. 生成側が `band_min_odds` / `prob_ranked` を**代替へ落ちた後のプラン**から作ること
3. 採点側が 3列（`win_pred_odds` / `win_prob_rank` / `miss_class`）を書き、
   かつ**組み直しでその3列が捨てられる**こと（古い分類が新しい買い目に付かない）
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from src.type_lab import (  # noqa: E402
    MISS_CLASSES, PLANS, PROB_RANKED_N, axis_hit_of, classify_miss, combo_str,
    lookup_prob_ranked, prob_ranked_rows,
)

import build_type_lab_picks as B  # noqa: E402
import settle_type_lab_picks as S  # noqa: E402


# ───────────────────────── 1. 判定の正本 ─────────────────────────

BASE = dict(hit=False, axis_hit=True, band_min_odds=15.0,
            win_pred_odds=40.0, win_prob_rank=50, n_legs=12)


@pytest.mark.parametrize("over,want", [
    ({"hit": True}, "hit"),
    # 軸崩壊は帯より先。帯の下で決まっていても「前提の崩壊」が優先
    ({"axis_hit": False, "win_pred_odds": 3.0}, "read_axis"),
    ({"win_pred_odds": 3.0}, "read_band"),
    ({"win_prob_rank": 12}, "legs_budget"),      # k 位ちょうどは買えていた側
    ({"win_prob_rank": 13}, "legs_model"),
    # 帯を持たないプランでは ③ は構造的に起きない
    ({"band_min_odds": 0.0, "win_pred_odds": 1.2, "win_prob_rank": 3}, "legs_budget"),
    # `prob_ranked` の圏外（= PROB_RANKED_N 位より下）は「モデルの限界」へ倒す
    ({"win_pred_odds": None, "win_prob_rank": None}, "legs_model"),
])
def test_classify_miss(over, want):
    assert classify_miss(**{**BASE, **over}) == want


def test_classify_miss_returns_known_label():
    assert classify_miss(**BASE) in MISS_CLASSES


def test_axis_hit_of_reads_both_bet_types():
    assert axis_hit_of(1, 2, "2-1-5") is True
    assert axis_hit_of(1, 2, "1=2=5") is True
    assert axis_hit_of(1, 2, "1-3-5") is False
    assert axis_hit_of(None, 2, "1-2-3") is None


# ───────────────────────── 2. 焼き付ける形 ─────────────────────────

def test_prob_ranked_rows_is_probability_descending_and_capped():
    po = {(1, 2, c): float(c) for c in range(3, 8)}
    pr = {k: 1.0 / (i + 1) for i, k in enumerate(po)}
    got = prob_ranked_rows(po, pr, "trifecta", 3)
    assert got == [["1-2-3", 3.0], ["1-2-4", 4.0], ["1-2-5", 5.0]]


def test_prob_ranked_rows_drops_legs_without_pred_odds():
    """予測オッズが無い目は落とす。**帯の判定に使えない**ため。"""
    po = {(1, 2, 3): 4.0, (1, 2, 4): 0.0}
    pr = {(1, 2, 3): 0.2, (1, 2, 4): 0.3, (1, 2, 5): 0.1}
    assert prob_ranked_rows(po, pr, "trifecta") == [["1-2-3", 4.0]]


def test_lookup_round_trips_with_combo_str():
    """🔴 焼き付けと引き当ては**同じ表記**でなければ黙って外れ扱いになる。"""
    po = {frozenset({1, 2, 3}): 2.5, frozenset({1, 2, 4}): 9.0}
    pr = {frozenset({1, 2, 3}): 0.3, frozenset({1, 2, 4}): 0.1}
    rows = prob_ranked_rows(po, pr, "trio")
    win = combo_str(frozenset({1, 2, 4}), "trio")
    assert lookup_prob_ranked(rows, win) == (9.0, 2)
    assert lookup_prob_ranked(rows, "5=6=7") == (None, None)


def test_prob_ranked_n_covers_every_trio_combo():
    """三連複は 7車 35目 / 9車 84目。**上限は実質効かない**（doc の前提）。"""
    assert PROB_RANKED_N >= 84


def test_build_uses_the_same_combo_str_as_type_lab():
    """生成側がローカルの表記関数を持ち直していないこと。"""
    assert B._combo_str is combo_str


# ───────────────────────── 3. 配線 ─────────────────────────

def _src(mod):
    return Path(mod.__file__).read_text()


def test_generation_bakes_band_and_prob_ranked():
    src = _src(B)
    assert "band_min_odds" in src and "prob_ranked" in src
    for col in ("band_min_odds", "prob_ranked"):
        assert col in B.COLS, f"{col} が INSERT の列に無い"


def test_band_comes_from_the_substituted_plan():
    """🔴 帯は `plan_used`（代替へ落ちた後）から取ること。

    `plan` のままだと `GATE_FALLBACK` で別の帯に化けた商品へ元の帯が付き、
    ③帯下決着 を**静かに誤判定**する。
    """
    tree = ast.parse(_src(B))
    got = [n for n in ast.walk(tree)
           if isinstance(n, ast.keyword) and n.arg == "band_min_odds"]
    assert got, "band_min_odds を渡している箇所が無い"
    assert any("plan_used" in ast.unparse(n.value) for n in got), \
        "band_min_odds が plan_used から作られていない"


def test_settle_writes_the_three_columns():
    src = _src(S)
    for col in ("win_pred_odds", "win_prob_rank", "miss_class"):
        assert f"{col} = ?" in src, f"{col} を UPDATE していない"
    assert "classify_miss(" in src


def test_settle_loads_the_inputs_it_needs():
    """採点側はモデルを引き直さず、**焼き付けた列だけ**で判定する。"""
    src = _src(S)
    for col in ("axis1", "axis2", "n_legs", "band_min_odds", "prob_ranked"):
        assert f'"{col}"' in src, f"{col} を SELECT していない"


def test_regeneration_drops_the_classification():
    """🔴 買い目を組み直したら分類も捨てる。

    残すと**古い当たり外れと古い分類が新しい買い目に付く**（`SETTLE_COLS` の
    docstring と同じ事故）。例外もログも出ない。
    """
    for col in ("win_pred_odds", "win_prob_rank", "miss_class"):
        assert col in B.SETTLE_COLS, f"{col} が SETTLE_COLS に無い"


def test_banded_plans_are_the_only_ones_that_can_be_read_band():
    """帯を持つのは `C_hit` / `E_hit` / `F_hit` だけ（doc の前提の固定）。"""
    banded = {k for k, p in PLANS.items() if p.min_odds > 0}
    assert {"C_hit", "E_hit", "F_hit"} <= banded


# ───────────────────── 4. DB を通した往復（SQLite）─────────────────────

def _seed(conn, **over):
    """`build_type_lab_picks` が書くのと同じ列で1行入れる。"""
    row = dict(
        race_key="20260911_99_01", race_date="2026-09-11", venue_name="テスト",
        race_no=1, race_type="一般", n_entries=7, day_index=1,
        type_label="C", axis_sum=1.5, arare=1, gap=0.2, pw_ent=1.2,
        axis1=1, axis2=2, p3_order="1-2-3-4-5-6-7", mode="live",
        plan_key="C_hit", bet_type="trifecta", n_legs=12, budget=10_000,
        legs=json.dumps([{"combo": "1-2-4", "stake": 800, "pred_odds": 20.0,
                          "prob": 0.05, "role": "base"}]),
        pred_mean_payout=22_000.0, pred_min_payout=16_000.0,
        band_min_odds=15.0,
        prob_ranked=json.dumps([["1-2-3", 4.0], ["1-2-4", 20.0], ["3-1-2", 80.0]]),
        rule_version="test",
    )
    row.update(over)
    cols = [c for c in B.COLS if c in row]
    conn.execute(
        f"INSERT INTO type_lab_picks ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
    conn.commit()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """まっさらな SQLite に `init_db()` の DDL を当てる。

    🔴 `DB_PATH` は**モジュール定数**で環境変数では動かない。差し替えないと
       開発機の `keirin/data/keirin.db` を掴み、`CREATE TABLE IF NOT EXISTS` は
       既存テーブルに対して**何もしない**ので、DDL を足しても検査が通らない
       （＝列の足し忘れを見逃す）。
    """
    from src import database as D
    monkeypatch.setattr(D, "DB_PATH", tmp_path / "t.db")
    D.init_db()
    with D.get_connection() as c:
        yield c


def test_columns_exist_in_the_sqlite_fallback_schema(db):
    """🔴 SQLite 側の DDL を足し忘れると、テストだけ通って本番で落ちる。"""
    cols = {r[1] for r in db.execute("PRAGMA table_info(type_lab_picks)").fetchall()}
    assert {"band_min_odds", "prob_ranked", "win_pred_odds",
            "win_prob_rank", "miss_class"} <= cols


def test_insert_then_classify_round_trip(db):
    """生成で焼き付けた列だけで、採点側と同じ手順が5分類まで届くこと。"""
    _seed(db)
    r = dict(zip(("axis1", "axis2", "n_legs", "band_min_odds", "prob_ranked"),
                 db.execute("SELECT axis1, axis2, n_legs, band_min_odds, prob_ranked "
                            "FROM type_lab_picks").fetchone()))
    ranked = json.loads(r["prob_ranked"])

    # ③ 帯下決着: 決着 1-2-3 は予測 4.0倍 < 帯 15.0倍
    po, rank = lookup_prob_ranked(ranked, "1-2-3")
    assert (po, rank) == (4.0, 1)
    assert classify_miss(hit=False, axis_hit=axis_hit_of(r["axis1"], r["axis2"], "1-2-3"),
                         band_min_odds=r["band_min_odds"], win_pred_odds=po,
                         win_prob_rank=rank, n_legs=r["n_legs"]) == "read_band"

    # ② 軸崩壊: 軸2（2番）が3着外
    assert classify_miss(hit=False, axis_hit=axis_hit_of(r["axis1"], r["axis2"], "1-3-5"),
                         band_min_odds=r["band_min_odds"], win_pred_odds=None,
                         win_prob_rank=None, n_legs=r["n_legs"]) == "read_axis"

    # ④a 予算: 帯の中・確率3位で 12点以内なのに買えていない
    po, rank = lookup_prob_ranked(ranked, "3-1-2")
    assert classify_miss(hit=False, axis_hit=axis_hit_of(r["axis1"], r["axis2"], "3-1-2"),
                         band_min_odds=r["band_min_odds"], win_pred_odds=po,
                         win_prob_rank=rank, n_legs=r["n_legs"]) == "legs_budget"

    # ④b モデル: `prob_ranked` の圏外
    assert classify_miss(hit=False, axis_hit=axis_hit_of(r["axis1"], r["axis2"], "1-2-7"),
                         band_min_odds=r["band_min_odds"],
                         win_pred_odds=None, win_prob_rank=None,
                         n_legs=r["n_legs"]) == "legs_model"


def test_settle_columns_are_writable(db):
    _seed(db)
    db.execute("UPDATE type_lab_picks SET win_pred_odds = ?, win_prob_rank = ?, "
               "miss_class = ? WHERE race_key = ?", (4.0, 1, "read_band", "20260911_99_01"))
    db.commit()
    got = db.execute("SELECT win_pred_odds, win_prob_rank, miss_class "
                     "FROM type_lab_picks").fetchone()
    assert (float(got[0]), int(got[1]), str(got[2])) == (4.0, 1, "read_band")


def test_settle_skips_rows_without_the_baked_inputs():
    """🔴 焼き付けが無い行（列を足す前に生成）には分類を書かない。

    `prob_ranked` が NULL だと `classify_miss` は必ず `legs_model` を返す。
    それは「モデルの限界だった」という**確信のある誤った札**で、
    ③帯下決着 と ④a予算 を丸ごと飲み込む。
    """
    src = _src(S)
    assert "if prob_ranked is None:" in src
    assert "win_po = win_rank = miss_class = None" in src
    # 実際にその条件で classify_miss が誤った札を返すことの実演（＝ガードの必要性）
    assert classify_miss(hit=False, axis_hit=True, band_min_odds=None,
                         win_pred_odds=None, win_prob_rank=None,
                         n_legs=12) == "legs_model"
