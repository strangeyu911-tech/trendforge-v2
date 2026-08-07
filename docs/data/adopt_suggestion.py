"""M3 闭环：把 FeedbackAnalyst 产出的建议「人审采纳」→ 写入覆盖层，下次运行直接生效。

关键点（符合 M3 设计）：
- adopt_version 置 adopted=True 并调用 refresh_overrides，PromptManager 内存覆盖层立即更新；
- 同一模板其它版本自动取消采纳；
- 应用启动（seed_all）也会调 refresh_overrides，故部署后无需重启即与 DB 一致。
"""
import asyncio
from sqlalchemy import select
from app.models import SessionLocal, PromptSuggestion, PromptRecord
from app.services.prompt_versions import adopt_version
from app.prompts.manager import get_pm

SUG_ID = "a4cf60d2-4637-4bf3-9517-af98fee5186f"
VER_ID = 16  # writer@v3，本次实跑创建的新版


async def main():
    async with SessionLocal() as s:
        # 1) 校验建议存在且为 pending
        sug = (await s.execute(
            select(PromptSuggestion).where(PromptSuggestion.id == SUG_ID))).scalar_one_or_none()
        assert sug, "建议不存在"
        print(f"[check] suggestion {SUG_ID[:8]} status={sug.status} target={sug.target_template}/{sug.section[:16]}")

        # 2) 采纳版本（写入覆盖层）
        res = await adopt_version(s, VER_ID)
        print(f"[adopt] version id={res['id']} name={res['name']} version={res['version']} "
              f"adopted={res['adopted']} overrides_active={res['overrides_active']}")

        # 3) 建议状态置 adopted（该建议已被执行）
        sug.status = "adopted"
        await s.commit()

        # 4) 校验覆盖层确实生效（无需重启）
        pm = get_pm()
        av = pm.active_version("writer")
        print(f"[verify] active_version('writer') = {av}")
        ov = pm._overrides.get("writer")
        snippet = ""
        if ov:
            sys_text = ov[0] or ""
            hit = "每一小节" in sys_text
            print(f"[verify] override present={bool(ov)} contains_new_phrase={'每一小节' in sys_text}")
            # 打印命中的那一行上下文
            for line in sys_text.splitlines():
                if "每一小节" in line or "首段必须兑现钩子" in line:
                    snippet = line.strip()
                    break
        print(f"[verify] matched_line: {snippet!r}")

        # 5) 最终 DB 状态
        rows = (await s.execute(
            select(PromptRecord).where(PromptRecord.name == "writer").order_by(PromptRecord.id))).scalars().all()
        print("[db] writer versions:", [(r.id, r.version, r.source, r.adopted) for r in rows])
        sug2 = (await s.execute(
            select(PromptSuggestion).where(PromptSuggestion.id == SUG_ID))).scalar_one_or_none()
        print(f"[db] suggestion {SUG_ID[:8]} status={sug2.status}")


asyncio.run(main())
