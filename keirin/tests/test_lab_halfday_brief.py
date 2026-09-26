"""半日レビューの Discord 要約（2026-09-27 ユーザー要望「ポイントのみ簡略に」）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "lab_halfday_review.py"
_spec = importlib.util.spec_from_file_location("lab_halfday_review", _PATH)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _t(dim, name, eff):
    return {"cell": (dim, name), "effect": eff, "n": 150, "plans": 5, "lo": -0.5, "hi": -0.1}


def _odds(won_ratio):
    return [{"pred_odds": 10, "final_odds": 10 * won_ratio, "won": True}] * 5


def test_裏付けで注意と参考に分ける():
    scored = [(_t("会場", "四日市", -0.545), "中"), (_t("開催日次", "4日目", -0.335), "強"),
              (_t("会場", "前橋", -0.418), "弱"), (_t("型", "C", 0.21), "中")]
    s = m.brief("昼", 31, 10453, 0.757, _odds(0.86), scored)
    lines = s.splitlines()
    assert lines[1] == "ROI 75.7%（直近31日・10,453件）"
    assert "🎯 オッズ: 当たる目は予測より 14% 安い" in lines
    assert "⚠️ 注意: 四日市 −55pt・4日目 −34pt" in lines
    assert "✅ 好調: C +21pt" in lines
    assert "💤 参考（裏付け弱）: 前橋 −42pt" in lines
    assert len(lines) == 6


def test_傾向が無ければ一行で済ませる():
    s = m.brief("夜", 31, 100, 0.8, [], [])
    assert "🎯 オッズ: 突き合わせ待ち" in s and "傾向: 目立つ偏りなし" in s
    assert "⚠️" not in s and "💤" not in s


def test_裏付けの強さを誇張しない():
    t = _t("会場", "X", -0.4)
    assert m.strength_of(t, None) == "弱"
    assert m.strength_of(t, -0.01) == "弱"      # 長期は実質ゼロ
    assert m.strength_of(t, +0.10) == "弱"      # 逆符号
    assert m.strength_of(t, -0.10) == "中"      # 同符号だが直近が4倍
    assert m.strength_of(t, -0.30) == "強"


def test_Discordへは要約を送る():
    src = _PATH.read_text(encoding="utf-8")
    assert "post_discord(short)" in src and "post_discord(text)" not in src
