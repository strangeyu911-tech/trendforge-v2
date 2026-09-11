# 兜底推送日志（Git Data API）

认证为 strangeyu911-tech
远端 main 当前： fed7caa8
待重放提交： ['d257b05', '6163092', 'a8a05fc', '6478514']

--- 提交 d257b05 · 12 个文件 · chore: 补提交前序会话已完成但未入库的改动
    blob render.yaml (694B, mode 100644)
    blob src/app/api/main.py (3566B, mode 100644)
    blob src/app/api/routers/calibration.py (9176B, mode 100644)
    blob src/app/api/routers/kb.py (4516B, mode 100644)
    blob src/app/api/routers/pipeline.py (8593B, mode 100644)
    blob src/app/config.py (3121B, mode 100644)
    blob src/app/rag/store.py (12183B, mode 100644)
    blob src/app/services/alignment.py (12129B, mode 100644)
    blob src/app/services/zh_mirror.py (10432B, mode 100644)
    blob tools/calibration/calibration_chart.svg (3613B, mode 100644)
    blob tools/calibration/calibration_report.md (22870B, mode 100644)
    blob tools/calibration/compute_alignment.py (3277B, mode 100644)
    → commit 3907b84a

--- 提交 6163092 · 3 个文件 · data: 重建 demo 快照——补齐市场档案与真人校准分
    blob docs/data/SNAPSHOT_REBUILD_LOG_v1.1.md (548B, mode 100644)
    blob src/app/data/demo_snapshot.db (1064960B, mode 100644)
    blob tools/rebuild_snapshot.py (7285B, mode 100644)
    → commit 24486014

--- 提交 a8a05fc · 9 个文件 · feat: 按全站审计整改——标签层、系统进化、诚实标注与 Demo 叙事
    blob src/app/agents/feedback_analyst.py (7899B, mode 100644)
    blob src/app/analytics/queries.py (19795B, mode 100644)
    blob src/app/api/routers/prompts.py (10073B, mode 100644)
    blob src/app/workflow/ab.py (7536B, mode 100644)
    blob ui/console/assets/api.js (4599B, mode 100644)
    blob ui/console/assets/app.js (122830B, mode 100644)
    blob ui/console/assets/styles.css (20320B, mode 100644)
    blob ui/console/index.html (1145B, mode 100644)
    blob ui/index.html (9443B, mode 100644)
    → commit e8ae2688

--- 提交 6478514 · 4 个文件 · docs: 全站审计报告 v1.0 + 整改记录 v1.1；新增渲染冒烟脚本
    blob docs/PRODUCT_AUDIT_FIX_v1.1.md (9268B, mode 100644)
    blob docs/PRODUCT_AUDIT_v1.0.md (20073B, mode 100644)
    blob tools/fetch_fixtures.sh (1097B, mode 100644)
    blob tools/smoke_render.js (9800B, mode 100644)
    → commit 7da63c19

✅ main 已更新到 7da63c19（共 4 个提交）
    旧 tag 不存在或删除失败（忽略）：DELETE /repos/strangeyu911-tech/trendforge-v2/git/refs/tags/v2.19-audit-fix → HT
✅ 已打 tag v2.19-audit-fix → 686d8b53
