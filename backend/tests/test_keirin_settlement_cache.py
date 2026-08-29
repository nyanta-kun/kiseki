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
import shutil
import subprocess
from pathlib import Path

import pytest

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

#: `keirin_settlement.py` の**コードだけ**（docstring・コメントを除く）の指紋。
#: 🔴 **これが変わったら「焼いた結果の意味が変わったか」を判断すること。**
#:    変わったなら `SETTLE_VERSION` を上げる（全行の指紋がずれてキャッシュが
#:    自動的に作り直される）。変わっていない（整形・リネームだけ）なら
#:    この定数を新しい値へ更新する。
_SETTLEMENT_CODE_DIGEST = "bc57f18cadb4e402"

_SETTLEMENT_PY = (Path(__file__).resolve().parent.parent
                  / "src" / "services" / "keirin_settlement.py")

#: 版をまたいで同じ指紋になるかを確かめるための、外部プロセス用のワンライナー。
_DIGEST_SNIPPET = (
    "import ast,hashlib,sys;"
    "t=ast.parse(open(sys.argv[1],encoding='utf-8').read());"
    "[setattr(n,'body',n.body[1:] or [ast.Pass()])"
    " for n in ast.walk(t)"
    " if isinstance(n,(ast.Module,ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef))"
    " and n.body and isinstance(n.body[0],ast.Expr)"
    " and isinstance(n.body[0].value,ast.Constant)"
    " and isinstance(n.body[0].value.value,str)];"
    "print(hashlib.sha256(ast.unparse(t).encode()).hexdigest()[:16])"
)


def _code_digest(src: str) -> str:
    """docstring・コメント・空白を落としたコードの指紋。

    🔴 **`ast.dump()` を使ってはいけない。** ノードのフィールドは Python の版で
       増えるため、**同じファイルでも版が違うと違う値になる**。
       実際にこれで CI が落ちた（2026-08-29: ローカル 3.14 と CI 3.12 で不一致・
       ファイルは無変更）。版ごとに違う指紋は「中身が変わった」と嘘をつき、
       定数を機械的に貼り替える習慣を作るだけでガードとして死ぬ。
       `ast.unparse()` は 3.11〜3.14 で同一（`test_digest_is_stable_across_python`）。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef
                          | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return hashlib.sha256(ast.unparse(tree).encode()).hexdigest()[:16]


def test_settlement_logic_unchanged_since_version_was_set():
    digest = _code_digest(_SETTLEMENT_PY.read_text(encoding="utf-8"))
    assert digest == _SETTLEMENT_CODE_DIGEST, (
        f"keirin_settlement.py の中身が変わっています（{digest}）。\n"
        "採点の**意味**が変わったなら keirin_settlement_cache.SETTLE_VERSION を上げ、\n"
        "変わっていない（整形・リネームだけ）ならこのテストの "
        "_SETTLEMENT_CODE_DIGEST を更新してください。\n"
        "🔴 何もせず放置すると、古い採点で焼いた結果を新しい採点の結果として"
        "出し続けます（画面もログもエラーを出しません）。"
    )


# --- ガードそのものが働くことを確かめる（定数を貼り替えるだけの儀式にしない）---

_SRC_BASE = '''
def settle(bet, payout):
    """採点する。"""
    # 払戻が賭け金以上なら的中
    return payout >= bet
'''


def test_digest_ignores_docstrings_and_comments():
    """🔴 説明の書き足しで落ちないこと。

    このリポジトリは docstring を頻繁に書き足す。そこで落ちるガードは
    「とりあえず定数を貼り替える」習慣を作り、本当の変更まで通してしまう。
    """
    edited = (_SRC_BASE
              .replace("採点する。", "採点する（2026-08-29 追記）。")
              .replace("# 払戻が賭け金以上なら的中", "# ガミは不的中"))
    assert edited != _SRC_BASE
    assert _code_digest(edited) == _code_digest(_SRC_BASE)


def test_digest_reacts_to_a_logic_change():
    """🔴 判定を変えたら必ず落ちること（ガミの境界を1つずらす）。"""
    changed = _SRC_BASE.replace("payout >= bet", "payout > bet")
    assert _code_digest(changed) != _code_digest(_SRC_BASE)


def test_digest_is_stable_across_python():
    """🔴 Python の版が違っても同じ値になること。

    `ast.dump()` はノードのフィールドが版で増えるため使えない
    （実測 2026-08-29: 3.11 / 3.12 / 3.13+ で3通りに割れ、CI だけが落ちた）。
    手元にある Python 全部で `ast.unparse` の指紋が一致することを確かめる
    （1つしか無い環境ではスキップ）。
    """
    exes = [p for v in ("3.11", "3.12", "3.13", "3.14")
            if (p := shutil.which(f"python{v}"))]
    if len(exes) < 2:
        pytest.skip("比較できる Python が1つしかない")
    got = {
        exe: subprocess.run([exe, "-c", _DIGEST_SNIPPET, str(_SETTLEMENT_PY)],
                            capture_output=True, text=True, check=True).stdout.strip()
        for exe in exes
    }
    assert len(set(got.values())) == 1, f"版によって指紋が変わる: {got}"
    assert next(iter(got.values())) == _SETTLEMENT_CODE_DIGEST
