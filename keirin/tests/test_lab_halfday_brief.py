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


def test_Discordへはカードを送り失敗時だけテキスト():
    src = _PATH.read_text(encoding="utf-8")
    assert "post_embed(card) or post_discord(short)" in src
    assert "post_discord(text)" not in src


def test_カードの中身():
    scored = [(_t("会場", "四日市", -0.545), "中"), (_t("会場", "前橋", -0.418), "弱")]
    e = m.brief_embed("昼", 31, 10453, 0.757, _odds(0.86), scored)
    f = {x["name"]: x for x in e["fields"]}
    assert f["ROI"]["value"] == "**75.7%**" and f["ROI"]["inline"]
    assert f["当たる目のオッズ"]["value"] == "**予測より 14% 安い**"
    assert f["⚠️ 注意"]["value"] == "**四日市 −55pt**"
    assert f["💤 参考（裏付け弱）"]["value"] == "前橋 −42pt"
    assert e["color"] == 0xDC2626                      # 注意あり＝赤
    assert m.brief_embed("", 1, 1, 1.0, [], [])["color"] == 0x6B7280


def test_カードの上限を切る(monkeypatch):
    """上限超えは 400 で1枚ごと落ちるので、送る前に切る。"""
    import sys
    sys.path.insert(0, str(_PATH.parent.parent))
    from src.notify import discord as d

    sent = {}

    class _R:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _open(req, timeout=0):
        import json
        sent.update(json.loads(req.data))
        return _R()

    monkeypatch.setattr(d, "_load_webhook_url", lambda ch: "http://x")
    monkeypatch.setattr(d.urllib.request, "urlopen", _open)
    assert d.send_embed({"title": "t" * 300, "fields": [{"name": "a", "value": "v" * 2000}] * 30},
                        channel="review")
    e = sent["embeds"][0]
    assert len(e["title"]) == 256 and len(e["fields"]) == 25
    assert len(e["fields"][0]["value"]) == 1024
