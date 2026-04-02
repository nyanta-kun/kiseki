"""GallopLab - FastAPI エントリポイント"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.access import access_admin_router, access_router
from .api.agent_router import router as agent_router
from .api.horses import router as horses_router
from .api.import_router import changes_router
from .api.import_router import router as import_router
from .api.races import router as races_router
from .api.users import admin_router as users_admin_router
from .api.users import router as users_router
from .config import settings

app = FastAPI(
    title="GallopLab API",
    description="競馬AI指数・期待値分析 API",
    version="0.1.0",
    docs_url=None if settings.api_env == "production" else "/docs",
    redoc_url=None if settings.api_env == "production" else "/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://frontend:3000",
        "https://galloplab.com",
        "https://www.galloplab.com",
        "https://sekito-stable.com",
        "https://www.sekito-stable.com",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@app.get("/health")
async def health_check():
    """ヘルスチェックエンドポイント"""
    return {"status": "ok"}


# --- API Routers ---
app.include_router(import_router)       # POST /api/import/*
app.include_router(changes_router)      # POST /api/changes/notify
app.include_router(races_router)        # GET  /api/races/*
app.include_router(horses_router)       # GET  /api/horses/*
app.include_router(agent_router)        # GET/POST /api/agent/*
app.include_router(users_router)        # POST /api/users/upsert
app.include_router(users_admin_router)  # GET/PATCH /api/admin/users
app.include_router(access_router)       # POST/GET /api/users/{id}/redeem-code, /access
app.include_router(access_admin_router) # GET/POST/PATCH /api/admin/invitation-codes

# MS2以降で順次有効化:
# from .api import indices, newspaper
# app.include_router(indices.router)
# app.include_router(newspaper.router)
