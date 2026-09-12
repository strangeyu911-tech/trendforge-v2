# -*- coding: utf-8 -*-
"""重建 demo_snapshot.db（诚实版）

原则：只搬运真实数据，不虚构分数/报告。
1. 补齐 human_calibrations 表 + 真实人工打分（仅保留快照内存在的内容，剔除退化测试行）
2. 回填 contents.human_score_avg（由上述真人打分聚合，真实派生）
3. 从本地库同步 GB / IN 两个市场档案（真实调研配置）
4. 不同步 local 里 GB/IN 的 3 条垃圾兜底内容（标题是中文模板串给英文市场，放上去更丢分）

用法：python tools/rebuild_snapshot.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "src", "app", "data")
SNAP = os.path.join(DATA, "demo_snapshot.db")
LOCAL = os.path.join(DATA, "trendforge_v2.db")

CAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS human_calibrations (
  id INTEGER NOT NULL,
  content_id VARCHAR(36) NOT NULL,
  rater VARCHAR(64) NOT NULL,
  scores JSON NOT NULL,
  reasons JSON NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  FOREIGN KEY(content_id) REFERENCES contents (id)
)
"""

log: list[str] = []


def w(*a):
    line = " ".join(str(x) for x in a)
    log.append(line)
    print(line)


def main() -> None:
    bak = os.path.join(DATA, "demo_snapshot.db.bak")
    shutil.copy2(SNAP, bak)
    w("backup ->", bak)

    snap = sqlite3.connect(SNAP)
    snap.row_factory = sqlite3.Row
    cur = snap.cursor()

    local = sqlite3.connect(LOCAL)
    local.row_factory = sqlite3.Row
    lcur = local.cursor()

    snap_ids = {r[0] for r in cur.execute("select id from contents").fetchall()}
    w("snapshot contents:", len(snap_ids))

    # ---------- 1. human_calibrations ----------
    cur.execute(CAL_SCHEMA)
    cur.execute("delete from human_calibrations")
    lcur.execute("select * from human_calibrations order by id")
    cals = lcur.fetchall()
    kept, skipped_missing, skipped_degenerate = [], 0, 0
    # 同一 content+rater 只保留最新一条，避免重复提交造成样本膨胀
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for r in cals:
        cid, rater = r["content_id"], r["rater"]
        try:
            scores = json.loads(r["scores"])
            reasons = json.loads(r["reasons"])
        except Exception:
            skipped_degenerate += 1
            continue
        # 退化测试行：全 1 分且无理由
        if all(float(v) <= 1.0 for v in scores.values()) and not any(
                str(v or "").strip() for v in reasons.values()):
            skipped_degenerate += 1
            continue
        if cid not in snap_ids:
            skipped_missing += 1
            continue
        key = (cid, rater)
        prev = latest.get(key)
        if prev is None or str(r["created_at"]) > str(prev["created_at"]):
            latest[key] = r
    kept = sorted(latest.values(), key=lambda x: x["id"])
    for r in kept:
        cur.execute(
            "insert into human_calibrations (id, content_id, rater, scores, reasons, created_at)"
            " values (?,?,?,?,?,?)",
            (r["id"], r["content_id"], r["rater"], r["scores"], r["reasons"], r["created_at"]),
        )
    w(f"calibrations: local={len(cals)} kept={len(kept)} "
      f"skip_missing={skipped_missing} skip_degenerate={skipped_degenerate}")

    # ---------- 2. 回填 contents.human_score_avg ----------
    cols = [r[1] for r in cur.execute("PRAGMA table_info(contents)").fetchall()]
    if "human_score_avg" not in cols:
        cur.execute("ALTER TABLE contents ADD COLUMN human_score_avg JSON")
        w("added column contents.human_score_avg")
    if "signals" not in cols:
        cur.execute("ALTER TABLE contents ADD COLUMN signals JSON")
        w("added column contents.signals")
    touched = 0
    cur.execute("select content_id from human_calibrations group by content_id")
    for (cid,) in cur.fetchall():
        rows = cur.execute(
            "select scores from human_calibrations where content_id=?", (cid,)).fetchall()
        per_dim: dict[str, list[float]] = {}
        for (raw,) in rows:
            try:
                s = json.loads(raw)
            except Exception:
                continue
            for k, v in s.items():
                per_dim.setdefault(k, []).append(float(v))
        if not per_dim:
            continue
        dim_avg = {k: round(sum(v) / len(v), 2) for k, v in per_dim.items()}
        # 与 calibration._aggregate_content 的输出格式保持一致：各维均值 + n_raters
        payload = dict(dim_avg)
        payload["n_raters"] = len(rows)
        cur.execute("update contents set human_score_avg=? where id=?",
                    (json.dumps(payload, ensure_ascii=False), cid))
        touched += 1
    w("contents with human_score_avg:", touched)

    # ---------- 2.5 失败案例库同步 ----------
    # 之前漏了这张表：案例库改了状态机列、回填了文案，快照却还是旧值，部署后页面回退。
    # 案例是真实运行产出的治理资产，直接搬运（不做任何加工）。
    b_cols = [r[1] for r in cur.execute("PRAGMA table_info(bad_cases)").fetchall()]
    for col, ddl in [
        ("failure_kind", "ALTER TABLE bad_cases ADD COLUMN failure_kind VARCHAR(24) DEFAULT ''"),
        ("market", "ALTER TABLE bad_cases ADD COLUMN market VARCHAR(8) DEFAULT ''"),
        ("resolved_at", "ALTER TABLE bad_cases ADD COLUMN resolved_at TIMESTAMP"),
    ]:
        if col not in b_cols:
            cur.execute(ddl)
            w("added column bad_cases." + col)
    cur.execute("delete from bad_cases")
    lcur.execute("select * from bad_cases order by id")
    n_bc = 0
    for r in lcur.fetchall():
        cur.execute(
            "insert into bad_cases (id,content_id,category,title,root_cause,fix_action,status,"
            "failure_kind,market,resolved_at,created_at) values (?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["content_id"], r["category"], r["title"], r["root_cause"],
             r["fix_action"], r["status"], r["failure_kind"], r["market"],
             r["resolved_at"], r["created_at"]),
        )
        n_bc += 1
    w("bad_cases synced:", n_bc)

    # ---------- 3. 同步缺失市场档案 ----------
    m_cols = [r[1] for r in cur.execute("PRAGMA table_info(markets)").fetchall()]
    if "insight_sources" not in m_cols:
        cur.execute("ALTER TABLE markets ADD COLUMN insight_sources JSON")
        w("added column markets.insight_sources")
    cur.execute("select code from markets")
    have = {r[0] for r in cur.fetchall()}
    lcur.execute("select * from markets")
    added = []
    for r in lcur.fetchall():
        if r["code"] in have:
            continue
        cur.execute(
            "insert into markets (code,name,language,timezone,media_landscape,culture_notes,"
            "interests,platforms,tone,default_style,insight_sources)"
            " values (?,?,?,?,?,?,?,?,?,?,?)",
            (r["code"], r["name"], r["language"], r["timezone"], r["media_landscape"],
             r["culture_notes"], r["interests"], r["platforms"], r["tone"],
             r["default_style"], r["insight_sources"]),
        )
        added.append(r["code"])
    w("markets added:", added or "none")

    snap.commit()
    cur.execute("VACUUM")
    snap.commit()
    snap.close()
    local.close()

    # ---------- 4. 自检 ----------
    v = sqlite3.connect(SNAP)
    v.row_factory = sqlite3.Row
    c = v.cursor()
    c.execute("select count(*) from contents")
    nc = c.fetchone()[0]
    c.execute("select count(*) from markets")
    nm = c.fetchone()[0]
    c.execute("select count(*) from human_calibrations")
    nh = c.fetchone()[0]
    c.execute("select count(*) from contents where human_score_avg is not null")
    nhs = c.fetchone()[0]
    c.execute("select count(*) from bad_cases")
    nbc = c.fetchone()[0]
    c.execute("select count(*) from bad_cases where status in ('open','retrying')")
    nbopen = c.fetchone()[0]
    w(f"VERIFY contents={nc} markets={nm} calibrations={nh} contents_with_human_avg={nhs} "
      f"bad_cases={nbc} bad_cases_pending={nbopen}")
    c.execute("select code from markets order by code")
    w("markets:", [r[0] for r in c.fetchall()])
    v.close()

    report = os.path.join(BASE, "docs", "data", "SNAPSHOT_REBUILD_LOG_v1.1.md")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as f:
        f.write("# demo_snapshot.db 重建日志（v1.1 · 诚实版）\n\n")
        f.write(f"- 时间：{datetime.now().isoformat(timespec='seconds')}\n")
        f.write("- 原则：只搬运真实数据；不同步虚构/退化内容\n\n## 操作\n\n")
        f.write("\n".join("- " + x for x in log))
        f.write("\n")
    w("report ->", report)


if __name__ == "__main__":
    main()
