"""TrendForge V2 数据模型（SQLAlchemy 2.0）"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _now() -> datetime:
    return datetime.utcnow()


class Market(Base):
    """市场档案：AI 理解当地内容生态的知识载体"""
    __tablename__ = "markets"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)  # US/JP/KR/BR/CN
    name: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(String(32), default="UTC")
    media_landscape: Mapped[dict] = mapped_column(JSON, default=dict)   # 媒体/平台生态
    culture_notes: Mapped[list] = mapped_column(JSON, default=list)     # 文化语境与禁忌
    interests: Mapped[dict] = mapped_column(JSON, default=dict)         # 类目→权重
    platforms: Mapped[dict] = mapped_column(JSON, default=dict)         # 平台→形态/受众/时段
    tone: Mapped[str] = mapped_column(String(64), default="")
    default_style: Mapped[str] = mapped_column(String(32), default="deep_dive")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(128), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    category: Mapped[str] = mapped_column(String(32), default="general")
    country: Mapped[str] = mapped_column(String(8), default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    credibility: Mapped[int] = mapped_column(Integer, default=2)  # 1官方 2权威 3一般
    published_at: Mapped[str] = mapped_column(String(16), default="")
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    # 新鲜度治理（KBCurator 用）：最后核实日期 + 有效期(天) + 是否退役
    last_verified_at: Mapped[str] = mapped_column(String(16), default="")
    freshness_ttl: Mapped[int] = mapped_column(Integer, default=90)
    retired: Mapped[bool] = mapped_column(default=False)


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(128), default="")
    text: Mapped[str] = mapped_column(Text)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), default="pipeline")
    market: Mapped[str] = mapped_column(String(8), default="US")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/running/done/failed
    progress: Mapped[str] = mapped_column(String(64), default="")      # 当前 agent 名
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_log: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    total_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    review_rounds: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TaskSpan(Base):
    __tablename__ = "task_spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    agent: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok/degraded/failed
    model: Mapped[str] = mapped_column(String(64), default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(default=_now)


class Content(Base):
    __tablename__ = "contents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    market: Mapped[str] = mapped_column(String(8), default="US")
    language: Mapped[str] = mapped_column(String(8), default="en")
    status: Mapped[str] = mapped_column(String(16), default="published")  # published/retracted
    brief: Mapped[dict] = mapped_column(JSON, default=dict)        # AngleEditor 选题简报
    title: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[dict] = mapped_column(JSON, default=dict)         # {sections:[{heading,text}]}
    evidences: Mapped[list] = mapped_column(JSON, default=list)    # ev 证据集
    formats: Mapped[dict] = mapped_column(JSON, default=dict)      # 多形态派生
    distribution: Mapped[dict] = mapped_column(JSON, default=dict) # 分发计划
    quality: Mapped[dict] = mapped_column(JSON, default=dict)      # Rubric 评分 + fact_check
    decision_log: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    # 中文回译镜像：非中文市场的内容供中文运营审核用，按需生成后缓存
    # {lang, title, summary, brief:{...}, formats:{...}, model, generated_at}
    translation: Mapped[dict] = mapped_column(JSON, default=dict)
    # 驱动本内容的真实信号溯源（SignalScout 实时拉取，含来源/时间/互动/原文链接）
    signals: Mapped[list] = mapped_column(JSON, default=list)
    is_fallback: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ContentEvent(Base):
    __tablename__ = "content_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("contents.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(24))  # exposed/clicked/finished/liked/shared/negative/completed_video
    market: Mapped[str] = mapped_column(String(8), default="")
    platform: Mapped[str] = mapped_column(String(32), default="")
    format: Mapped[str] = mapped_column(String(24), default="article")
    ts: Mapped[datetime] = mapped_column(default=_now)


class EvalReport(Base):
    __tablename__ = "eval_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[str] = mapped_column(String(36), default="")  # 空=全局报告
    quality_avg: Mapped[float] = mapped_column(Float, default=0.0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    findings: Mapped[list] = mapped_column(JSON, default=list)
    suggestions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class BadCase(Base):
    __tablename__ = "bad_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[str] = mapped_column(String(36), default="")
    category: Mapped[str] = mapped_column(String(8), default="Q")  # F事实/H合规/C文化/Q质量
    title: Mapped[str] = mapped_column(String(256), default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    fix_action: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(default=_now)


class PipelineCache(Base):
    __tablename__ = "pipeline_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class KBPatch(Base):
    """知识库待审补丁：KBCurator 提议、人 approve 才入库（AI 提议 + 人审闸门）"""
    __tablename__ = "kb_patches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/approved/rejected
    market: Mapped[str] = mapped_column(String(8), default="")          # 空=全局
    rationale: Mapped[str] = mapped_column(Text, default="")            # 策展理由
    items: Mapped[list] = mapped_column(JSON, default=list)            # [{action,title,source,...}]
    created_at: Mapped[datetime] = mapped_column(default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)


async def migrate_db() -> None:
    """兼容旧快照：给已存在的表补加新增列（SQLite create_all 不会给旧表加列）"""
    async with engine.begin() as conn:
        res = await conn.execute(text("PRAGMA table_info(documents)"))
        cols = {row[1] for row in res}
        alters = [
            ("last_verified_at", "ALTER TABLE documents ADD COLUMN last_verified_at VARCHAR(16) DEFAULT ''"),
            ("freshness_ttl", "ALTER TABLE documents ADD COLUMN freshness_ttl INTEGER DEFAULT 90"),
            ("retired", "ALTER TABLE documents ADD COLUMN retired BOOLEAN DEFAULT 0"),
        ]
        for col, ddl in alters:
            if col not in cols:
                await conn.execute(text(ddl))
        # 回填：已存在但未核实的文档，用发布日作为首次核实日
        await conn.execute(text(
            "UPDATE documents SET last_verified_at = published_at "
            "WHERE last_verified_at IS NULL OR last_verified_at = ''"))

        # contents.translation：中文回译镜像
        res = await conn.execute(text("PRAGMA table_info(contents)"))
        ccols = {row[1] for row in res}
        if "translation" not in ccols:
            await conn.execute(text("ALTER TABLE contents ADD COLUMN translation JSON"))
        await conn.execute(text(
            "UPDATE contents SET translation = '{}' WHERE translation IS NULL"))
        # contents.signals：真实信号溯源
        if "signals" not in ccols:
            await conn.execute(text("ALTER TABLE contents ADD COLUMN signals JSON"))
        await conn.execute(text(
            "UPDATE contents SET signals = '[]' WHERE signals IS NULL"))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate_db()


class PromptRecord(Base):
    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(16), default="v1")
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="production")
    created_at: Mapped[datetime] = mapped_column(default=_now)
