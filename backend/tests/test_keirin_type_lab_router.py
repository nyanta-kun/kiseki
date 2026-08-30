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
    # 型A が3分割された（2026-08-31）ので 8 → 10。
    assert len(PLAN_ORDER) == 10
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


# ---------------------------------------------------------------------------
# 軸信頼ゲート（2026-08-27 追加）
# ---------------------------------------------------------------------------
def test_axis_gate_keeps_rows_it_cannot_judge():
    """🔴 判定できない行は**通す**。

    閾値を持たないプランや axis_sum が無い行を落とすと、ゲートを入れた瞬間に
    理由の分からない件数減が起きる。落とすのは「測って下だった」ときだけ。
    """
    from src.services.keirin_type_lab_gate import passes_axis_gate

    assert passes_axis_gate("A_hit", None) is True
    assert passes_axis_gate("未知のプラン", 0.0) is True


def test_axis_gate_thresholds_are_per_plan():
    """🔴 絶対閾値では効かない（本番7Cの1.44含め全部0を跨ぐ）。

    効くのは「各プランの中で相対的に下を外す」形だけなので、閾値は
    **プランごとに違う値**でなければならない。全部同じ値になっていたら、
    それは絶対閾値に退化している。
    """
    from src.services.keirin_type_lab_gate import AXIS_GATE_MIN

    assert len(set(AXIS_GATE_MIN.values())) > 1
    # 堅い型ほど高い（型A > 型F）。逆転していたら分位の取り違え
    assert AXIS_GATE_MIN["A_hit"] > AXIS_GATE_MIN["F_hit"]
    # 同じレースに出るプランは同じ閾値（A_hit/A_pay・F_hit/F_pay）
    assert AXIS_GATE_MIN["A_hit"] == AXIS_GATE_MIN["A_pay"]
    assert AXIS_GATE_MIN["F_hit"] == AXIS_GATE_MIN["F_pay"]


def test_axis_gate_is_applied_before_conflict_detection():
    """🔴 ゲートは競合判定の**前**に掛ける。

    後に掛けると、片方だけゲートで落ちたレースが「競合ではない」のに
    1プランだけ残り、母集団がずれる。実装順を構文で固定する。
    """
    import ast
    import inspect

    from src.api import keirin_type_lab_router as m

    src = inspect.getsource(m.get_type_lab_combo)
    tree = ast.parse(src.lstrip())
    gate_at = combine_at = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None)
            if name == "passes_axis_gate" and gate_at is None:
                gate_at = node.lineno
            if name == "combine_plans" and combine_at is None:
                combine_at = node.lineno
    assert gate_at is not None and combine_at is not None
    assert gate_at < combine_at, "軸信頼ゲートは combine_plans より前に掛けること"


# ──────────────────── 9車の実投入（2026-08-28） ────────────────────

def test_axis_gate_does_not_apply_to_nine_car():
    """🔴 軸信頼ゲートは**7車の探索窓の分位**なので9車には掛けない。

    9車は同じプランでも軸信頼の分布が丸ごと低い（A_hit の p20 は 7車 1.537 ↔
    9車 1.504 / F_hit は 1.230 ↔ 1.160）。そのまま当てると「下位1/5を外す」ではなく
    **絶対値で切る**ことになり、doc が明確に否定した操作と同じになる。
    9車の結論（ROI 83.0/89.1%）もゲート無しで測った数字。
    """
    from src.services.keirin_type_lab_gate import passes_axis_gate

    # 7車なら落ちる値
    assert passes_axis_gate("A_hit", 1.50, 7) is False
    # 9車・車数不明は通す
    assert passes_axis_gate("A_hit", 1.50, 9) is True
    assert passes_axis_gate("A_hit", 1.50, None) is True
    assert passes_axis_gate("F_hit", 1.00, 9) is True


def test_combo_passes_the_car_count_to_the_gate():
    """🔴 `passes_axis_gate` に車数を渡すこと。

    渡し忘れても例外は出ず、9車の行が7車の閾値で静かに削られるだけになる
    （件数が減った理由が画面からは読めない）。実装を構文で固定する。
    """
    import ast
    import inspect

    from src.api import keirin_type_lab_router as m

    src = inspect.getsource(m.get_type_lab_combo)
    tree = ast.parse(src.lstrip())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "passes_axis_gate"]
    assert calls, "passes_axis_gate を呼んでいない"
    for c in calls:
        assert len(c.args) >= 3, "passes_axis_gate に車数を渡していない"
    # 判定に使う列を SELECT していること（渡す式があっても列が無ければ常に None）
    assert "n_entries" in str(m._SQL_COMBO), "_SQL_COMBO が n_entries を引いていない"


def test_all_modes_are_selectable_on_every_endpoint():
    """4モードすべてが3つのエンドポイントで受け付けられること。

    片方だけ足すと、一覧は9車が見えるのに答え合わせだけ 422 になる。

    ⚠️ 2026-08-28 に **複数選択**へ変えたので、注釈は `Literal` ではなく `str`。
       受け付ける値の正本は `parse_modes`（カンマ区切り）。
    """
    import inspect

    from src.api import keirin_type_lab_router as m

    for fn in (m.get_type_lab, m.get_type_lab_combo, m.get_type_lab_outcome):
        src = inspect.getsource(fn)
        assert "modes = parse_modes(mode)" in src, f"{fn.__name__} が parse_modes を通していない"
    for one in m.TYPE_LAB_MODES:
        assert m.parse_modes(one) == [one]


def test_parse_modes_normalizes_multi_select():
    """複数選択の正規化。**同じ選択なら同じ URL** になること。"""
    from src.api.keirin_type_lab_router import TYPE_LAB_MODES, parse_modes

    assert parse_modes("paper9,live") == ["live", "paper9"]      # 並びは定義順
    assert parse_modes("live, live9 ,live") == ["live", "live9"]  # 重複と空白
    assert parse_modes("") == list(TYPE_LAB_MODES)                # 空＝すべて
    assert parse_modes("all") == list(TYPE_LAB_MODES)
    assert parse_modes(None) == ["live"]


def test_parse_modes_never_returns_empty():
    """🔴 知らない値だけでも空リストを返さない（0件の SQL を投げない）。

    URL を手で書いたときに 500 や「全件0件」を出すより、選べる範囲へ丸める。
    """
    from src.api.keirin_type_lab_router import parse_modes

    assert parse_modes("知らない値") == ["live"]
    assert parse_modes(",,,") == ["live", "live9", "paper", "paper9"]
    assert parse_modes("paper,知らない値") == ["paper"]


def test_every_mode_query_uses_an_array_comparison():
    """🔴 SQL は `mode = ANY(:modes)`。単一比較が1つでも残ると、そのタブだけ
       「モードを複数選んでも1つしか出ない」という**気づきにくい**壊れ方をする。
    """
    from src.api import keirin_type_lab_router as m

    for sql in (m._SQL, m._SQL_COMBO, m._SQL_OUTCOME):
        text = str(sql)
        assert "mode = ANY(:modes)" in text, text
        assert "mode = :mode" not in text, text


def test_rank_pos_treats_the_head_of_the_list_as_highest_priority():
    """🔴 `_rank_pos` は**先頭ほど上位**。反転しても既存テストは通っていた。

    1レースに複数ランクの候補があるとき、比較表は「実際に売られる1つ」＝
    優先順位の最上位と並べる。向きを取り違えると**最下位のランクと比べた数字**を
    出すことになるが、リストの中身しか固定していなかったため検出できなかった
    （2026-08-28 の監査 D）。
    """
    from src.api.keirin_type_lab_router import CURRENT_RANK_ORDER, _rank_pos

    assert _rank_pos(CURRENT_RANK_ORDER[0]) == 0
    assert _rank_pos(CURRENT_RANK_ORDER[0]) < _rank_pos(CURRENT_RANK_ORDER[-1])
    # 単調（先頭 → 末尾で増える）
    pos = [_rank_pos(r) for r in CURRENT_RANK_ORDER]
    assert pos == sorted(pos) and pos == list(range(len(CURRENT_RANK_ORDER)))
    # 知らないランクは**最下位**へ（黙って最上位に割り込ませない）
    assert _rank_pos("RANK_UNKNOWN") == len(CURRENT_RANK_ORDER)


def test_comparison_picks_the_highest_priority_rank_for_a_race():
    """同じレースに複数ランクがあるとき、比較の相手は優先順位の最上位。"""
    import ast
    import inspect

    from src.api import keirin_type_lab_router as m

    src = inspect.getsource(m.get_type_lab)
    tree = ast.parse(src.lstrip())
    # `_rank_pos(...) < _rank_pos(...)` で選んでいること（`>` へ反転させない）
    ops = [type(n.ops[0]).__name__ for n in ast.walk(tree)
           if isinstance(n, ast.Compare)
           and isinstance(n.left, ast.Call)
           and getattr(n.left.func, "id", "") == "_rank_pos"]
    assert ops == ["Lt"], ops
