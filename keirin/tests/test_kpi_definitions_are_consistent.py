"""KPI の定義が経路で割れていないことを固定する（2026-09-21 新設）。

## なぜ要るか

同じ名前の量が経路ごとに違う定義で計算されていた。数字は小さくても、
**比べた瞬間に誤読を生む**（「Web と夜間レビューで ROI が違う」「同じ払戻中央が
スクリプトによって 5% 高い」）。

| 量 | 正しい定義 | 割れていた箇所（是正済み） |
|---|---|---|
| 表示的中 | 払戻 **>** 賭け金（元返しはガミ側） | `keirin_settlement.net_hit` が `>=`、`common.summarize` が `<`、`nightly_review_type_lab` が `>=` |
| 投資額 | `budget − void_refund`（欠車返還は賭かっていない） | `nightly_review_type_lab` / `type_lab_design_report` が引いていなかった |
| ガミ / 2倍+ の基準額 | 同じ純額 | Web の router が ROI 分母だけ純額で、判定は gross |
| 払戻中央の母集団 | **的中した全件**（ガミを含む） | `type_lab_design_report` / `axis_gate_audit` がガミ除外後 |

実測の規模: 表示的中の不等号 40行 / 投資額 ROI +0.072pt / ガミ判定 0行 /
払戻中央 live +3.9%・paper +5.6%。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KISEKI = REPO.parent


def _read(rel: str) -> str:
    p = (KISEKI / rel)
    assert p.exists(), f"見つからない: {rel}"
    return p.read_text(encoding="utf-8")


# ───────────────── 表示的中は `払戻 > 賭け金` ─────────────────

def test_settlement_net_hit_uses_strict_greater():
    """正本。元返し（払戻＝賭け金）は表示的中に数えない。"""
    src = _read("backend/src/services/keirin_settlement.py")
    assert "self.payout > self.bet" in src
    assert "self.payout >= self.bet" not in src


def test_harness_gami_matches_the_canonical():
    """`common.summarize` のガミは `pay <= inv`（＝表示的中は `>`）。"""
    src = _read("keirin/scripts/exp_type_lab/common.py")
    assert 'gami = [r for r in hits if r["pay"] <= r["inv"]]' in src


def test_nightly_review_uses_strict_greater():
    src = _read("keirin/scripts/nightly_review_type_lab.py")
    assert "hits += int(p > b)" in src
    assert "hits += int(p >= b)" not in src


def test_synth_floor_local_summarize_matches():
    src = _read("keirin/scripts/exp_type_lab/synth_floor.py")
    assert 'shown = sum(1 for r in rs if r["pay"] > r["bet"])' in src
    assert 'shown = sum(1 for r in rs if r["pay"] >= r["bet"])' not in src


# ───────────────── 投資額は純額（欠車返還を除く） ─────────────────

@pytest.mark.parametrize("rel", [
    "keirin/scripts/nightly_review_type_lab.py",
    "keirin/scripts/type_lab_design_report.py",
    "backend/src/api/keirin_type_lab_router.py",
])
def test_void_refund_is_subtracted(rel):
    """Web だけが引いていて夜間レビューが引かない、という状態に戻さない。"""
    assert "void_refund" in _read(rel), rel


def test_router_uses_the_same_base_for_gami_and_two_plus():
    """🔴 ROI の分母だけ純額で、ガミ/2倍+ は gross、という混在に戻さない。"""
    src = _read("backend/src/api/keirin_type_lab_router.py")
    assert '(x["payout"] or 0) <= _net(x)' in src
    assert '(x["payout"] or 0) >= 2 * _net(x)' in src
    assert '2 * int(x["budget"])' not in src


# ───────────────── 払戻中央の母集団は「的中した全件」 ─────────────────

def test_median_payout_population_is_all_hits():
    """ガミ除外後で取ると系統的に高く出る（live +3.9% / paper +5.6%）。"""
    rep = _read("keirin/scripts/type_lab_design_report.py")
    assert 'hits = [r["pay"] for r in sub if r["pay"] > 0]' in rep
    assert "med=st.median(hits)" in rep
    aud = _read("keirin/scripts/exp_type_lab/axis_gate_audit.py")
    assert 'med=st.median([r["pay"] for r in rs if r["pay"] > 0]' in aud


# ───────────────── 件/日 の分母は窓の全開催日 ─────────────────

def test_per_day_denominator_is_the_whole_window():
    """そのセグメントが出た日数を分母にしない（実測 最大1.78倍過大）。"""
    src = _read("keirin/scripts/exp_type_lab/type_d.py")
    assert "C.days_of(C.select(None, w))" in src
    # 絞った idx をそのまま分母にしている箇所が残っていないこと
    assert not re.search(r"nd = C\.days_of\(idx\)", src)
