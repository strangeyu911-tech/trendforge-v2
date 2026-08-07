"""清掉旧 content_events 并用 M2 新版仿真器（事件时间戳按 72h 衰减分布）重跑。

用法：
  python tools/regen_events.py <db_path>
依赖：在虚拟环境中运行（pip install -r src/requirements.txt）。
必须在导入 app 之前设置 TF_DB_PATH，让 engine 指向目标库。
"""
from __future__ import annotations
import os
import sys
import asyncio

if len(sys.argv) < 2:
    print("用法: python tools/regen_events.py <db_path>")
    sys.exit(1)

os.environ["TF_DB_PATH"] = sys.argv[1]
os.environ.setdefault("TF_LLM_MODEL", "deepseek-v4-flash")

from sqlalchemy import text  # noqa: E402
from app.models import SessionLocal, init_db  # noqa: E402
from app.simulator import simulate_events  # noqa: E402


async def main():
    await init_db()  # 确保 schema 与最新代码一致（补 signals/translation 等列）
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM content_events"))
        await s.commit()
        n0 = (await s.execute(text("SELECT COUNT(*) FROM content_events"))).scalar()
        print(f"已清空 content_events（剩 {n0} 条）")
    res = await simulate_events(per_content=300)
    print("仿真结果:", res)


if __name__ == "__main__":
    asyncio.run(main())
