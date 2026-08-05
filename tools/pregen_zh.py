"""预生成中文对照，写入演示快照 demo_snapshot.db。

为什么需要：Render 免费层磁盘是临时的，每次冷启动都会用仓库里的
demo_snapshot.db 重建运行库。若中文对照只在运行时按需生成，冷启动后
第一位访客又要等 20–40 秒。把演示内容的对照预生成并提交进快照，
线上就永远是秒开、零额度消耗。

两种用法：

1）本地有 LLM key —— 直接调模型生成
    DEEPSEEK_API_KEY=sk-xxx python tools/pregen_zh.py
    DEEPSEEK_API_KEY=sk-xxx python tools/pregen_zh.py --refresh   # 覆盖已有对照

2）本地没有 key，但线上服务有 —— 从线上把已生成的对照拉回来回灌
    python tools/pregen_zh.py --from-api https://trendforge-v2-api.onrender.com
    （脚本会先 POST /contents/{id}/zh 让线上生成，再把结果写回本地快照）

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


async def fetch_from_api(base: str, cid: str, refresh: bool):
    """向线上服务要中文对照（线上配了 key，本地不用）"""
    import httpx

    url = f"{base.rstrip('/')}/api/contents/{cid}/zh" + ("?refresh=true" if refresh else "")
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(url)
        r.raise_for_status()
        data = r.json()
    if not data.get("available"):
        raise RuntimeError(data.get("reason") or "线上未返回可用对照")
    return data["translation"]


def ensure_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(contents)")}
    if "translation" not in cols:
        conn.execute("ALTER TABLE contents ADD COLUMN translation JSON")
        conn.execute("UPDATE contents SET translation = '{}' WHERE translation IS NULL")
        conn.commit()


async def main(refresh: bool = False, api_base: str | None = None) -> int:
    if not api_base and not settings.llm_api_key:
        print("✗ 未配置 DEEPSEEK_API_KEY / TF_LLM_API_KEY")
        print("  本地没有 key 时，可改用线上服务生成：")
        print("  python tools/pregen_zh.py --from-api https://trendforge-v2-api.onrender.com")
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
        via = f"线上 {api_base}" if api_base else "本地 LLM"
        print(f"→ {cid[:8]} {market}/{lang} 回译中（{via}）…", flush=True)
        try:
            if api_base:
                mirror = await fetch_from_api(api_base, cid, refresh)
                cost = float(mirror.get("cost_cny") or 0.0)
            else:
                mirror, resps = await translate_source(src, market, lang)
                cost = sum(getattr(r, "cost_cny", 0.0) for r in resps)
                mirror.pop("_partial", None)
                mirror.update({"lang": "zh", "model": getattr(resps[0], "model", "") if resps else "",
                               "cost_cny": round(cost, 6)})
        except Exception as e:
            print(f"  ✗ 失败：{str(e)[:200]}")
            failed += 1
            continue
        mirror["generated_at"] = "pregen"
        conn.execute("UPDATE contents SET translation = ? WHERE id = ?",
                     (json.dumps(mirror, ensure_ascii=False), cid))
        conn.commit()
        zt = (mirror.get("brief") or {}).get("topic", "")
        print(f"  ✓ 完成 · ¥{cost:.4f} · 选题中文：{zt[:40]}")
        done += 1

    conn.close()
    print(f"\n完成 {done} 条，跳过 {skipped} 条，失败 {failed} 条 → {SNAPSHOT}")
    print("记得提交 demo_snapshot.db，线上冷启动即可直接展示中文对照。")
    return 0 if not failed else 2


if __name__ == "__main__":
    argv = sys.argv[1:]
    base = None
    if "--from-api" in argv:
        i = argv.index("--from-api")
        base = argv[i + 1] if i + 1 < len(argv) else "https://trendforge-v2-api.onrender.com"
    sys.exit(asyncio.run(main("--refresh" in argv, base)))
