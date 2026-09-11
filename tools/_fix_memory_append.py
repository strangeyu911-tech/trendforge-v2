# -*- coding: utf-8 -*-
"""修复 MEMORY.md 尾部被 shell 反引号污染的内容，并重新追加干净章节。"""
import io

p = '.workbuddy/memory/MEMORY.md'
t = io.open(p, encoding='utf-8').read()
i = t.find('## v2.19 审计整改')
if i > 0:
    t = t[:i].rstrip() + '\n'
    io.open(p, 'w', encoding='utf-8').write(t)
    print('truncated at', i)

add = '''

## v2.19 审计整改（2026-09-12，tag v2.19-audit-fix）

- 审计源 `docs/PRODUCT_AUDIT_v1.0.md`；整改记录 `docs/PRODUCT_AUDIT_FIX_v1.2.md`（v1.1 保留）。
- **UI 铁律升级**：任何枚举值渲染前必须过 `lb()`（app.js 全局 `LABEL` 表），禁止直接 String(r[0])。新增枚举先加表。
- 「迭代闭环」已更名**系统进化**（含第四段效果回收）。新接口 `GET /api/prompts/adoption-impact`；失败案例库走 `GET /api/bad-cases`（misc router，前缀 /api，不是 /api/contents）。
- 快照重建 `tools/rebuild_snapshot.py`：缺列动态 ALTER；真人校准剔除全 1 分退化行；`human_score_avg` 格式须匹配 `calibration._aggregate_content`。
- 冒烟测试 `tools/smoke_render.js`（Node+DOM 桩，回放 curl 抓取的真实响应，42 项）；配套 `tools/fetch_fixtures.sh`。
- 部署核验清单：/api/health 200、/api/markets 7 个（含 GB/IN）、adoption-impact 200、bad-cases 200、console app.js?v=2.2、侧边栏「系统进化」。
- **网络兜底**：github.com:443 不通时用 `tools/push_via_api.py <sha...>` 走 Git Data API 重放（基线取 API 远端 head，勿用陈旧 origin/main；支持删除文件与 tag）。
- **Bash 坑**：`python -c "..."` 里含反引号会被 shell 当命令替换执行并污染输出；写 md 内容一律用 Write 建 .py 脚本再跑。
- 叙事原则：Demo 不粉饰——机器分 vs 人分的差值讲成「人工校准存在的理由」；不伪造 QSR、不同步本地兜底垃圾内容。
'''
io.open(p, 'a', encoding='utf-8').write(add)
print('ok')
