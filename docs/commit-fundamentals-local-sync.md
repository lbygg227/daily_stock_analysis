# Commit 说明：基本面本地缓存、闲时同步与浏览页

> 相对分支：`origin/main`（基准 commit `4b3f679b`）  
> 与 [commit-exposure-graph-local-sync.md](commit-exposure-graph-local-sync.md) **同一批本地提交**（`.env` 不入库；`ARCHITECTURE_ANALYSIS.md` 视需要单独决定是否纳入）。  
> **验证状态（2026-06-27）**：基本面页随机抽查名称、行业、列表/详情正常；库内 5526/5528 有行业，缺名称已通过清单同步修复。

---

## 建议 Commit Message

```
feat: 基本面本地 SQLite 缓存、闲时同步调度与 Web 浏览页

将股票清单、季度财务、行业分类等慢变基本面数据落本地 SQLite，
支持每日清单/财务与每周行业闲时同步；分析链路优先读本地；
新增 REST API 与 dsa-web 基本面浏览页（搜索、行业 Tab、详情与分析入口）。

- 东财/交易所多源行业补全，适配 akshare 移除 stock_board_cons_ths
- partial upsert 修复 + 行业同步后自动 repair_missing_stock_names
- 调度器支持 extra daily / weekly 任务
- 前端修复财务 null 字段导致页面崩溃
```

---

## 变更概览

| 类别 | 新增 | 修改 |
|------|------|------|
| 后端 API | 2 | 1 |
| 后端服务/仓储 | 4 | 6 |
| 前端 | 4 | 4 |
| 脚本 | 1 | — |
| 测试 | 5 | 2 |
| 配置/文档 | 1 | 3 |

**规模（不含 `ARCHITECTURE_ANALYSIS.md`）：** 约 15 个修改文件 + 15 个新文件（含本文档），净增约 2500+ 行。

---

## 一、数据层与同步管线

### 新增

| 文件 | 说明 |
|------|------|
| `src/repositories/fundamental_repo.py` | 清单/财务/估值 CRUD；搜索、行业统计、分页排序；`get_stocks_without_name` |
| `src/services/fundamental_sync.py` | 新浪清单 + THS/东财财务；行业补全（东财板块代码 / 交易所名录 / 东财 f127）；`repair_missing_stock_names` |
| `src/services/fundamental_sync_task.py` | 每日 `run_scheduled_fundamental_sync`、每周 `run_scheduled_industry_sync` |
| `src/services/local_fundamental_provider.py` | 分析时从 SQLite 组装 growth/earnings/industry |
| `scripts/sync_industry_only.py` | 一次性行业补全（清代理；exchange + quote；可选 `--boards` 慢速板块扫描） |

### 修改

| 文件 | 说明 |
|------|------|
| `src/storage.py` | ORM：`StockListing`、`FinancialAbstract`、`ValuationDaily` |
| `src/repositories/__init__.py` | 导出 `FundamentalRepository` |
| `src/services/__init__.py` | 懒加载导出同步/本地 provider |

### 行为要点

- **慢变数据本地化**：清单、季度财务、行业落 SQLite；实时行情仍走原链路。
- **行业来源（优先级）**：东财板块成分（BK 代码）→ 深/北交易所名录 → 东财个股 `f127`；不依赖已移除的 `stock_board_cons_ths`。
- **名称保护**：
  - `_listing_conflict_updates`：空 `name` 不覆盖已有值；行业更新仅写 `industry_ths`。
  - `upsert_stock_listing`：已有记录走 ORM 部分更新，避免 INSERT 缺字段。
  - `repair_missing_stock_names()`：从新浪清单补全空名称；`enrich_industry_all()` 结束时自动调用。
- **每日 vs 每周**：每日同步清单+财务；每周仅补行业（不重复拉财务）。

---

## 二、配置与 CLI

### 修改

| 文件 | 说明 |
|------|------|
| `src/config.py` | `FUNDAMENTAL_LOCAL_*`、`FUNDAMENTAL_SYNC_*`、`FUNDAMENTAL_SYNC_INDUSTRY_*`；`parse_env_weekday` |
| `main.py` | `--sync-fundamentals` / `--sync-industry` / `--sync-valuation`；daily/weekly 任务；「仅基本面调度」模式 |
| `.env.example` | 环境变量注释模板 |

### 环境变量（`.env.example`；本地 `.env` 已配置但未入库）

```env
# 分析读本地
FUNDAMENTAL_LOCAL_PREFER_ENABLED=true
FUNDAMENTAL_LOCAL_MAX_AGE_DAYS=180

# 每日 02:00：清单 + 财务（与分析 SCHEDULE_TIME 独立）
FUNDAMENTAL_SYNC_ENABLED=true
FUNDAMENTAL_SYNC_TIME=02:00
FUNDAMENTAL_SYNC_RUN_IMMEDIATELY=false
FUNDAMENTAL_SYNC_INCLUDE_INDUSTRY=false   # 建议 false，行业走每周任务
FUNDAMENTAL_SYNC_INCLUDE_VALUATION=false

# 每周日 03:00：行业 + 自动补名称
FUNDAMENTAL_SYNC_INDUSTRY_ENABLED=true
FUNDAMENTAL_SYNC_INDUSTRY_TIME=03:00
FUNDAMENTAL_SYNC_INDUSTRY_WEEKDAY=6         # 0=周一 .. 6=周日，也支持 sunday
FUNDAMENTAL_SYNC_INDUSTRY_RUN_IMMEDIATELY=false
```

### CLI 示例

```bash
python main.py --sync-fundamentals
python main.py --sync-fundamentals --sync-industry
python main.py --schedule                    # 18:00 分析 + 02:00 基本面 + 周日 03:00 行业

# 仅补行业（不清财务、不覆盖名称）
python scripts/sync_industry_only.py

# 手动补全缺失名称
python -c "from src.services.fundamental_sync import FundamentalSyncService; print(FundamentalSyncService().repair_missing_stock_names())"

# 或全量刷新清单（含名称）
python -c "from src.services.fundamental_sync import FundamentalSyncService; print(FundamentalSyncService().sync_stock_list())"
```

---

## 三、调度器

| 文件 | 说明 |
|------|------|
| `src/scheduler.py` | `add_daily_task` / `add_weekly_task`；`extra_daily_tasks` + `extra_weekly_tasks`；执行时间热更新 |
| `tests/test_scheduler_extra_daily.py` | daily / weekly 任务注册 |
| `tests/test_scheduler_background.py` | Fake schedule 支持 weekday |

---

## 四、分析链路集成

| 文件 | 说明 |
|------|------|
| `data_provider/base.py` | `get_fundamental_context` / `get_stock_name` 优先读本地 SQLite（可配置 `FUNDAMENTAL_LOCAL_MAX_AGE_DAYS`） |

---

## 五、REST API

### 新增

| 文件 | 说明 |
|------|------|
| `api/v1/schemas/fundamentals.py` | 列表、详情、行业、缓存统计等模型 |
| `api/v1/endpoints/fundamentals.py` | `GET /stats`、`GET /industries`、`GET /stocks`、`GET /stocks/{code}` |

### 修改

| 文件 | 说明 |
|------|------|
| `api/v1/router.py` | 注册 `fundamentals` 路由 |

---

## 六、前端（dsa-web）

### 新增

| 文件 | 说明 |
|------|------|
| `apps/dsa-web/src/api/fundamentals.ts` | API 客户端 |
| `apps/dsa-web/src/pages/FundamentalsPage.tsx` | 状态栏、搜索/行业 Tab、财务列、详情、跳转分析 |
| `apps/dsa-web/src/pages/__tests__/FundamentalsPage.test.tsx` | 渲染测试 |

### 修改

| 文件 | 说明 |
|------|------|
| `apps/dsa-web/src/App.tsx` | `/fundamentals` 路由 |
| `apps/dsa-web/src/components/layout/SidebarNav.tsx` | 侧栏入口 |
| `apps/dsa-web/src/i18n/uiText.ts` | 中英文文案 |
| `apps/dsa-web/vite.config.ts` | 开发代理（**提交前建议恢复 8000 或改环境变量**，勿提交本地 8080 临时值） |

### 前端修复

- 财务字段为 `null` 时 `.toFixed()` 崩溃 → 改为 `!= null` 判断。

---

## 七、测试

| 文件 | 说明 |
|------|------|
| `tests/test_fundamentals_api.py` | stats / industries / stocks API |
| `tests/test_local_fundamental_provider.py` | 本地 provider |
| `tests/test_fundamental_repo.py` | partial upsert 不清空 name |
| `tests/test_scheduler_extra_daily.py` | 调度器 daily/weekly |
| `tests/test_scheduler_background.py` | weekly fake job |

---

## 八、其他

| 文件 | 说明 |
|------|------|
| `docs/commit-fundamentals-local-sync.md` | 本 commit 说明文档 |
| `CLAUDE.md` | 项目说明更新（可选纳入） |
| `ARCHITECTURE_ANALYSIS.md` | **未跟踪**；架构笔记，按需纳入 |

---

## 本地验证记录

| 项 | 结果 |
|----|------|
| 基本面页列表名称 | 随机抽查正常（曾缺 544 只沪市名称，已 `sync_stock_list` 修复为 0） |
| 行业覆盖 | 5526 / 5528（`600421`、`600599` 退市，接口无行业） |
| 页面崩溃 | 已修复 null 财务字段 |
| 定时配置 | `.env` 已设 daily 02:00 + weekly 周日 03:00（需 `--schedule` 常驻生效） |

---

## 建议 `git add` 范围

```bash
git add \
  .env.example \
  docs/commit-fundamentals-local-sync.md \
  docs/commit-exposure-graph-local-sync.md \
  docs/requirements-event-exposure-graph.md \
  api/v1/endpoints/fundamentals.py \
  api/v1/schemas/fundamentals.py \
  api/v1/router.py \
  apps/dsa-web/src/api/fundamentals.ts \
  apps/dsa-web/src/pages/FundamentalsPage.tsx \
  apps/dsa-web/src/pages/__tests__/FundamentalsPage.test.tsx \
  apps/dsa-web/src/App.tsx \
  apps/dsa-web/src/components/layout/SidebarNav.tsx \
  apps/dsa-web/src/i18n/uiText.ts \
  data_provider/base.py \
  main.py \
  scripts/sync_industry_only.py \
  src/config.py \
  src/repositories/ \
  src/scheduler.py \
  src/services/fundamental_sync.py \
  src/services/fundamental_sync_task.py \
  src/services/local_fundamental_provider.py \
  src/services/__init__.py \
  src/storage.py \
  tests/test_fundamental_repo.py \
  tests/test_fundamentals_api.py \
  tests/test_local_fundamental_provider.py \
  tests/test_scheduler_extra_daily.py \
  tests/test_scheduler_background.py

# 可选
git add CLAUDE.md
# git add ARCHITECTURE_ANALYSIS.md

# 不要提交
# apps/dsa-web/vite.config.ts   ← 本地 8080 代理临时改动
# .env                          ← 已在 .gitignore
```

---

## Test Plan（提交前自检）

- [x] 基本面页：名称、行业、分页、详情、分析跳转（人工抽查）
- [ ] `python -m unittest tests.test_fundamentals_api tests.test_fundamental_repo tests.test_local_fundamental_provider tests.test_scheduler_extra_daily -q`
- [ ] `cd apps/dsa-web && npm run test -- FundamentalsPage`
- [ ] `python main.py --sync-fundamentals --stocks 600519`
- [ ] `FUNDAMENTAL_SYNC_ENABLED=true` + `python main.py --schedule` 日志可见 daily/weekly 任务

---

## 已知限制 / 后续可选

1. **退市股**（`600421`、`600599`）公开接口无行业 → 显示「未分类」属预期。
2. **东财 496 板块全扫**很慢；日常走 exchange + quote；需全量板块时 `scripts/sync_industry_only.py --boards`。
3. **`vite.config.ts`** 代理端口建议环境变量化，避免硬编码 8080。
4. **仅 uvicorn 起 API** 不会自动刷新基本面；需 `python main.py --schedule` 或手动同步。
