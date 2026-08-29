"""採点結果の焼き付け（`services/keirin_settlement_cache`）を固定する。

## 背景

`/keirin/summary` の当年ぶんは入稿1,091件を**毎リクエスト採点し直して**おり、
`wt_odds`（7.2GB・この DB の shared_buffers は 128MB）への 2,138回の
インデックス参照が冷えると 1.5秒かかっていた（本番実測 2026-08-29:
温 0.48〜0.79秒 / 冷 1.4〜7.0秒）。着順と確定配当が入った後の採点結果は
二度と変わらないので行へ焼き付ける。

🔴 **このキャッシュが静かに古くなるのが唯一の怖い壊れ方**なので、
   「使ってよい条件」をここで機械的に固定する。
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from src.services.keirin_settlement_cache import (
    SETTLE_VERSION,
    cached_settlement,
    fingerprint,
    is_cacheable,
)

_BET = '{"lines":[{"bet_type":"3連複","combo":"1=2=3","stake":10000}]}'


def _row(**kw):
    base = {"settled_fp": fingerprint(_BET), "settled_bet": 10000,
            "settled_payout": 15200, "settled_hit": True, "settled_n_combos": 1}
    base.update(kw)
    return base


def test_hit_returns_the_baked_numbers():
    got = cached_settlement(_row(), _BET)
    assert got == {"bet": 10000, "payout": 15200, "hit": True,
                   "net_hit": True, "n_combos": 1}


def test_net_hit_is_derived_not_stored():
    """🔴 ガミ（払戻 < 賭け金）は**不的中**として数える。

    列に持たず `hit` と金額から出す（列を増やすと食い違う余地だけが増える）。
    """
    got = cached_settlement(_row(settled_payout=9000), _BET)
    assert got is not None
    assert got["hit"] is True and got["net_hit"] is False


def test_bet_detail_change_invalidates():
    """🔴 買い目が差し替わった行のキャッシュを使わないこと。

    keirin 側の再入稿は `ON CONFLICT DO UPDATE SET`（INSERT に並べた列だけ）で
    `bet_detail` を書き換えうるが、キャッシュ列は並ばないので**残る**。
    指紋が無いと「古い買い目の採点結果」を新しい買い目の成績として出す。
    """
    assert cached_settlement(_row(), '{"lines":[]}') is None


def test_settle_version_change_invalidates():
    other = fingerprint(_BET, version=SETTLE_VERSION + 1)
    assert cached_settlement(_row(settled_fp=other), _BET) is None


def test_partial_row_is_not_used():
    """🔴 1つでも欠けていたら実採点へ落とす（投資0円の「外れ」を作らない）。"""
    for key in ("settled_fp", "settled_bet", "settled_payout",
                "settled_hit", "settled_n_combos"):
        assert cached_settlement(_row(**{key: None}), _BET) is None, key


def test_zero_bet_is_not_used():
    """買い目が記録されていない入稿（2026-08-07 以前）は集計から外す側の行。"""
    assert cached_settlement(_row(settled_bet=0), _BET) is None


def test_only_settled_rows_are_cacheable():
    """🔴 「まだ分からない」を焼かない。

    焼くと当たっているレースが永久に「外れ・払戻0円」で固定される。
    """
    assert is_cacheable(settled=True, bet=10000)
    assert not is_cacheable(settled=False, bet=10000)
    assert not is_cacheable(settled=True, bet=0)


# ---------------------------------------------------------------------------
# 採点ロジックを変えたら SETTLE_VERSION を上げる
# ---------------------------------------------------------------------------

#: `keirin_settlement.py` の AST（docstring を除く）の指紋。
#: 🔴 **これが変わったら「焼いた結果の意味が変わったか」を判断すること。**
#:    変わったなら `SETTLE_VERSION` を上げる（全行の指紋がずれてキャッシュが
#:    自動的に作り直される）。変わっていない（整形・リネームだけ）なら
#:    この定数を新しい値へ更新する。
#: コメントと docstring の手直しでは落ちない（AST を比べているため）。
_SETTLEMENT_AST_DIGEST = "9f7b81d07867d452"


def _ast_digest(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef
                          | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return hashlib.sha256(
        ast.dump(ast.fix_missing_locations(tree)).encode()).hexdigest()[:16]


def test_settlement_logic_unchanged_since_version_was_set():
    path = Path(__file__).resolve().parent.parent / "src" / "services" / "keirin_settlement.py"
    digest = _ast_digest(path)
    assert digest == _SETTLEMENT_AST_DIGEST, (
        f"keirin_settlement.py の中身が変わっています（{digest}）。\n"
        "採点の**意味**が変わったなら keirin_settlement_cache.SETTLE_VERSION を上げ、\n"
        "変わっていない（整形・リネームだけ）ならこのテストの "
        "_SETTLEMENT_AST_DIGEST を更新してください。\n"
        "🔴 何もせず放置すると、古い採点で焼いた結果を新しい採点の結果として"
        "出し続けます（画面もログもエラーを出しません）。"
    )
