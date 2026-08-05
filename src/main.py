"""本地启动入口：python main.py [seed|serve|run <market>|simulate]"""
from __future__ import annotations

import asyncio
import sys


async def _seed():
    from app.seed import seed_all
    print(await seed_all())


async def _run(market: str):
    from app.seed import seed_all
    from app.workflow.orchestrator import run_pipeline
    await seed_all()
    result = await run_pipeline(market)
    print("done:", result)


async def _simulate():
    from app.seed import seed_all
    from app.simulator import simulate_events
    await seed_all()
    print(await simulate_events())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "seed":
        asyncio.run(_seed())
    elif cmd == "run":
        asyncio.run(_run(sys.argv[2] if len(sys.argv) > 2 else "US"))
    elif cmd == "simulate":
        asyncio.run(_simulate())
    else:
        import uvicorn
        uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000)
