# Commit 说明：事件驱动暴露图谱、主题包与 Web 暴露页

> 相对分支：`origin/main`  
> 与 [commit-fundamentals-local-sync.md](commit-fundamentals-local-sync.md) **同一批本地提交**（`.env` 不入库）。  
> **验证状态（2026-06-29）**：主题包已导入；Tavily 搜索可用后 ingest 可写入 `event_signal`；Web `/exposure` 修复 API 前缀后正常展示。

---

## 建议 Commit Message

```
feat: 事件驱动暴露图谱全链路与 Web 暴露页

以 SQLite 图谱为 SSOT，实现主题包导入、图谱同步、公告抽边、
事件 ingest、增量分析与板块共振；新增半导体主题包与 /exposure 管理页。

- Phase 1-5：仓储/API/Worker/CLI + 运营反馈
- 主题包 changxin_chain、semiconductor_chain（冷启动种子）
- 修复 Web exposure API 缺少 /api/v1 前缀导致页面崩溃
- 配套单测与 requirements v0.5 文档
```

---

## 变更概览

| 类别 | 新增 | 修改 |
|------|------|------|
| 数据层 / 仓储 | 6 | 1 |
| 后端服务 | 12 | 4 |
| REST API | 4 | 1 |
| CLI / 脚本 | 3 | 2 |
| 主题包 | 3 | — |
| 前端 | 5 | 4 |
| 测试 | 10 | 1 |
| 文档 | 2 | 2 |

---

## 一、数据模型（`src/storage.py`）

| 表 | 用途 |
|----|------|
| `entity_alias` | 主题/公司实体与别名 |
| `company_profile` | 表观主业、定价备注 |
| `company_exposure` | 暴露边（图谱 SSOT） |
| `analysis_baseline_cache` | 全量分析基线（增量对比） |
| `event_signal` | 盘中事件 inbox |
| `event_push_cooldown` / `event_sector_cooldown` | 推送与板块冷却 |
| `exposure_feedback` | 运营反馈（误报/禁用边） |

---

## 二、核心服务（Phase 1–5）

| 文件 | 说明 |
|------|------|
| `src/repositories/exposure_repo.py` | 图谱 CRUD、文本反查实体 |
| `src/repositories/event_signal_repo.py` | 事件信号去重与状态 |
| `src/repositories/exposure_feedback_repo.py` | 反馈与边禁用 |
| `src/services/theme_pack_importer.py` | YAML 主题包导入 |
| `src/services/exposure_graph_sync.py` | 自选股节点、ingest 查询词推导 |
| `src/services/exposure_edge_extractor.py` | 公告搜索 + 规则抽边 |
| `src/services/exposure_event_ingest.py` | 图谱驱动新闻/公告 ingest |
| `src/services/exposure_event_worker.py` | 定时 Worker 编排 |
| `src/services/event_delta_analysis.py` | LLM 增量分析 |
| `src/services/event_delta_processor.py` | 基线对比 + 推送 |
| `src/services/sector_resonance_service.py` | 板块共振摘要 |
| `src/services/baseline_cache_service.py` | 分析完成后写基线 |

---

## 三、CLI（`main.py`）

```bash
python main.py --import-theme-pack semiconductor_chain
python main.py --import-theme-pack changxin_chain
python main.py --sync-exposure-graph
python main.py --extract-exposure-edges
python main.py --run-exposure-ingest --force-exposure-ingest
python main.py --run-event-delta
```

辅助脚本：

- `scripts/import_theme_pack.py`
- `scripts/check_exposure_db.py`（需 `PYTHONPATH=项目根`）

---

## 四、主题包（`config/themes/`）

| 包 ID | 说明 |
|-------|------|
| `changxin_chain` | 长鑫/存储紧缺示例 |
| `semiconductor_chain` | 半导体、存储、光模块、设备、封装、PCB；映射自选股 600487/600584/601066/002409/002463 |

日常 ingest **不**依赖 `.env` 关键词枚举；查询词由 `ExposureGraphSyncService.build_ingest_queries_from_graph()` 推导。

---

## 五、REST API

| 前缀 | 文件 |
|------|------|
| `/api/v1/exposure/*` | `api/v1/endpoints/exposure.py` |
| `/api/v1/events/*` | `api/v1/endpoints/events.py` |

运营能力：边列表/更新/反馈、事件 inbox、误报标记。

---

## 六、前端（dsa-web）

| 文件 | 说明 |
|------|------|
| `apps/dsa-web/src/pages/ExposurePage.tsx` | 暴露边 + 事件 Inbox |
| `apps/dsa-web/src/api/exposure.ts` | API 客户端 |
| `apps/dsa-web/src/types/exposure.ts` | 类型定义 |

### 本次修复（2026-06-29）

- **根因**：`exposure.ts` 请求 `/exposure/edges`，被 SPA 回退为 HTML，`edges` 为 `undefined`，渲染 `edges.map` 触发 ErrorBoundary「页面加载失败」。
- **修复**：全部改为 `/api/v1/exposure/...`、`/api/v1/events/...`；页面侧 `items ?? []` 兜底。
- 路由：`/exposure`；侧栏「暴露图谱」。

---

## 七、配置（`.env.example`）

```env
# EXPOSURE_GRAPH_ENABLED=false
# EXPOSURE_EVENT_WORKER_ENABLED=false
# THEME_NEWS_INGEST_ENABLED=false
# EXPOSURE_INGEST_QUERY_MODE=graph
# EVENT_DELTA_ANALYSIS_ENABLED=false
# TAVILY_API_KEYS=          # 推荐；公共 SearXNG 易限流
# SEARXNG_PUBLIC_INSTANCES_ENABLED=true
```

---

## 八、测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_exposure_repo.py` | 仓储 |
| `tests/test_exposure_graph_sync.py` | 图谱同步 |
| `tests/test_exposure_edge_extractor.py` | 抽边 |
| `tests/test_exposure_event_worker.py` | Worker |
| `tests/test_event_delta_processor.py` | 增量处理 |
| `tests/test_sector_resonance.py` | 板块共振 |
| `tests/test_exposure_api.py` / `test_events_api.py` / `test_exposure_ops_api.py` | API |
| `apps/dsa-web/src/api/__tests__/exposure.test.ts` | API 路径前缀 |
| `apps/dsa-web/src/pages/__tests__/ExposurePage.test.tsx` | 页面渲染 |

---

## 本地验证记录（2026-06-29）

| 项 | 结果 |
|----|------|
| `python main.py --import-theme-pack semiconductor_chain` | aliases=6, exposures=13 |
| `python main.py --sync-exposure-graph` | watchlist_nodes=5, ingest_queries=20 |
| Tavily 配置后 ingest | inserted=4 event_signal |
| Web `http://127.0.0.1:8888/exposure` | 修复 API 前缀后正常 |
| 暴露相关单测（venv） | 21 passed |

---

## 建议 `git add` 范围（暴露图谱部分）

```bash
git add \
  docs/commit-exposure-graph-local-sync.md \
  docs/requirements-event-exposure-graph.md \
  config/themes/ \
  api/v1/endpoints/exposure.py api/v1/endpoints/events.py \
  api/v1/schemas/exposure.py api/v1/schemas/events.py \
  scripts/check_exposure_db.py scripts/import_theme_pack.py \
  src/repositories/exposure_repo.py src/repositories/event_signal_repo.py \
  src/repositories/exposure_feedback_repo.py \
  src/repositories/event_push_cooldown_repo.py \
  src/repositories/event_sector_cooldown_repo.py \
  src/services/exposure_*.py src/services/event_delta_*.py \
  src/services/theme_pack_importer.py src/services/sector_resonance_service.py \
  src/services/baseline_cache_service.py \
  apps/dsa-web/src/api/exposure.ts apps/dsa-web/src/types/exposure.ts \
  apps/dsa-web/src/pages/ExposurePage.tsx \
  apps/dsa-web/src/api/__tests__/exposure.test.ts \
  apps/dsa-web/src/pages/__tests__/ExposurePage.test.tsx \
  tests/test_exposure*.py tests/test_events_api.py tests/test_sector_resonance.py \
  tests/test_event_delta_processor.py \
  main.py src/config.py src/storage.py src/core/pipeline.py \
  src/repositories/__init__.py src/services/__init__.py \
  api/v1/router.py docs/INDEX.md .env.example \
  apps/dsa-web/src/App.tsx apps/dsa-web/src/components/layout/ \
  apps/dsa-web/src/i18n/uiText.ts apps/dsa-web/vite.config.ts
```

**不要提交**：`.env`（含 Tavily/DeepSeek Key）、`static/`（构建产物）、`ARCHITECTURE_ANALYSIS.md`（可选）。

---

## Test Plan

- [x] `python -m unittest discover -s tests -p 'test_exposure*.py' -q`
- [x] `cd apps/dsa-web && npm run test -- exposure`
- [ ] 配置 `TAVILY_API_KEYS` 后重跑 `--extract-exposure-edges` 与 `--run-exposure-ingest`
- [ ] 自选股全量分析后开启 `EVENT_DELTA_ANALYSIS_ENABLED` 验证增量推送
