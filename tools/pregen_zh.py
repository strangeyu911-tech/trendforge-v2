"""预生成中文对照，写入演示快照 demo_snapshot.db。

为什么需要：Render 免费层磁盘是临时的，每次冷启动都会用仓库里的
demo_snapshot.db 重建运行库。若中文对照只在运行时按需生成，冷启动后
第一位访客又要等 20–40 秒。把演示内容的对照预生成并提交进快照，
线上就永远是秒开、零额度消耗。

用法（需要 LLM key）：
    DEEPSEEK_API_KEY=sk-xxx python tools/pregen_zh.py
    DEEPSEEK_API_KEY=sk-xxx python tools/pregen_zh.py --refresh   # 覆盖已有对照

之后把 src/app/data/demo_snapshot.db 一起提交即可。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.config import settings  # noqa: E402
from app.services.zh_mirror import BRIEF_FIELDS, translate_source  # noqa: E402

SNAPSHOT = ROOT / "src" / "app" / "data" / "demo_snapshot.db"


def ensure_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(contents)")}
    if "translation" not in cols:
        conn.execute("ALTER TABLE contents ADD COLUMN translation JSON")
        conn.execute("UPDATE contents SET translation = '{}' WHERE translation IS NULL")
        conn.commit()


async def main(refresh: bool = False) -> int:
    if not settings.llm_api_key:
        print("✗ 未配置 DEEPSEEK_API_KEY / TF_LLM_API_KEY，无法预生成")
        return 1
    if not SNAPSHOT.exists():
        print(f"✗ 找不到快照：{SNAPSHOT}")
        return 1

    conn = sqlite3.connect(SNAPSHOT)
    ensure_column(conn)
    rows = conn.execute(
        "SELECT id, market, language, title, summary, brief, formats, translation FROM contents"
    ).fetchall()

    done = skipped = failed = 0
    for cid, market, lang, title, summary, brief, formats, translation in rows:
        if (lang or "").lower().startswith("zh"):
            print(f"– {cid[:8]} {market}/{lang} 已是中文，跳过")
            skipped += 1
            continue
        existing = json.loads(translation or "{}")
        if existing.get("brief") and not refresh:
            print(f"– {cid[:8]} {market}/{lang} 已有对照，跳过（--refresh 可覆盖）")
            skipped += 1
            continue

        b = json.loads(brief or "{}")
        src = {
            "title": title or "",
            "summary": summary or "",
            "brief": {k: b[k] for k in BRIEF_FIELDS if b.get(k)},
            "formats": json.loads(formats or "{}"),
        }
        print(f"→ {cid[:8]} {market}/{lang} 回译中…", flush=True)
        try:
            mirror, resp = await translate_source(src, market, lang)
        except Exception as e:
            print(f"  ✗ 失败：{str(e)[:200]}")
            failed += 1
            continue
        mirror.update({"lang": "zh", "model": resp.model,
                       "cost_cny": round(resp.cost_cny, 6), "generated_at": "pregen"})
        conn.execute("UPDATE contents SET translation = ? WHERE id = ?",
                     (json.dumps(mirror, ensure_ascii=False), cid))
        conn.commit()
        zt = (mirror.get("brief") or {}).get("topic", "")
        print(f"  ✓ 完成 · ¥{resp.cost_cny:.4f} · 选题中文：{zt[:40]}")
        done += 1

    conn.close()
    print(f"\n完成 {done} 条，跳过 {skipped} 条，失败 {failed} 条 → {SNAPSHOT}")
    print("记得提交 demo_snapshot.db，线上冷启动即可直接展示中文对照。")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--refresh" in sys.argv)))
