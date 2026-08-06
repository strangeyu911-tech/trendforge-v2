"""TrendForge V2 全局配置"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent          # src/app
DATA_DIR = BASE_DIR / "data"
KB_DIR = DATA_DIR / "kb"
DB_PATH = DATA_DIR / "trendforge_v2.db"


class Settings:
    # LLM（默认 DeepSeek，OpenAI 兼容协议）
    llm_base_url: str = os.getenv("TF_LLM_BASE_URL", "https://api.deepseek.com")
    llm_api_key: str = os.getenv("DEEPSEEK_API_KEY") or os.getenv("TF_LLM_API_KEY", "")
    llm_model: str = os.getenv("TF_LLM_MODEL", "deepseek-v4-flash")
    llm_timeout: float = float(os.getenv("TF_LLM_TIMEOUT", "90"))
    llm_max_retries: int = int(os.getenv("TF_LLM_MAX_RETRIES", "3"))
    # 定价（元/1M tokens, in/out），仅用于成本展示
    llm_price_in: float = float(os.getenv("TF_LLM_PRICE_IN", "2"))
    llm_price_out: float = float(os.getenv("TF_LLM_PRICE_OUT", "8"))

    # 流水线
    max_review_rounds: int = int(os.getenv("TF_MAX_REVIEW_ROUNDS", "2"))
    top_signals: int = int(os.getenv("TF_TOP_SIGNALS", "12"))
    top_trends: int = int(os.getenv("TF_TOP_TRENDS", "4"))
    # 证据数 10→5（L0 反漂移）：证据集是 Writer 的唯一素材来源，
    # 多喂一条无关证据就多一个跑题入口。少而准 > 多而杂。
    top_evidences: int = int(os.getenv("TF_TOP_EVIDENCES", "5"))
    cache_ttl_hours: int = int(os.getenv("TF_CACHE_TTL_HOURS", "72"))

    # 反主题漂移闸门
    # BM25 绝对分下限：低于此分视为与选题无实质词面关联（相对阈值挡不住"全员不相关"）
    topic_min_score: float = float(os.getenv("TF_TOPIC_MIN_SCORE", "1.0"))
    # TCS 硬闸：主干文档引用占比下限 / 允许引用的背景文档篇数上限
    tcs_main_ratio_min: float = float(os.getenv("TF_TCS_MAIN_RATIO", "0.6"))
    tcs_cross_doc_max: int = int(os.getenv("TF_TCS_CROSS_DOC_MAX", "2"))

    # 形态派生清单（FormatAdapter 默认产出）
    formats: tuple = ("video_script", "card", "brief_news", "comment")

    database_url: str = f"sqlite+aiosqlite:///{DB_PATH.as_posix()}"


settings = Settings()

# Editor 合规扫描的通用敏感词（演示级，市场特有禁忌在 MarketProfile.culture_notes）
COMPLIANCE_BLOCKLIST = [
    "赌博", "博彩", "洗钱", "恐怖主义",
    "gambling", "terrorism", "money laundering",
]

# 内容风格（Writer 使用）
CONTENT_STYLES = {
    "deep_dive": {"label": "深度解读", "length": "800-1200字"},
    "explainer": {"label": "科普解释", "length": "600-900字"},
    "news_roundup": {"label": "资讯聚合", "length": "500-800字"},
    "opinion": {"label": "观点评论", "length": "600-1000字"},
}
