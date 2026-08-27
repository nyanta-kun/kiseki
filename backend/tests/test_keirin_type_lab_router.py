"""型ラボ API の回帰テスト（2026-08-27）。

ここで固定するのは、壊れると**ページが 500 になって何も見えない**点。
"""
from __future__ import annotations

from datetime import date

from src.api.keirin_type_lab_router import CURRENT_RANK_ORDER, PLAN_ORDER, window


def test_window_returns_date_objects_for_date_columns():
    """🔴 asyncpg は DATE 列へ文字列を渡せない。

    `'str' object has no attribute 'toordinal'` で 500 になる（2026-08-27 に実際に踏んだ）。
    `race_date` と比べる引数は必ず `datetime.date` にすること。
    """
    d1, d2, dd1, dd2 = window("2026-08-01", "2026-08-07")
    assert (d1, d2) == ("2026-08-01", "2026-08-07")
    assert isinstance(dd1, date) and isinstance(dd2, date)
    assert (dd1.isoformat(), dd2.isoformat()) == (d1, d2)


def test_window_defaults_to_the_last_seven_days():
    d1, d2, dd1, dd2 = window(None, "2026-08-07")
    assert d1 == "2026-08-01" and d2 == "2026-08-07"
    assert (dd2 - dd1).days == 6


def test_lists_are_not_empty():
    """表示順と優先順位の手書きリストが空になっていないこと。"""
    assert len(PLAN_ORDER) == 8
    assert CURRENT_RANK_ORDER[0] == "RANK_7H2" and CURRENT_RANK_ORDER[-1] == "RANK_7M1"


def test_each_query_gets_the_parameter_type_its_column_needs():
    """🔴 `race_date` の型がテーブルごとに違う。

        keirin.type_lab_picks.race_date  … DATE     → datetime.date
        keirin.picks_history.race_date   … VARCHAR  → str
        keirin.netkeirin_submissions     … 日付列なし → race_key の先頭8桁（str）

    asyncpg は型を厳格に見るので取り違えると即 500 になる。
    2026-08-27 に**両方向とも**踏んだ（文字列を DATE へ／date を VARCHAR へ）。
    呼び出し側が渡している式を構文で固定する。
    """
    import ast
    import inspect

    from src.api import keirin_type_lab_router as m

    src = inspect.getsource(m.get_type_lab)
    tree = ast.parse(src.lstrip())
    got: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        target = getattr(node.args[0], "id", None)
        if target not in ("_SQL", "_SQL_CURRENT", "_SQL_SOLD"):
            continue
        d = node.args[1]
        if not isinstance(d, ast.Dict):
            continue
        got[target] = {ast.unparse(v) for k, v in zip(d.keys, d.values)
                       if getattr(k, "value", "") in ("d1", "d2")}
    assert got.get("_SQL") == {"dd1", "dd2"}, got.get("_SQL")
    assert got.get("_SQL_CURRENT") == {"d1", "d2"}, got.get("_SQL_CURRENT")
    assert got.get("_SQL_SOLD") == {"d1.replace('-', '')", "d2.replace('-', '')"}, \
        got.get("_SQL_SOLD")


def test_venue_options_are_built_before_filtering():
    """🔴 競輪場の選択肢は**絞り込む前**の一覧から作ること。

    絞ってから作ると選んだ場しか候補に残らず、他の場へ切り替えられなくなる。
    実装順（venues を作ってから rows を絞る）を構文で固定する。
    """
    import ast
    import inspect

    from src.api import keirin_type_lab_router as m

    src = inspect.getsource(m.get_type_lab).lstrip()
    i_v = src.index("venues = sorted(")
    i_f = src.index("rows = [r for r in rows if r[\"venue_name\"] == venue]")
    assert i_v < i_f, "venues を作る前に rows を絞っている"
    # 引数として受け取っていること
    tree = ast.parse(src)
    fn = tree.body[0]
    names = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
    assert "venue" in names


def test_start_time_is_converted_from_unix_seconds_to_jst():
    """🔴 `wt_races.start_at` は **UNIX 秒の文字列**。そのまま出すと数字が並ぶ。

    2026-08-27 の伊東1R は 1787787000 → JST 10:30。
    """
    from src.api.keirin_type_lab_router import _hhmm

    assert _hhmm("1787795880") == "10:58"
    assert _hhmm(1787795880) == "10:58"
    # 読めない値は None（画面では "--:--" になる）。例外で 500 にしない
    for bad in (None, "", "abc", "9" * 30):
        assert _hhmm(bad) is None


def test_picks_are_ordered_by_start_time():
    """🔴 一覧は**発走の早い順**。`race_key` は場コード順なので時系列にならない。"""
    from src.api.keirin_type_lab_router import _SQL

    sql = str(_SQL)
    assert "LEFT JOIN keirin.wt_races" in sql
    i_date = sql.index("ORDER BY")
    order = sql[i_date:]
    assert "start_at" in order and order.index("start_at") < order.index("p.race_key")


# ---------------------------------------------------------------------------
# 複数プランの組み合わせ集計（2026-08-27 追加）
# ---------------------------------------------------------------------------
def _pick(race: str, plan: str, *, budget: int = 10000, payout: int | None = None,
          settled: bool = True, day: str = "2026-08-27") -> dict:
    return {"race_key": race, "plan_key": plan, "race_date": day, "budget": budget,
            "settled_at": "x" if settled else None,
            "hit": payout is not None, "payout": payout}


def test_combine_plans_drops_races_where_two_selected_plans_collide():
    """🔴 1レースの推奨は1プラン。

    選んだプランが同じレースに2つ当たったら、どちらを買ったことにするか
    決められないので**そのレースは丸ごと外す**。外した数は必ず返す
    （黙って落とすと「件数が少ない」としか見えなくなる）。
    """
    from src.api.keirin_type_lab_router import combine_plans

    rows = [
        _pick("R1", "A_hit", payout=30000),
        _pick("R1", "A_pay"),            # ← 同じレースに2プラン = 競合
        _pick("R2", "B_hit", payout=25000),
    ]
    detail, total, n_conflict, n_days = combine_plans(rows)
    assert n_conflict == 1
    assert total.n_races == 1              # R1 は両方とも消える
    assert [d.plan_key for d in detail] == ["B_hit"]
    assert total.returned == 25000 and total.invested == 10000


def test_combine_plans_totals_only_settled_rows():
    """🔴 未採点を分母に入れると当日の朝ほど ROI が 0 に近く見える。"""
    from src.api.keirin_type_lab_router import combine_plans

    rows = [
        _pick("R1", "A_hit", payout=20000),
        _pick("R2", "B_hit", settled=False),   # 未採点
    ]
    _, total, _, _ = combine_plans(rows)
    assert total.n_races == 2 and total.n_settled == 1
    assert total.invested == 10000 and total.roi == 200.0


def test_combine_plans_separates_gami_from_shown_hit():
    """ガミ（払戻 <= 賭け金）は生の的中に入るが表示的中には入らない。"""
    from src.api.keirin_type_lab_router import combine_plans

    rows = [
        _pick("R1", "A_hit", payout=8000),     # 当たったが賭け金割れ = ガミ
        _pick("R2", "A_hit", payout=30000),
    ]
    _, total, _, _ = combine_plans(rows)
    assert total.n_hit == 2 and total.n_shown_hit == 1


def test_combine_plans_rows_follow_the_display_order():
    from src.api.keirin_type_lab_router import PLAN_ORDER, combine_plans

    rows = [_pick("R1", "F_hit"), _pick("R2", "A_hit"), _pick("R3", "C_hit")]
    detail, _, _, _ = combine_plans(rows)
    got = [d.plan_key for d in detail]
    assert got == sorted(got, key=PLAN_ORDER.index)
