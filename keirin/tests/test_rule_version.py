"""`picks_history.rule_version` の取りこぼしを検出する。

## なぜ要るのか

`picks_history` は日次では**当月しか再構築しない**
（`reconcile_walkforward_tail.sh` は `--tail-only`）。過去月は書かれた当時の
コードのまま残るので、閾値や買い方を変えると台帳が静かに世代混在する。

🔴 実害: 2026-08-18 の 7S 閾値調査で「axis_sum 1.40〜1.50 の帯はどのランクも
   出していない」と判定したが、参照した RANK_7S 行の一部は **7S の上限が
   1.50 だった時代**のもので当然その帯を含んでいた。混在は例外を出さない。

版を書き忘れた経路が1つでもあると、そこだけ NULL になって同じ穴が空く。
**本番の書き込み経路を全件走査**して検査する（手で列挙すると次に増えた経路で漏れる）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

#: 本番で picks_history へ書く経路。実験用スクリプト（exp_*）は対象外。
_PROD_WRITERS = (
    "src/wt_rebuild_common.py",
    "scripts/notify_prerace_wt.py",
)


def _statements(src: str) -> list[str]:
    """INSERT ... picks_history ... の1文を、隣接文字列を連結して取り出す。"""
    out = []
    for m in re.finditer(r'"INSERT (?:OR REPLACE )?INTO picks_history ', src):
        chunk = src[m.start():m.start() + 900]
        # 連続する "..." を連結（Python の暗黙連結を再現）
        joined = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', chunk))
        out.append(joined)
    return out


def test_every_production_insert_writes_rule_version():
    missing = []
    for rel in _PROD_WRITERS:
        src = (REPO / rel).read_text(encoding="utf-8")
        for st in _statements(src):
            head = st.split("VALUES")[0]
            if "rule_version" not in head:
                missing.append((rel, head[:120]))
    assert not missing, (
        "picks_history へ rule_version を書いていない経路がある: "
        f"{missing}。書き忘れるとその世代だけ NULL になり、混在の検出が効かない")


def test_version_changes_when_a_constant_changes():
    """定数を1つ変えれば版が変わること（変わらなければ検出の意味が無い）。"""
    import src.strategy_wt as S
    before = S.rank_rule_version("7S")
    old = S.RANK_7S_AXIS_SUM_MAX
    try:
        S.RANK_7S_AXIS_SUM_MAX = old + 0.05
        assert S.rank_rule_version("7S") != before
    finally:
        S.RANK_7S_AXIS_SUM_MAX = old
    assert S.rank_rule_version("7S") == before, "戻したのに版が一致しない＝不安定"


def test_version_is_stable_across_calls():
    """同じ定数なら何度呼んでも同じ（辞書順や id に依存しないこと）。"""
    from src.strategy_wt import rank_rule_version
    for rank in ("7S", "7C", "7H1", "9C"):
        assert rank_rule_version(rank) == rank_rule_version(rank)
    # "RANK_" 接頭辞の有無で変わらない
    assert rank_rule_version("7C") == rank_rule_version("RANK_7C")


def test_merged_rank_sources_match_the_merged_backfill():
    """🔴 統合ランクの構成が実装とずれていないこと。

    RANK_7S は 旧7S ∪ 旧7A ∪ 旧7SS。統合元の定数を版に含めないと、
    **7A の閾値だけ変えたときに 7S の版が動かない**（混在が検出できない）。
    """
    from scripts.backfill_7s_merged_rank_wt import _SOURCES
    from src.strategy_wt import MERGED_RANK_SOURCES
    actual = tuple(suffix for _mod, suffix in _SOURCES)
    assert MERGED_RANK_SOURCES["7S"] == actual, (
        f"統合構成がずれている: strategy_wt={MERGED_RANK_SOURCES['7S']} / "
        f"backfill={actual}")


def test_merged_rank_version_reacts_to_component_constants():
    """7A の定数を変えたら 7S の版も変わること（統合ランクなので）。"""
    import src.strategy_wt as S
    before = S.rank_rule_version("7S")
    old = S.RANK_7A_TOP2_FALLBACK if hasattr(S, "RANK_7A_TOP2_FALLBACK") else None
    names = [n for n in dir(S) if n.startswith("RANK_7A_")
             and isinstance(getattr(S, n), (int, float)) and not isinstance(getattr(S, n), bool)]
    if not names:
        return                      # 7A にスカラー定数が無ければ検査不要
    name = names[0]
    orig = getattr(S, name)
    try:
        setattr(S, name, orig + 1)
        assert S.rank_rule_version("7S") != before, (
            f"{name} を変えても 7S の版が動かない＝統合元が版に入っていない")
    finally:
        setattr(S, name, orig)
