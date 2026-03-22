"""kiseki - FastAPI エントリポイント"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.import_router import changes_router, router as import_router
from .api.races import router as races_router
from .config import settings

app = FastAPI(
    title="kiseki API",
    description="競馬予測指数システム API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """ヘルスチェックエンドポイント"""
    return {"status": "ok", "env": settings.api_env}


# --- API Routers ---
app.include_router(import_router)   # POST /api/import/*
app.include_router(changes_router)  # POST /api/changes/notify
app.include_router(races_router)    # GET  /api/races/*

# MS2以降で順次有効化:
# from .api import indices, newspaper
# app.include_router(indices.router)
# app.include_router(newspaper.router)
