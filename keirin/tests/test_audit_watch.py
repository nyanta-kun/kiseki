"""監査後の前向き観測（`scripts/audit_watch.py`）。

守るのは4点:
  1. **構成の差分を見落とさない**——「商品を据え置く」が破れたら 🔴 を出す
  2. **既定は「判定しない」**——件数が足りない層・実売で判定しない層を
     数字だけで見せない（監査 §4.2 の教訓）
  3. **期待帯を外れたときだけ判定する**——CI が帯と重なる限り「想定どおり」
  4. **定数が改名されても落ちない**——夜間チェーンを道連れにしない

🔴 DB に触るのは集計側だけ。ここで検査するのは純関数だけにする
   （テストが本番 DB に繋がると CI で落ちる）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "audit_watch", REPO / "scripts" / "audit_watch.py")
AW = importlib.util.module_from_spec(_spec)
sys.modules["audit_watch"] = AW
_spec.loader.exec_module(AW)


# ── ① 構成の差分 ───────────────────────────────────────────────
def test_構成が同じなら差分は出ない():
    cfg = {"a": 1, "b": ["x", "y"]}
    assert AW.diff_config(cfg, dict(cfg)) == []


def test_値が変わったら差分に出る():
    got = AW.diff_config({"a": 1}, {"a": 2})
    assert got == [("a", 1, 2)]


def test_定数が消えても落ちずに差分として見える():
    """🔴 改名も「構成が動いた」。例外にすると夜間チェーンごと止まる。"""
    got = AW.diff_config({"type_lab.X": 5}, {})
    assert got == [("type_lab.X", 5, AW._MISSING)]


def test_定数の取り出しは例外を投げない():
    mod = SimpleNamespace(A=1, B=frozenset({"b", "a"}))
    assert AW._val(mod, "A") == 1
    assert AW._val(mod, "B") == ["a", "b"]          # 集合は順序を固定して比較可能に
    assert AW._val(mod, "NOPE") == AW._MISSING


# ── ② 既定は「判定しない」 ─────────────────────────────────────
def test_件数が足りなければ判定しない():
    assert "判定不能" in AW._verdict((0.1, 0.2), (0.7, 0.9), n=10, min_n=300)


def test_実売で判定しない層は参考と書く():
    """高額枠は的中 3〜5%。n がいくら積もっても実売では判定しない（監査 §4.2）。"""
    got = AW._verdict((0.1, 0.2), (0.7, 0.9), n=10_000, min_n=AW.NEVER_JUDGE)
    assert got == "参考（実売では判定しない）"


def test_高額枠と看板枠は判定しない設定になっている():
    never = {label for label, _, _, _, min_n in AW.LAYERS if min_n == AW.NEVER_JUDGE}
    assert any("高額枠" in x for x in never)
    assert any("看板枠" in x for x in never)


def test_CIが無ければ判定しない():
    assert AW._verdict(None, (0.7, 0.9), n=10_000, min_n=300) == "判定不能"


# ── ③ 期待帯との突き合わせ ─────────────────────────────────────
def test_CIが期待帯と重なるなら想定どおり():
    assert AW._verdict((0.65, 0.95), (0.7, 0.9), n=1_000, min_n=300) == "想定どおり"


def test_CIが期待帯より完全に下なら赤を出す():
    got = AW._verdict((0.30, 0.55), (0.7, 0.9), n=1_000, min_n=300)
    assert got.startswith("🔴")


def test_CIが期待帯より完全に上なら緑を出す():
    got = AW._verdict((0.95, 1.20), (0.7, 0.9), n=1_000, min_n=300)
    assert got.startswith("🟢")


# ── ④ ブートストラップは日でクラスタする ───────────────────────
def _race(day: str, payout: int) -> AW.SoldRace:
    return AW.SoldRace(race_key=f"{day}_x", race_date=day, rank_key="C_hit",
                       origin="rank", bet=10_000, payout=payout,
                       hit=payout > 0, net_hit=payout > 10_000, n_points=5)


def test_1日しかなければCIを出さない():
    """🔴 日クラスタなので1日ではリサンプリングできない。無理に出さない。"""
    hit, roi = AW._boot_ci([_race("2026-09-20", 0)], n_boot=50, seed=1)
    assert hit is None and roi is None


def test_複数日あればCIが出る():
    races = [_race(f"2026-09-{d:02d}", p)
             for d, p in ((10, 0), (11, 30_000), (12, 0), (13, 20_000))]
    _, roi = AW._boot_ci(races, n_boot=500, seed=1)
    assert roi is not None and roi[0] <= roi[1]


def test_層の切り分けは高額枠と看板枠を本線から外す():
    races = [
        AW.SoldRace("r1", "2026-09-20", "C_hit", "rank", 10_000, 0, False, False, 5),
        AW.SoldRace("r2", "2026-09-20", "C_sign", "highpay_fill", 10_000, 0, False, False, 5),
        AW.SoldRace("r3", "2026-09-20", "F_sign", "rank", 10_000, 0, False, False, 5),
    ]
    assert [r.race_key for r in AW._layer(races, "base")] == ["r1"]
    assert [r.race_key for r in AW._layer(races, "highpay")] == ["r2"]
    assert [r.race_key for r in AW._layer(races, "signboard")] == ["r3"]
    assert len(AW._layer(races, "all")) == 3


# ── ⑤ 基準ファイル ─────────────────────────────────────────────
def test_構成の基準が存在し監査時点の値を持っている():
    """🔴 基準が無いと「据え置き」を機械的に確認できない。"""
    import json
    got = json.loads(AW.BASELINE.read_text(encoding="utf-8"))
    cfg = got["config"]
    assert cfg["type_lab.HIGHPAY_SLOTS_PER_DAY"] == 5      # PR#588
    assert cfg["type_lab.TIER_SELL_ENABLED"] is False      # 段は停止中
    assert cfg["confident.CONFIDENT_MIN_SYNTH_ODDS"] == 2.5  # PR#589


def test_基準が無ければ撮り直しを促す():
    lines, drifted = AW.section_config(REPO / "docs" / "nope.json")
    assert drifted is False
    assert any("--write-baseline" in x for x in lines)
