"""入稿の出自（`netkeirin_submissions.origin`）の配線を固定する（2026-08-11）。

## 背景（この検査が要る理由）

看板レースの穴埋め入稿（`submit_marquee_wt.py`）は
`RANK_BY_CARS = {7: "7A", 9: "9A"}` により **7A/9A を名乗って入稿する**。
そのため `rank_key` だけではゲート通過分と穴埋めを区別できず、
実測（2026-08-01〜08-10）では **7A 入稿52件中49件＝94%が穴埋め**だった。
経路で割ると成績はまったく別物になる:

| 経路 | R数 | 売上 | 表示的中率 | 回収率 |
|---|---|---|---|---|
| ゲート通過 | 107 | 25.4% | 29.0% | 0.702 |
| 穴埋め | 87 | **71.2%** | 14.9% | 0.333 |

## 何を守るか

`origin` は **入稿した時点でしか分からない**（後から復元できるのは推定だけ）。
配線が外れると、その日から静かに全部 `rank` として記録され、
**ランクの成績評価がまた汚染される**。しかも数字は自然に見えるので気づけない。

⚠️ 検査は **AST で構造を見る**。grep は docstring やコメントを拾って
   偽陽性で通ってしまう（過去2回踏んでいる:
   memory `keirin_submit_approval_ui_2026_08_11`）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUBMIT = ROOT / "scripts" / "netkeirin_submit_wt.py"

_TREE = ast.parse(SUBMIT.read_text(encoding="utf-8"))


def _func(name: str) -> ast.FunctionDef:
    for node in ast.walk(_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() が見つかりません（構造が変わりました）")


def _record_calls_in(func: ast.FunctionDef) -> list[ast.Call]:
    return [n for n in ast.walk(func)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_record_submission"]


def _origin_kwarg(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "origin":
            return kw.value
    return None


def test_出自の定数が3種そろっている():
    """値を各所に散らさない（ランク一覧の手書き二重管理で事故った前例がある）。"""
    assigned = {
        t.id: n.value.value
        for n in ast.walk(_TREE)
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
        for t in n.targets if isinstance(t, ast.Name)
    }
    assert assigned.get("ORIGIN_RANK") == "rank"
    assert assigned.get("ORIGIN_MARQUEE_FILL") == "marquee_fill"
    assert assigned.get("ORIGIN_MANUAL") == "manual"


def test_記録関数がoriginを受け取り既定はrank():
    """既定を `rank` にしておくのは、呼び出しの取りこぼしを
    「ゲート通過として記録」に倒すため（NOT NULL 違反で入稿ごと落とさない）。"""
    fn = _func("_record_submission")
    names = [a.arg for a in fn.args.args]
    assert "origin" in names, "_record_submission が origin を受け取っていません"
    # 既定値は末尾の引数から順に対応する
    n_defaults = len(fn.args.defaults)
    default_map = dict(zip(names[len(names) - n_defaults:], fn.args.defaults))
    origin_default = default_map.get("origin")
    assert isinstance(origin_default, ast.Name) and origin_default.id == "ORIGIN_RANK", (
        "origin の既定値が ORIGIN_RANK ではありません"
    )


def test_記録関数のINSERTがorigin列を書いている():
    """引数で受け取っても SQL に無ければ何も記録されない（黙って既定値のまま）。

    ⚠️ **docstring を拾わないこと。** 隣接する文字列リテラルはパーサが1つの
       Constant へ畳むので、「INSERT を含む定数」だけを対象にすれば
       説明文が混ざらない。
    """
    fn = _func("_record_submission")
    sqls = [n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "INSERT" in n.value.upper()]
    assert len(sqls) == 1, f"INSERT 文が1つではありません（{len(sqls)}個）"
    sql = sqls[0]
    assert "origin" in sql, "INSERT 文に origin 列がありません"

    # 列数とプレースホルダ数がずれると実行時に落ちる（本番でしか走らない箇所）。
    cols = sql.split("(", 1)[1].split(")", 1)[0]
    n_cols = cols.count(",") + 1
    n_marks = sql.count("?")
    assert n_cols == n_marks, f"列数({n_cols})とプレースホルダ数({n_marks})が一致しません"

    # 実引数の数も合っていること（SQL だけ直して値を渡し忘れる事故を潰す）。
    call = next(n for n in ast.walk(fn)
                if isinstance(n, ast.Call)
                and getattr(getattr(n.func, "attr", None), "__str__", str)() == "execute")
    values = call.args[1]
    assert isinstance(values, ast.Tuple), "execute の第2引数がタプルではありません"
    assert len(values.elts) == n_marks, (
        f"渡している値の数({len(values.elts)})がプレースホルダ数({n_marks})と違います"
    )


def test_手動経路はrankとして記録しない():
    """🔴 これがこのファイルの本体。

    `_process_manual` はゲートを通っていない入稿（看板の穴埋め or 手動）。
    ここが `rank` になると 7A/9A に穴埋めが混ざり、ランクの成績が測れなくなる。
    """
    calls = _record_calls_in(_func("_process_manual"))
    assert calls, "_process_manual が _record_submission を呼んでいません"
    for call in calls:
        origin = _origin_kwarg(call)
        assert origin is not None, (
            "_process_manual の _record_submission に origin が渡っていません。"
            "既定の ORIGIN_RANK で記録され、穴埋めがゲート通過に混ざります"
        )
        # marquee フラグで分岐していること（三項演算子）。
        assert isinstance(origin, ast.IfExp), (
            "origin が marquee で分岐していません（穴埋めと手動が区別できません）"
        )
        branches = {getattr(origin.body, "id", None), getattr(origin.orelse, "id", None)}
        assert branches == {"ORIGIN_MARQUEE_FILL", "ORIGIN_MANUAL"}, (
            f"分岐先が想定と違います: {branches}"
        )
        assert getattr(origin.test, "id", None) == "marquee", (
            "分岐条件が marquee フラグではありません"
        )


def test_ランク経路はrankとして記録する():
    fn = _func("_process_rank")
    calls = _record_calls_in(fn)
    assert calls, "_process_rank が _record_submission を呼んでいません"
    for call in calls:
        origin = _origin_kwarg(call)
        # 既定が ORIGIN_RANK なので省略も許すが、書いてあるなら ORIGIN_RANK であること。
        if origin is not None:
            assert getattr(origin, "id", None) == "ORIGIN_RANK", (
                "ゲート通過経路が rank 以外で記録されています"
            )


@pytest.mark.parametrize("name", ["_process_manual", "_process_rank"])
def test_記録呼び出しが1関数に1つだけ(name: str):
    """呼び出しが増えたら、その追加分にも origin を渡す必要がある。
    見落とすと**その経路だけ静かに rank として記録される**ので、数で縛る。"""
    assert len(_record_calls_in(_func(name))) == 1, (
        f"{name}() の _record_submission 呼び出しが増減しています。"
        "追加した呼び出しにも origin を渡してください"
    )
