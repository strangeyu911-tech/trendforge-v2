import asyncio, os, sys, json, datetime, traceback
from pathlib import Path

DB = r"D:/workbuddy/Data/2026-07-21-19-08-17/ai-news-system/v2_trendforge/src/app/data/trendforge_v2.db"
os.environ["TF_DB_PATH"] = DB
os.environ.setdefault("TF_LLM_MODEL", "deepseek-v4-flash")
os.environ.setdefault("TF_LLM_TIMEOUT", "240")
os.environ.setdefault("TF_LLM_BASE_URL", "https://api.deepseek.com")

LOG = open(r"D:/tmp/feedback_ab.log", "w", encoding="utf-8", buffering=1)
def log(*a):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = " ".join(str(x) for x in a)
    LOG.write(f"[{ts}] {line}\n"); LOG.flush()
    print(f"[{ts}] {line}", flush=True)

from sqlalchemy import select, func
from app.models import SessionLocal, Market, PromptRecord, Content, ContentEvent
from app.agents.base import RunContext
from app.agents.feedback_analyst import FeedbackAnalystAgent
from app.llm import get_llm
from app.seed import seed_all
from app.services import prompt_versions as pv
from app.workflow.ab import run_ab

SUGGESTABLE = ["writer", "editor", "distributor", "angle_editor"]


async def pick_market(session):
    ev = (await session.execute(
        select(ContentEvent.market, func.count())
        .where(ContentEvent.event_type == "exposed")
        .group_by(ContentEvent.market))).all()
    ct = (await session.execute(
        select(Content.market, func.count()).group_by(Content.market))).all()
    ev_map, ct_map = {m: n for m, n in ev}, {m: n for m, n in ct}
    log("events_by_market:", ev_map, "| contents_by_market:", ct_map)
    cands = [m for m in ev_map if ct_map.get(m, 0) > 0]
    cands.sort(key=lambda m: ev_map.get(m, 0), reverse=True)
    return (cands[0] if cands else "US"), ev_map, ct_map


def validate_new_prompt(np: str) -> bool:
    if not np or "---system---" not in np or "---user---" not in np:
        return False
    # 粗略完整性：user 段之后不应停在半句（这里只检查有实质长度）
    return len(np) > 200


async def run_feedback(session, market, m, llm):
    for attempt in range(3):
        task = type("T", (), {"id": f"feedback-run-{attempt}"})()
        ctx = RunContext(task_id=task.id, session=session, llm=llm, task=task, market=m)
        try:
            result = await FeedbackAnalystAgent()._exec(ctx, {})
        except Exception as e:
            log(f"FeedbackAnalyst attempt {attempt} EXC:", repr(e))
            traceback.print_exc()
            continue
        report = result.get("eval_report", {})
        structured = report.get("structured_suggestions", [])
        if structured:
            log(f"FeedbackAnalyst OK (attempt {attempt}): structured={len(structured)}")
            return report, structured
        else:
            log(f"FeedbackAnalyst attempt {attempt}: 0 structured (fell back). readable={report.get('suggestions')}")
    return None, []


async def main():
    try:
        log("STEP 0 seed_all (migrate + v1 prompts + markets)")
        await seed_all()
        async with SessionLocal() as session:
            market, _, _ = await pick_market(session)
            m = await session.get(Market, market)
            llm = get_llm()

            log("STEP 1 FeedbackAnalyst (live LLM) ...")
            report, structured = await run_feedback(session, market, m, llm)
            if not structured:
                log("FATAL: no structured suggestion after retries; abort.")
                return
            log("metrics:", json.dumps(report.get("metrics", {}), ensure_ascii=False))
            log("findings:", report.get("findings"))
            for i, s in enumerate(structured):
                log(f"  [{i}] {s['target_template']}/{s['section']} :: {s['proposed_change'][:90]}")

            ids = await pv.persist_structured_suggestions(session, structured, market=market)
            await session.commit()
            log("persisted suggestion_ids:", ids)

            chosen = next((s for s in structured if s["target_template"] in SUGGESTABLE
                          and validate_new_prompt(s["new_prompt"])), None)
            if not chosen:
                log("FATAL: no valid new_prompt among suggestions; abort.")
                return
            tpl = chosen["target_template"]
            log(f"CHOSEN for A/B: template={tpl} section={chosen['section']}")
            log("proposed_change:", chosen["proposed_change"])
            log("rationale:", chosen["rationale"])
            log("expected_metric:", chosen["expected_metric"])

            rec = await pv.create_version(session, tpl, chosen["new_prompt"],
                                          source="ai_suggested", parent_version="v1")
            await session.commit()
            v2_id, v2_ver = rec.id, rec.version
            v1 = (await session.execute(
                select(PromptRecord).where(PromptRecord.name == tpl,
                                          PromptRecord.version == "v1"))).scalars().first()
            v1_id = v1.id
            log(f"v1_id={v1_id}(v1)  v2_id={v2_id}({v2_ver})")

            log("STEP 2 run_ab (live LLM, two produce runs + sim) ...")
            angle = "How AI coding assistants are reshaping the career path of junior developers"
            topic = "AI coding tools"
            if market != "US":
                angle = "AI 编程助手如何改变初级开发者成长路径"; topic = "AI 编程工具"
            ab = await run_ab(market, tpl, v1_id, v2_id, angle=angle, topic=topic, per_content=300)
            log("A/B delta:", json.dumps(ab.get("delta", {}), ensure_ascii=False))

            out = {"market": market, "chosen_template": tpl,
                   "chosen_section": chosen["section"],
                   "proposed_change": chosen["proposed_change"],
                   "rationale": chosen["rationale"], "expected_metric": chosen["expected_metric"],
                   "report": report, "suggestion_ids": ids, "ab": ab}
            Path(r"D:/tmp/feedback_ab_result.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            log("WROTE D:/tmp/feedback_ab_result.json")
            print("\n===== SUMMARY =====")
            print("market:", market, "| template:", tpl, "| section:", chosen["section"])
            print("suggestions:", len(structured), "| persisted:", len(ids))
            print("v1:", v1_id, "v2:", v2_id, v2_ver)
            print("A/B delta (v2 - v1):", json.dumps(ab.get("delta", {}), ensure_ascii=False))
            print("v1 quality_avg:", ab["v1"].get("quality_avg"), "ctr:", ab["v1"].get("ctr"),
                  "cost:", ab["v1"].get("cost_cny"), "verdict:", ab["v1"].get("verdict"))
            print("v2 quality_avg:", ab["v2"].get("quality_avg"), "ctr:", ab["v2"].get("ctr"),
                  "cost:", ab["v2"].get("cost_cny"), "verdict:", ab["v2"].get("verdict"))
    except Exception as e:
        log("FATAL EXCEPTION in main:", repr(e))
        traceback.print_exc(file=LOG); traceback.print_exc()


asyncio.run(main())
