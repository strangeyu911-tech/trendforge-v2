"""TrendForge V2 API 入口"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import analytics, contents, kb, misc, pipeline, prompts
from app.seed import seed_all

app = FastAPI(title="TrendForge V2 — AI Native 内容供给引擎", version="2.0.0")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    # 冷启动：工作 DB 不存在时先恢复演示快照（含预跑内容/消费事件/评估报告），
    # 保证 Render 每次部署后 Demo 开箱即有数据（容器文件系统是易失的）
    import shutil

    from app.config import DB_PATH
    snapshot = DB_PATH.parent / "demo_snapshot.db"
    if not DB_PATH.exists() and snapshot.exists():
        shutil.copy2(snapshot, DB_PATH)
    await seed_all()


app.include_router(misc.router, prefix="/api", tags=["misc"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(contents.router, prefix="/api", tags=["contents"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(kb.router, prefix="/api", tags=["kb"])
app.include_router(prompts.router, prefix="/api", tags=["prompts"])
