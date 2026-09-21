"""夜間レビュー（`scripts/nightly_review_type_lab.py`）の不変条件（2026-08-29 新設）。

## 守る不変条件

1. **判定を写経していない。** 決着クラス・軸信頼ゲート・看板・採点は
   すべて kiseki 側の正本をファイル読み込みで束縛する。写した瞬間、
   「画面の答え」と「夜のレビューの答え」が静かに食い違う
2. **参照分布はプランごとに引く。** 全体から引くと今日のプラン構成が比較から消える
3. **表示的中の定義は `SoldRace.net_hit`（払戻 > 賭け金）と同じ。**
   素の的中で比べると、点数を増やしたときに「改善した」と誤読する
4. **台帳は同じ日を二度書かない。** 採点が進んでから再実行することがある
5. **決着クラスは `dim="plan"` の行にだけ入れる。** 種別・時間帯・看板の行にも
   足すと、1商品が4回数えられて母集団が4倍に見える
6. **狙い帯の判定は `win_tf_odds`（確定三連単）で行う。** `final_odds` は的中時しか
   入らないので、外れたレースの荒れ具合が測れず「狙い違い」と「買い目違い」を分離できない
7. **累積は起点（`REVIEW_EPOCH`＝2026-08-29）から数え、台帳も起点ごとに分ける。**
   他の起点の台帳は消さず・読まない。起点より前の日は今の台帳へ積まない
7b. **除外日（`EXCLUDED_DAYS`＝2026-09-15）は累積・基準・台帳のどれにも入れない。**
   その日自体を流してもレポートは出し、台帳は増やさない
8. **参照分布の無い商品を黙って落とさない。** 段の商品（T_*）にはペーパー行が無い
9. **§4 の母集団にゲート対象外のプラン（段の商品など）を入れない**
"""

from __future__ import annotations

import ast
import csv
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "nightly_review_type_lab.py"


def _load():
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("nightly_review_type_lab", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_判定は正本へ委譲している():
    """`_bind` で読む正本のパスが4つとも生きていること。"""
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    bound = [n.args[0].value for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_bind"]
    assert bound, "_bind の呼び出しが無い（正本の束縛をやめていないか）"
    for rel in bound:
        assert (REPO.parent / rel).exists(), f"正本が無い: {rel}"


def test_決着クラスをここで定義していない():
    """`FINISH_CLASSES` 相当の分類名を写経していないこと。"""
    src = SCRIPT.read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]        # モジュール docstring は説明なので除く
    for token in ("FINISH_CLASSES = ", "def finish_class("):
        assert token not in body, f"分類の定義を写経している: {token}"


def test_軸信頼ゲートの閾値を写経していない():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "AXIS_GATE_MIN = " not in src
    assert "_GATE.passes_axis_gate" in src


def test_参照分布はプランごとに引く():
    m = _load()
    pool = {"A_hit": [(10_000, 0)], "B_hit": [(10_000, 30_000)]}
    # A だけ 2件・B は 0件 → 必ず ROI 0%（B の当たりが混ざったら按分が壊れている）
    got = m._bootstrap(pool, {"A_hit": 2}, n_boot=50, seed=1)
    assert got and all(roi == 0.0 for roi, _ in got)
    got = m._bootstrap(pool, {"B_hit": 2}, n_boot=50, seed=1)
    assert got and all(roi == 3.0 for roi, _ in got)


def test_表示的中はガミを不的中として数える():
    m = _load()
    # 払戻 9,999 円 = 当たっているが賭け金 10,000 円を割る＝ガミ
    pool = {"A_hit": [(10_000, 9_999)]}
    got = m._bootstrap(pool, {"A_hit": 4}, n_boot=20, seed=1)
    assert got and all(hit == 0.0 for _, hit in got)
    pool = {"A_hit": [(10_000, 10_000)]}
    got = m._bootstrap(pool, {"A_hit": 4}, n_boot=20, seed=1)
    assert got and all(hit == 1.0 for _, hit in got)


def test_台帳は同じ日を上書きする(tmp_path, monkeypatch):
    m = _load()
    ledger = tmp_path / "ledger.csv"
    monkeypatch.setattr(m, "LEDGER", ledger)

    class _R:
        def __init__(self, plan, pay):
            self.race_key, self.rank_key, self.origin = "k", plan, None
            self.bet, self.payout = 10_000, pay
            self.hit, self.net_hit, self.n_points = pay > 0, pay >= 10_000, 5

    brk = {"per_plan": {}, "gami_by_plan": {}}
    m.append_ledger("2026-09-16", [_R("A_hit", 0)], brk)
    m.append_ledger("2026-09-16", [_R("A_hit", 50_000)], brk)
    rows = list(csv.DictReader(ledger.open(encoding="utf-8")))
    assert len(rows) == 1, "同じ日が二重に積まれている"
    assert rows[0]["payout"] == "50000", "再実行で新しい値に置き換わっていない"

    m.append_ledger("2026-09-17", [_R("A_hit", 0)], brk)
    rows = list(csv.DictReader(ledger.open(encoding="utf-8")))
    assert {r["date"] for r in rows} == {"2026-09-16", "2026-09-17"}


def test_台帳は軸ごとに積み決着クラスはプラン行にだけ入る(tmp_path, monkeypatch):
    m = _load()
    ledger = tmp_path / "ledger.csv"
    monkeypatch.setattr(m, "LEDGER", ledger)

    class _R:
        def __init__(self, plan, pay):
            self.race_key, self.rank_key, self.origin = "rk", plan, None
            self.bet, self.payout = 10_000, pay
            self.hit, self.net_hit, self.n_points = pay > 0, pay >= 10_000, 5

    from collections import Counter
    brk = {"per_plan": {"A_hit": Counter({"firm34": 1})}, "gami_by_plan": {}}
    meta = {"rk": {"race_type": "一般", "hour": 19, "marquee": False,
                   "payout_band": "30_100"}}
    m.append_ledger("2026-09-16", [_R("A_hit", 0)], brk, meta)
    rows = list(csv.DictReader(ledger.open(encoding="utf-8")))
    got = {(r["dim"], r["key"]): r for r in rows}
    assert set(d for d, _ in got) == set(m.LEDGER_DIMS), "軸が欠けている"
    assert got[("race_type", "一般")]["n"] == "1"
    assert got[("band", "18〜20時")]["n"] == "1", "JST の帯に入っていない"
    assert got[("marquee", "看板でない")]["n"] == "1"
    assert got[("payout_band", "30_100")]["n"] == "1"
    # 🔴 決着クラスはプラン行にだけ
    assert got[("plan", "A_hit")]["firm34"] == "1"
    for dim in ("race_type", "band", "marquee"):
        row = next(r for (d, _), r in got.items() if d == dim)
        assert row["firm34"] in ("0", ""), f"{dim} にも決着クラスが入っている（二重計上）"


def test_発走時刻帯の境界():
    m = _load()
    assert m._band(10) == "〜10時" and m._band(11) == "11〜14時"
    assert m._band(14) == "11〜14時" and m._band(15) == "15〜17時"
    assert m._band(17) == "15〜17時" and m._band(18) == "18〜20時"
    assert m._band(20) == "18〜20時" and m._band(21) == "21時〜"
    assert m._band(None) == "unknown"


def test_狙い帯の一致は隣の帯まで許す():
    m = _load()
    order = ["lt10", "10_30", "30_100", "100_300", "ge300"]
    assert m._near(order, "30_100", "30_100")
    assert m._near(order, "10_30", "30_100"), "隣の帯は想定どおりとして扱う"
    assert m._near(order, "100_300", "30_100")
    assert not m._near(order, "lt10", "30_100"), "2帯離れたら狙い違い"
    assert not m._near(order, None, "30_100")
    assert not m._near(order, "30_100", None)


def test_決着帯は確定三連単オッズから作る():
    """`final_odds`（的中時しか入らない）を使っていないこと。"""
    import ast
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    # 文字列（docstring・SQL・メッセージ）以外で final_odds を触っていないこと。
    # docstring には「使ってはいけない」と書いてあるので、素の grep では判定できない。
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    consts = [n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    sql = [c for c in consts if "SELECT" in c.upper()]
    assert any("win_tf_odds" in c for c in sql), "確定三連単オッズを引いていない"
    assert not any("final_odds" in c for c in sql), (
        "SQL で final_odds を引いている。的中時しか入らないので"
        "外れたレースの荒れ具合が測れなくなる")
    assert "final_odds" not in names


def test_参照分布は9車も含む():
    """🔴 参照は `mode='paper'`（7車）だけで作らない（2026-08-30 是正）。

    9車は7車より当たりにくい（表示的中 19.4% ↔ 21.6%）ので、7車だけの参照で
    9車を売った日を測ると**期待を高く置いたまま「大きく下振れた」と読む**。
    2026-08-30 は売った商品の 38% が9車だった。
    """
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_baseline_pool")
    consts = [c.value for c in ast.walk(fn)
              if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    assert "paper9" in consts, "参照分布に9車（paper9）が入っていない"
    assert any("mode IN" in c for c in consts), "mode の絞りが単一値のまま"


def test_参照の構成は車数まで揃える():
    """🔴 プランだけで揃えない。9車を売った日の期待が高く出る。"""
    m = _load()
    pool = {("A_hit", 7): [(10_000, 30_000)], ("A_hit", 9): [(10_000, 0)]}
    got = m._bootstrap(pool, {("A_hit", 9): 2}, n_boot=20, seed=1)
    assert got and all(roi == 0.0 for roi, _ in got), \
        "9車を指定したのに7車の母集団から引いている"


# ─────────── 並び・印の欠測（2026-09-02 是正）───────────
#
# 🔴 **欠測で見送ったこと自体は異常ではない。** ミッドナイト開催は winticket の
#    公開が遅く、朝(07:00)・昼(13:05) の波では構造的に取れない。ガードはレースを
#    確保せずに抜けるので後の波が拾い直す——それが設計。
#    ここを NG にしていた間は、開催がある日はほぼ毎晩「対処不要の異常」が出ていた
#    （実測 2026-08-28〜09-01 の5日中5日）。
# 🔴 異常なのは「レビュー時点でも取れていない」ほう（2026-08-26 熊本の型）。

_MARKED = [{"prediction_mark": 1, "line_group": 1},
           {"prediction_mark": 0, "line_group": 2}]
_NO_MARK = [{"prediction_mark": 0, "line_group": 1},
            {"prediction_mark": 0, "line_group": 2}]
_NO_LINE = [{"prediction_mark": 1, "line_group": 0},
            {"prediction_mark": 2, "line_group": 0}]


def test_後の波で売れた欠測は異常にしない():
    m = _load()
    st = m.classify_lineup({"R1"}, {"R1": _MARKED}, sold={"R1"}, rejudged=set())
    assert st.unresolved == frozenset()
    assert st.recovered == frozenset({"R1"})
    assert st.dropped == frozenset()


def test_後の波で別の理由に変わった欠測も異常にしない():
    """daily_cap / axis_gate で落ちたなら、入力は届いている。"""
    m = _load()
    st = m.classify_lineup({"R1"}, {"R1": _MARKED}, sold=set(), rejudged={"R1"})
    assert st.unresolved == frozenset()
    assert st.recovered == frozenset({"R1"})


def test_レビュー時点でも欠測なら異常():
    m = _load()
    st = m.classify_lineup({"R1", "R2"}, {"R1": _NO_MARK, "R2": _NO_LINE},
                           sold=set(), rejudged=set())
    assert st.unresolved == frozenset({"R1", "R2"})
    assert st.recovered == frozenset()


def test_入力は届いたのに拾い直されなかったレースは別枠():
    """異常ではないが「売れずに終わった」ので数える。"""
    m = _load()
    st = m.classify_lineup({"R1"}, {"R1": _MARKED}, sold=set(), rejudged=set())
    assert st.unresolved == frozenset()
    assert st.recovered == frozenset()
    assert st.dropped == frozenset({"R1"})


def test_出走表が無いレースは異常にしない():
    """「分からない」を理由に異常を立てない（入稿ガードと同じ扱い）。"""
    m = _load()
    st = m.classify_lineup({"R1"}, {}, sold=set(), rejudged=set())
    assert st.unresolved == frozenset()


def test_欠測の判定は入稿ガードへ委譲している():
    """閾値をここへ写すと、片方だけ動いたときに黙って食い違う。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "from src.entry_health import missing_market_inputs" in src
    assert "missing_market_inputs(" in src
    # 正本が返す文言を書いていたら、判定そのものを持ち込んでいる。
    for token in ("WT印が全車ゼロ", "並び（ライン）が未取得"):
        assert token not in src, f"入稿ガードの判定を写経している: {token}"


def test_見送り理由の語彙も正本から取る():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "from src.submission_skips import MISSING_LINEUP" in src
    assert '"missing_lineup"' not in src, "reason_code をリテラルで書いている"


# ─────────── 起点のリセット（2026-09-15・段の商品へ差し替え）───────────


class _Sold:
    def __init__(self, race_key, plan, bet=10_000, pay=0):
        self.race_key, self.rank_key, self.origin = race_key, plan, None
        self.bet, self.payout = bet, pay
        self.hit, self.net_hit, self.n_points = pay > 0, pay >= bet, 5
        self.payouts = [pay] if pay else []


def test_起点は型ラボ全面移行日のまま():
    """🔴 2026-09-15 に一度 09-15 へ移したが、ユーザー決定で 08-29 に戻した（9/15 のみ除外）。"""
    m = _load()
    assert m.REVIEW_EPOCH == "2026-08-29"
    assert m.REVIEW_EPOCH_LABEL == "型ラボ全面移行日"


def test_除外日は2026_09_15だけで理由がある():
    m = _load()
    assert set(m.EXCLUDED_DAYS) == {"2026-09-15"}
    assert "段" in m.EXCLUDED_DAYS["2026-09-15"]


def test_除外日の判定は日付の型に依らない():
    """🔴 `race_date` は DATE と TEXT が混在する。揃えずに比べると除外が黙って効かない。"""
    from datetime import date
    m = _load()
    assert m.is_excluded_day("2026-09-15")
    assert m.is_excluded_day(date(2026, 9, 15))
    assert m.is_excluded_day("20260915")
    assert m.is_excluded_day("2026-09-15 00:00:00")
    assert not m.is_excluded_day("2026-09-14")
    assert not m.is_excluded_day(date(2026, 9, 16))


def test_台帳は起点_08_29_の旧ファイルを読み書きする():
    m = _load()
    assert m.LEDGER == m.ledger_path(m.REVIEW_EPOCH)
    # 🔴 2026-08-29〜の台帳は接尾辞なし（VPS に 09-14 まで 643行ある）
    assert m.LEDGER.name == "type_lab_nightly_ledger.csv"
    # PR#581〜#583 の間にできうる 09-15 起点の台帳は指さない
    assert m.ledger_path("2026-09-15").name == "type_lab_nightly_ledger_20260915.csv"
    assert m.ledger_path("2026-09-15") != m.LEDGER


def test_起点より前の日は台帳へ積まない(tmp_path, monkeypatch):
    m = _load()
    ledger = tmp_path / "ledger.csv"
    monkeypatch.setattr(m, "LEDGER", ledger)
    brk = {"per_plan": {}, "gami_by_plan": {}}
    m.append_ledger("2026-08-28", [_Sold("k", "7C")], brk)
    assert not ledger.exists(), "起点より前の日が台帳へ積まれている"
    m.append_ledger("2026-08-29", [_Sold("k", "A_hit")], brk)
    assert ledger.exists()


def test_除外日は台帳へ積まず既存の行も変えない(tmp_path, monkeypatch):
    m = _load()
    ledger = tmp_path / "ledger.csv"
    monkeypatch.setattr(m, "LEDGER", ledger)
    brk = {"per_plan": {}, "gami_by_plan": {}}
    m.append_ledger("2026-09-15", [_Sold("k", "T_firm")], brk)
    assert not ledger.exists(), "除外日が台帳へ積まれている"
    m.append_ledger("2026-09-14", [_Sold("k", "A_hit")], brk)
    before = ledger.read_text(encoding="utf-8")
    m.append_ledger("2026-09-15", [_Sold("k", "T_firm", pay=50_000)], brk)
    assert ledger.read_text(encoding="utf-8") == before, "除外日の実行で台帳が書き換わった"
    m.append_ledger("2026-09-16", [_Sold("k", "A_hit")], brk)
    dates = {r["date"] for r in csv.DictReader(ledger.open(encoding="utf-8"))}
    assert dates == {"2026-09-14", "2026-09-16"}


def test_発火判定は今の台帳だけを読み除外日を数えない(tmp_path, monkeypatch):
    m = _load()
    head = "date,dim,key,n,bet,payout,n_hits,n_net_hits\n"
    cur = tmp_path / "type_lab_nightly_ledger.csv"
    cur.write_text(head
                   + "2026-09-14,plan,A_hit,7,70000,10000,1,1\n"
                   # 除外日の行が紛れていても数えない
                   + "2026-09-15,plan,A_hit,900,9000000,0,0,0\n", encoding="utf-8")
    other = tmp_path / "type_lab_nightly_ledger_20260915.csv"
    other.write_text(head + "2026-09-15,plan,T_firm,500,5000000,1000,0,0\n",
                     encoding="utf-8")
    monkeypatch.setattr(m, "LEDGER", cur)
    out = "\n".join(m.section_escalate({}))
    assert "累積    7件" in out, out
    assert "907" not in out and "900" not in out, "除外日の行を数えている"
    assert "T_firm" not in out and "500" not in out, "他の起点の台帳が混ざっている"
    assert "累積から除外した日: 2026-09-15" in out


def test_参照の無いプランは発火判定できないと明示する(tmp_path, monkeypatch):
    m = _load()
    ledger = tmp_path / "l.csv"
    monkeypatch.setattr(m, "LEDGER", ledger)
    brk = {"per_plan": {}, "gami_by_plan": {}}
    m.append_ledger("2026-09-16", [_Sold(f"k{i}", "T_upset") for i in range(120)], brk)
    out = "\n".join(m.section_escalate({("A_hit", 7): [(10_000, 0)]}))
    assert "T_upset" in out and "参照分布なし" in out


def test_入稿件数の基準から除外日を外す():
    m = _load()
    counts = {"2026-09-12": 46, "2026-09-13": 55, "2026-09-14": 53,
              "2026-09-15": 79, "2026-09-16": 81}
    # 09-17 の基準: 09-12〜09-16 のうち 09-15 を除く4日 → 46,53,55,81 の [2] = 55
    med, n = m.median_submits(counts, "2026-09-17")
    assert n == 4, "除外日（09-15）を基準日に数えている"
    assert med == 55
    # 除外日自体を流したときの基準にも、除外日は入らない（当日なので元々入らない）
    med, n = m.median_submits(counts, "2026-09-15")
    assert n == 3 and med == 53
    # 起点より前は数えない
    assert m.median_submits({"2026-08-28": 30, "2026-08-29": 40}, "2026-08-30",
                            min_days=1) == (40, 1)
    # 当日は基準に入れない
    assert m.median_submits({"2026-09-16": 79}, "2026-09-16", min_days=1) == (None, 0)


def test_ゲートと自信ありの累積から除外日を落とす():
    from datetime import date
    m = _load()
    rows = [{"race_date": "2026-09-14"}, {"race_date": "2026-09-15"},
            {"race_date": date(2026, 9, 15)}, {"race_date": "2026-09-16"}]
    assert [r["race_date"] for r in m.drop_excluded_days(rows)] == ["2026-09-14", "2026-09-16"]

    class _R:
        def __init__(self, d):
            self.race_date = d
    got = m.drop_excluded_days([_R(date(2026, 9, 15)), _R(date(2026, 9, 16))])
    assert [str(r.race_date) for r in got] == ["2026-09-16"]
    # 本体が実際にこの関数を通していること（§4 の累積・§5 の日ごとの束ね）
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for fn_name in ("section_gate", "section_confident"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        calls = {getattr(c.func, "id", "") for c in ast.walk(fn) if isinstance(c, ast.Call)}
        assert "drop_excluded_days" in calls, f"{fn_name} が除外日を落としていない"


def _stub_db(monkeypatch, mod, sold):
    """DB を引く関数を差し替える（`build_report` / HTML の `build` を DB なしで回す）。"""
    empty = frozenset()
    monkeypatch.setattr(mod, "_sold", lambda day: (list(sold), 0, []))
    monkeypatch.setattr(mod, "_live_rows", lambda day: [])
    monkeypatch.setattr(mod, "_baseline_pool", lambda: {})
    monkeypatch.setattr(mod, "_band_baseline", lambda: {})
    monkeypatch.setattr(mod, "_race_meta", lambda day: {})
    monkeypatch.setattr(mod, "_recent_median_submits", lambda day: (50, 7))
    monkeypatch.setattr(mod, "_skips", lambda day: {})
    monkeypatch.setattr(mod, "_lineup_state",
                        lambda day: mod.LineupState(empty, empty, empty, empty))
    monkeypatch.setattr(mod, "_live_since", lambda s, e: [])
    monkeypatch.setattr(mod, "section_confident", lambda day, n_boot, seed: ["  (stub)"])


def test_除外日を流してもレポートは出て台帳は増えない(tmp_path, monkeypatch):
    m = _load()
    ledger = tmp_path / "type_lab_nightly_ledger.csv"
    monkeypatch.setattr(m, "LEDGER", ledger)
    _stub_db(monkeypatch, m, [_Sold("r1", "T_firm", pay=22_000), _Sold("r2", "T_upset")])

    report, summary, n_ng = m.build_report("2026-09-15", n_boot=10, append=True)
    assert "## §1 異常検知" in report and "## §6" in report
    assert "累積から除外した日: 2026-09-15" in report
    assert "この日（2026-09-15）は累積から除外した日" in report
    assert "売った商品 2件" in summary, "除外日でも当日の数字は出す"
    assert not ledger.exists(), "除外日の実行で台帳が作られた"

    report, _, _ = m.build_report("2026-09-16", n_boot=10, append=True)
    assert "この日（2026-09-16）は累積から除外した日" not in report
    assert ledger.exists(), "除外日でない日は台帳へ積む"


def test_HTML_も除外日を明記する(monkeypatch):
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "nightly_report_html", REPO / "scripts" / "nightly_report_html.py")
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    _stub_db(monkeypatch, h.NR, [_Sold("r1", "T_firm")])
    body, _n_ng, total = h.build("2026-09-15", n_boot=10)
    assert "累積から除外した日" in body
    assert "この日（2026-09-15）は累積から除外した日" in body
    assert total.n_races == 1
    body, _, _ = h.build("2026-09-16", n_boot=10)
    assert "この日（2026-09-16）は累積から除外した日" not in body


def test_基準日が足りない日は件数を黙って_OK_にしない():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "件数の判定なし" in src
    # `----` 行は notify_issues の NG/OK 集計に入らない（情報として出す）
    from importlib import util
    spec = util.spec_from_file_location("ni", REPO / "scripts" / "notify_issues.py")
    ni = util.module_from_spec(spec)
    spec.loader.exec_module(ni)
    ng, n_ok = ni.parse_alerts("## §1 異常検知\n  ---- 入稿 79件 — 件数の判定なし\n"
                               "  [OK] 1レース1商品\n## §2\n")
    assert ng == [] and n_ok == 1


def test_参照分布の無い段の商品は黙って落とさない():
    m = _load()
    pool = {("A_ana", 7): [(10_000, 0)], ("F_line", 9): [(10_000, 30_000)]}
    sold = [_Sold("r1", "T_firm", pay=22_000), _Sold("r2", "T_upset"),
            _Sold("r3", "A_ana"), _Sold("r4", "F_line", pay=30_000)]
    cars = {"r1": 7, "r2": 7, "r3": 7, "r4": 9}
    covered, missing = m.split_by_reference(sold, pool, cars)
    assert [r.race_key for r in covered] == ["r3", "r4"]
    assert missing == {("T_firm", 7): 1, ("T_upset", 7): 1}
    ref = m.reference_block(sold, pool, cars, n_boot=30, seed=1)
    assert ref["n_covered"] == 2 and ref["n_total"] == 4
    # 今日の値は参照のある商品だけで作る（T_firm の当たりを混ぜない）
    assert ref["roi"] == 30_000 / 20_000
    assert any("参照分布なし" in x for x in ref["missing_lines"])
    assert any("旧プランの分布は当てはめない" in x for x in ref["missing_lines"])


def test_段の商品だけの日は参照分布を作らない():
    m = _load()
    pool = {("A_hit", 7): [(10_000, 30_000)]}
    ref = m.reference_block([_Sold("r1", "T_firm")], pool, {"r1": 7}, 30, 1)
    assert ref["boot"] == [], "旧プランの分布を段の商品へ当てはめている"


def test_HTML_は参照分布を本体の関数で作る():
    """🔴 2026-08-30〜09-15 の HTML は構成の鍵がプラン名だけで母集団と合わず、図が出ていなかった。"""
    src = (REPO / "scripts" / "nightly_report_html.py").read_text(encoding="utf-8")
    assert "NR.reference_block(" in src
    assert "NR._bootstrap(" not in src


def test_ゲートの答え合わせから段の商品を外す():
    m = _load()
    rows = [
        {"plan_key": "T_firm", "type_label": "A", "n_entries": 7, "axis_sum": 1.6,
         "race_type": "予選", "cup_grade": None},
        {"plan_key": "T_upset", "type_label": "F", "n_entries": 7, "axis_sum": 1.2,
         "race_type": "予選", "cup_grade": None},
        {"plan_key": "A_hit", "type_label": "A", "n_entries": 7, "axis_sum": 1.6,
         "race_type": "予選", "cup_grade": None},
        {"plan_key": "A_ana", "type_label": "A", "n_entries": 7, "axis_sum": 1.6,
         "race_type": "予選", "cup_grade": None},
        {"plan_key": "A_trio", "type_label": "A", "n_entries": 7, "axis_sum": 1.6,
         "race_type": "予選", "cup_grade": None},
        {"plan_key": "A_hit", "type_label": "A", "n_entries": 9, "axis_sum": 1.6,
         "race_type": "予選", "cup_grade": None},
    ]
    got = [(d["plan_key"], d["n_entries"]) for d in m.gate_population(rows)]
    # 段の商品・閾値の無い A_ana・型別規則で売らない A_trio・9車は入らない
    assert got == [("A_hit", 7)]
    assert "T_firm" in m.SELLABLE_PLAN_KEYS, "前提: 段の商品は売りうるプランに入っている"


def test_自信ありの世代は段を別に数える():
    m = _load()
    assert m.confident_era("T_firm", "2026-09-15") == m.CONFIDENT_ERA_TIER
    assert m.confident_era("F_sign", "2026-09-14").startswith("新EV")
    assert m.confident_era("E_hit", "2026-09-01").startswith("Σp")


def test_段の自信ありは固めの中の無作為と比べる():
    m = _load()
    rows = [_Sold("r1", "T_firm"), _Sold("r2", "T_upset"), _Sold("r3", "T_firm")]
    pool = m.confident_control_pool(m.CONFIDENT_ERA_TIER, rows)
    assert {r.rank_key for r in pool} == {"T_firm"}
    assert len(m.confident_control_pool("新EV（型ラボ・18時前×合成3倍+）", rows)) == 3


def test_狙い帯の無いプランは決着率の分母に入れない():
    """🔴 段の商品は参照が無く狙い帯が決まらない。分母に入れると「狙い帯で決着 0%」に化ける。"""
    m = _load()
    sold = [_Sold("r1", "T_firm"), _Sold("r2", "T_upset")]
    live = [{"race_key": "r1", "plan_key": "T_firm", "win_tf_odds": 12.0},
            {"race_key": "r2", "plan_key": "T_upset", "win_tf_odds": 150.0}]
    out = "\n".join(m.section_landing(sold, live, {}))
    assert "狙い帯（±1帯）で決着" not in out
    assert "狙い帯を決められないプラン: T_firm, T_upset" in out
    assert " 0.0倍" not in out, "参照が無いのに参照中央を 0倍と出している"
