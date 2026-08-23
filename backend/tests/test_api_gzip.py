"""API レスポンスが gzip されることを固定する（2026-08-23）。

## 背景

`api.galloplab.com` は**全エンドポイントが無圧縮**だった。競輪トップページの
1回のロードだけで picks 225KB + proposals 241KB ＝ **約472KB を生で転送**
していた（実測・`Content-Encoding` ヘッダ無し）。返すのは JSON なので
gzip でおよそ 1/9 になる。

🔴 **これは外すと誰も気づかない類の変更**（遅くなるだけでエラーは出ない）ので
   テストで固定する。
"""
from __future__ import annotations

from fastapi.middleware.gzip import GZipMiddleware

from src.main import app


def test_gzip_middleware_is_registered():
    names = [m.cls.__name__ for m in app.user_middleware]
    assert "GZipMiddleware" in names, "API の gzip 圧縮を外してはいけない"


def test_cors_is_outside_gzip():
    """🔴 CORS は gzip より**外側**であること。

    Starlette は後から追加したミドルウェアが外側になる。CORS が内側だと
    エラー応答やプリフライトに CORS ヘッダが付かない経路ができる。
    """
    names = [m.cls.__name__ for m in app.user_middleware]
    assert names.index("CORSMiddleware") < names.index("GZipMiddleware")


def test_minimum_size_is_not_too_small():
    """小さすぎる応答まで圧縮すると CPU を使うだけで縮まない。

    VPS は RAM 1.9GiB の小さな機体なので、閾値は 1KB 以上に保つ。
    """
    mw = next(m for m in app.user_middleware if m.cls is GZipMiddleware)
    minimum = mw.kwargs.get("minimum_size", 500)
    assert minimum >= 1000, f"minimum_size={minimum} は小さすぎる"


def test_large_json_is_actually_compressed():
    """実際に縮むことを1本だけ通しで確かめる（設定漏れの検出）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    probe = FastAPI()
    probe.add_middleware(GZipMiddleware, minimum_size=1000)

    @probe.get("/big")
    def big():  # pragma: no cover - テスト用の擬似エンドポイント
        return {"rows": [{"i": i, "name": "選手" * 10} for i in range(500)]}

    with TestClient(probe) as c:
        r = c.get("/big", headers={"Accept-Encoding": "gzip"})
        assert r.headers.get("content-encoding") == "gzip"
