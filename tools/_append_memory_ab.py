# -*- coding: utf-8 -*-
"""记录 v2.20 A/B 修复到今日日志与项目长期记忆（用脚本写，避免 shell 反引号污染）。"""
import io

day = '.workbuddy/memory/2026-09-12.md'
mem = '.workbuddy/memory/MEMORY.md'

day_add = '''

---

## A/B 对比运行不了（v2.20 修复）

用户反馈「系统进化 → ④ A/B 对比运行不了」。排查结论是**三个问题叠在一起**：

1. **前端初始不填版本下拉**（最直观）：`closedLoopView()` 只调了 `loadVersions()`，没调 `loadAbVersions()`，
   两个下拉只有「旧版 / 新版」占位 option；不手动切一次模板就永远选不出两版 → 点运行只会弹 toast。
2. **后端是同步长请求**（根因）：`POST /prompts/ab/run` 在请求里同步跑两次完整 Produce 链路。
   线上单条 supply 实测 ~688s，A/B = 两次 ≈ 20 分钟，必然被网关/浏览器判超时 → 点了半天没结果。
3. 附带：结果卡的「选用这版」按钮用的是下拉 value，刷新/异步后取不到；结果也没带裁决。

修复：
- 后端：ab/run 改异步（跟 revise 同构）—— 校验后建内存 job、`asyncio.create_task`、
  立即返回 `{job_id}`（实测 15ms）；新增 `GET /prompts/ab/jobs/{job_id}`；
  入参校验 404（版本不存在）/ 400（同版本、模板不匹配）；job 表留最近 50 条。
- `run_ab` 增 `on_progress` 回调回传阶段进度（跑第 1 版 / 跑第 2 版 / 回收仿真 / 汇总）；
  返回体补 `v1.id` / `v2.id`（供「选用这版」脱离下拉取值）。
- 前端：进页面即 `loadAbVersions()` + 预选（旧=最旧一版、新=最新一版）；
  只有 1 个版本时禁用按钮并说明原因；新增 `ABState`（模块级 + localStorage，5s 轮询，
  切页/刷新可续）；面板文案改为「任务在后台运行，提交后即可切走」。
- 工具：`tools/fetch_fixtures.py` 取代 sh 版（sh 版在本机 shell 环境不稳）；
  冒烟测试加 A/B 6 项检查 → 54 项全绿。静态资源 v2.2 → v2.3。

线上核验：ab/jobs 返回新 404 文案说明已部署；真跑一次 A/B（writer v1 vs v3，US）
成功拿到 job_id 且进度推进（跑第 1 版…）。
'''

mem_add = '''

## A/B 对比（v2.20，2026-09-12）
- A/B 是**异步**的：`POST /api/prompts/ab/run` 只发任务返回 `job_id`，轮询 `GET /api/prompts/ab/jobs/{job_id}`。
  绝不要再改回同步——两次完整 Produce 链路真机十几分钟，同步必超时（这正是当初「运行不了」的根因）。
- 相关代码：`src/app/api/routers/prompts.py` 的 `AB_JOBS`/`_ab_work`、`src/app/workflow/ab.py` 的 `on_progress`、
  前端 `ABState`（与 `RunState`/`ReviseState` 同构，localStorage 键 `tf_active_ab`，结果不落盘只留内存）。
- 前端易错点：系统进化页初始化时**必须**同时调 `loadVersions()` 和 `loadAbVersions()`，否则 A/B 下拉是空的。
- 慢操作统一范式（供给/重写/AB）：后台任务 + job_id + 5s 轮询 + localStorage 恢复，别让 HTTP 请求挂着等 LLM。
- 冒烟测试脚本：`tools/fetch_fixtures.py`（抓真实响应到 D:/tmp/fx，端口会自动对齐成 8000）+ `tools/smoke_render.js`。
- 本机 shell 坑续：bash 的 PATH 会间歇性坏（dirname/grep/ls 找不到），`bash xx.sh` 可能被引擎判成 wsl 而拦截；
  写文档/记忆内容一律用 Write 落 .py 再跑（`python -c "..."` 里的反引号会被 shell 当命令替换）。
'''

io.open(day, 'a', encoding='utf-8').write(day_add)
io.open(mem, 'a', encoding='utf-8').write(mem_add)
print('memory updated')
