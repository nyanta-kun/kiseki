"""地方 5カテゴリ推奨の「実稼働停止・参考保持」を両側から固定する。

## 背景（2026-09-01）

5カテゴリ推奨（sweet_spot / place_bet / upset_place / low_odds_*）は
2026-08-05 に画面から外され、実測でも消費者ゼロだった
（フロントの呼び出し元は参照ゼロのコンポーネント2つだけ・cron / backend/scripts
からの利用も 0 件）。誰も見ないものを毎リクエスト算出していたため、
HTTP エンドポイント `GET /api/chihou/recommendations/sweet-spot` を撤去した。

**ただしコードは参考として残す**——条件そのものが他の推奨のブラッシュに
使えるかもしれないため。

## 何を守るか

この2つは**逆向き**なので、両方を明示的に固定しないと片方に倒れる。

1. **停止側**: エンドポイントが黙って復活しないこと。
   （消費者ゼロのまま毎回算出に戻るのを防ぐ）
2. **保持側**: 参考として残すと決めたコードが「使われていないから」と
   後で削除されないこと。
   ⚠️ 判定関数（`chihou_is_sweet_spot` / `chihou_is_place_bet` 等）は
   **レース詳細の個別馬バッジで現役**。停止したのは 5カテゴリの配信だけで、
   条件の実装は生きている。ここを混同して消すと画面のバッジが落ちる。
"""

from __future__ import annotations

import inspect
from pathlib import Path

from src.api import chihou_recommendations_router
from src.main import app

ROUTER_SRC = Path(inspect.getfile(chihou_recommendations_router)).read_text(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[2]
PANEL = REPO_ROOT / "frontend" / "src" / "components" / "ChihouRecommendPanelClient.tsx"
PANEL_WRAPPER = REPO_ROOT / "frontend" / "src" / "components" / "ChihouRecommendPanel.tsx"


def _routes() -> set[str]:
    """配信されている実パス。

    ⚠️ `app.routes` を直接見てはいけない。この FastAPI 版は include したルータを
       `_IncludedRouter` のまま保持し平坦化しないので `.path` が None になり、
       **検査が素通りする**（実際に一度これで誤検知した）。
       OpenAPI のパス一覧なら配信面そのものを見られる。
    """
    return set(app.openapi()["paths"])


class Test停止:
    def test_sweet_spot_エンドポイントは配信していない(self) -> None:
        offenders = sorted(p for p in _routes() if p.endswith("/sweet-spot"))
        assert not offenders, (
            f"撤去したはずの 5カテゴリ推奨が配信に戻っています: {offenders}。"
            " 復帰させる場合は chihou_recommendations_router.py のコメントの手順に従い、"
            " このテストも意図的に更新してください。"
        )

    def test_生きているエンドポイントは残っている(self) -> None:
        """停止したのは 5カテゴリだけ。DB 経路の4本は cron と外部 Routine が使う。"""
        paths = _routes()
        base = "/api/chihou/recommendations"
        for suffix in ("", "/source", "/submit", "/update-results", "/update-odds-decision"):
            assert base + suffix in paths, f"{base + suffix} が消えています"


class Test参考保持:
    def test_生成ロジックは残っている(self) -> None:
        from src.services.chihou_recommender import build_chihou_sweet_spot_recommendations

        assert callable(build_chihou_sweet_spot_recommendations)

    def test_変換集計ヘルパーは残っている(self) -> None:
        for name in (
            "_chihou_sweet_spot_to_out",
            "_summarize_by_category",
            "_build_chihou_sweet_spot_cached",
        ):
            assert hasattr(chihou_recommendations_router, name), f"{name} が消えています"

    def test_画面コードは残っている(self) -> None:
        for f in (PANEL, PANEL_WRAPPER):
            assert f.is_file(), f"{f.name} が消えています（参考として残す方針）"

    def test_停止の経緯と復帰手順が書いてある(self) -> None:
        """次に読む人が『使われていないから消そう』と判断しないための最低条件。"""
        assert "実稼働停止" in ROUTER_SRC
        assert "aggregate_chihou_recent.py" in ROUTER_SRC, "オフライン評価台への導線がない"

    def test_判定関数は現役なので消えていない(self) -> None:
        """個別馬バッジ（レース詳細）が使う。5カテゴリの停止と混同しないこと。"""
        from src.indices.buy_signal import (
            chihou_is_place_bet,
            chihou_is_sweet_spot,
            chihou_low_odds_trust_level,
        )

        assert callable(chihou_is_sweet_spot)
        assert callable(chihou_is_place_bet)
        assert callable(chihou_low_odds_trust_level)
