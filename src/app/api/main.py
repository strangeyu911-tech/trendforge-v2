"""TrendForge V2 API 入口"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import analytics, contents, misc, pipeline
from app.seed import seed_all

app = FastAPI(title="TrendForge V2 — AI Native 内容供给引擎", version="2.0.0")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    await seed_all()


app.include_router(misc.router, prefix="/api", tags=["misc"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(contents.router, prefix="/api", tags=["contents"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
