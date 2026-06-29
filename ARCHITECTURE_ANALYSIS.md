# 项目架构分析：Daily Stock Analysis (DSA)

> 生成日期：2026-06-21 | 分析范围：全仓库结构、核心模块功能、实现方式与模块间关系

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构与数据流](#2-整体架构与数据流)
3. [入口层](#3-入口层)
4. [数据供给层（data_provider/）](#4-数据供给层dataprovider)
5. [核心编排层（src/core/）](#5-核心编排层srccore)
6. [业务服务层（src/services/）](#6-业务服务层srcservices)
7. [智能体系统（src/agent/）](#7-智能体系统srcagent)
8. [数据访问层（src/repositories/）](#8-数据访问层srcrepositories)
9. [API 层（api/）](#9-api-层api)
10. [机器人层（bot/）](#10-机器人层bot)
11. [通知系统（src/notification.py + notification_sender/）](#11-通知系统)
12. [前端应用（apps/）](#12-前端应用apps)
13. [策略系统（strategies/）](#13-策略系统strategies)
14. [报告系统（templates/）](#14-报告系统templates)
15. [脚本、测试与 CI/CD](#15-脚本测试与-cicd)
16. [二次开发建议](#16-二次开发建议)

---

## 1. 项目概述

**项目名称：** Daily Stock Analysis (DSA)  
**项目定位：** 基于 AI 大模型的 A 股 / 港股 / 美股自选股智能分析系统  
**技术栈：** Python 3.10+ (后端) + React/TypeScript/Vite (Web 前端) + Electron (桌面端)  
**核心流程：** 抓取数据 → 技术分析 → 新闻检索 → LLM 分析 → 生成报告 → 多渠道通知推送  
**许可协议：** MIT

### 1.1 核心能力

| 能力 | 说明 |
|------|------|
| 每日自动分析 | 定时抓取自选股票数据，进行技术面 + 基本面 + 新闻情绪分析 |
| 多市场覆盖 | A 股（沪深）、港股、美股 |
| AI 驱动决策 | LLM 多智能体协同分析，输出结构化决策信号 |
| 多渠道推送 | 企业微信、飞书、Discord、Telegram、邮件、自定义 Webhook 等 14 个渠道 |
| 回测引擎 | 对历史数据进行策略回测，验证策略有效性 |
| 大盘复盘 | 每日盘后自动化市场整体复盘 |
| Web UI | React 单页应用，支持管理自选股、告警、投资组合、查看分析报告 |
| 机器人交互 | 钉钉、飞书、Discord 等平台 Bot 支持，可对话式查询分析 |

---

## 2. 整体架构与数据流

```
┌──────────────────────────────────────────────────────────────────┐
│                        入口层                                      │
│  main.py (CLI)    server.py (API)    webui.py (独立 WebUI)         │
└─────────┬───────────────┬────────────────┬────────────────────────┘
          │               │                │
          ▼               ▼                ▼
┌──────────────────────────────────────────────────────────────────┐
│                     API 层 (api/)                                  │
│  FastAPI 路由 → 中间件(认证/错误) → v1/endpoints (12 个路由模块)   │
└───────────────────────────────┬──────────────────────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  智能体系统       │  │  业务服务层       │  │  通知系统         │
│  src/agent/      │  │  src/services/   │  │  notification.py  │
│  (LLM 多智能体)   │  │  (分析/回测/警告) │  │  notification_    │
│                  │  │                  │  │  sender/ (14ch)   │
└────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
         │                     │                     │
         ▼                     ▼                     │
┌──────────────────────────────────────────────────┐ │
│              核心编排层 (src/core/)               │ │
│  pipeline.py / market_review.py / backtest_engine│ │
└───────────────────────┬──────────────────────────┘ │
                        │                            │
         ┌──────────────┼──────────────┐             │
         ▼              ▼              ▼             │
┌──────────────┐ ┌──────────────┐ ┌──────────────┐  │
│ 数据供给层    │ │ 数据访问层    │ │ 报告渲染      │  │
│ data_provider│ │ repositories │ │ templates/   │  │
│ (策略模式 +   │ │ (SQLAlchemy) │ │ (Jinja2)     │  │
│  fallback链) │ │              │ │              │  │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘  │
       │                │                │          │
       ▼                ▼                ▼          │
┌──────────────────────────────────────────────────┐ │
│              数据存储 (SQLite)                     │◄┘
│  src/storage.py / src/repositories/               │
└──────────────────────────────────────────────────┘
```

**关键设计原则：**
- **数据源多层 Fallback**：单个外部数据源故障不阻塞分析流程
- **服务优雅降级**：搜索、社交情绪、实时行情等可选功能失败时继续运行
- **配置集中管理**：`src/config.py` 单例 Config 数据类，统一从 `.env` 加载
- **通知渠道抽象**：每个通知渠道独立实现，通过路由层统一调度
- **线程池并发**：多股票并行分析，单个股票的异步流独立

---

## 3. 入口层

### 3.1 main.py — CLI 主入口

- **文件大小：** ~1073 行
- **功能：** 命令行调度中心，支持 30+ 参数
- **实现方式：** Python argparse，在模块级别调用 `setup_env()` 确保环境变量优先加载

**执行模式：**

| 模式 | 参数 | 说明 |
|------|------|------|
| 正常分析 | `--stocks 600519` | 单次分析指定股票 |
| 调试模式 | `--debug` | 不发送通知、不保存历史 |
| 干跑模式 | `--dry-run` | 模拟执行但不推送 |
| 定时调度 | `--schedule` | 使用 `schedule` 库按配置时间运行 |
| API 服务 | `--serve / --serve-only` | 启动 FastAPI 服务 |
| WebUI | `--webui / --webui-only` | 启动 Web 界面 |
| 大盘复盘 | `--market-review` | 仅运行大盘复盘 |
| 回测 | `--backtest` | 执行策略回测 |
| 强制执行 | `--force-run` | 跳过交易日检测 |

**核心函数：** `run_full_analysis()` 封装完整的分析生命周期，包含交易日过滤、飞书文档生成、自动回测和邮件合并推送。

### 3.2 server.py — FastAPI 服务入口

- **文件大小：** ~55 行
- **功能：** 生产环境 API 服务入口，支持 `uvicorn server:app` 启动
- **实现方式：** 从 `api.app` 导入 `app` 实例，兼容旧环境变量命名

### 3.3 webui.py — WebUI 独立启动

- **文件大小：** ~59 行
- **功能：** 独立 Web UI 启动脚本
- **实现方式：** 委托给 `api.app:app`，设置 WebUI 模式标志

---

## 4. 数据供给层（data_provider/）

### 4.1 架构设计

采用**策略模式 + Fallback 链**实现多数据源统一访问：

```
请求 (fetch_daily_history)
  │
  ▼
DataFetcherManager ──► efinance (优先级0, A股/ETF)
  │ 失败               │
  ▼                   ▼
fallback ──────────► akshare (优先级1, A股全类)
  │ 失败               │
  ▼                   ▼
fallback ──────────► tushare (优先级2, A股+基本面)
  │ 失败               │
  ▼                   ▼
fallback ──────────► pytdx (优先级2同层, A股实时)
  │ 失败               │
  ▼                   ▼
fallback ──────────► baostock (优先级3, A股历史)
  │ 失败               │
  ▼                   ▼
fallback ──────────► yfinance (优先级4, 美股/港股)
  │ 失败               │
  ▼                   ▼
fallback ──────────► longbridge (优先级5, 美股/港股)
```

### 4.2 核心文件

| 文件 | 大小 | 功能 | 实现方式 |
|------|------|------|----------|
| `base.py` | 核心 | 抽象基类 + 管理器 | `BaseFetcher` 抽象类定义统一接口（`fetch_daily_history`, `fetch_realtime_quote`, `fetch_chip_distribution`）；`DataFetcherManager` 管理优先级和 fallback |
| `efinance_fetcher.py` | - | 东方财富数据（优先级0） | `efinance` 库，覆盖 A 股、ETF |
| `akshare_fetcher.py` | ~96K | AKShare 数据（优先级1） | `akshare` 库，覆盖最全面的 A 股数据 |
| `tushare_fetcher.py` | ~52K | Tushare 数据（优先级2） | `tushare` SDK，需 Token，含基本面数据 |
| `pytdx_fetcher.py` | ~18K | 通达信实时数据 | `pytdx` 库，A 股实时行情 |
| `baostock_fetcher.py` | ~14K | 宝信数据 | `baostock` 库，A 股历史日线 |
| `yfinance_fetcher.py` | ~32K | Yahoo 财经（美/港股） | `yfinance` 库，覆盖美股和港股 |
| `longbridge_fetcher.py` | ~37K | 长桥证券（美/港股） | `longbridge` SDK，美股和港股增强 |
| `tickflow_fetcher.py` | ~12K | 大盘复盘增强 | `tickflow` 库，市场微观结构数据 |
| `fundamental_adapter.py` | - | 基本面数据标准化 | 各数据源基本面数据统一归一化 |

### 4.3 关键设计

- **统一协议：** `BaseFetcher` 定义 `fetch_daily_history()`, `fetch_realtime_quote()`, `fetch_chip_distribution()`, `fetch_fundamental()` 等公共方法
- **股票代码标准化：** `canonical_stock_code()` 支持 SH/SZ/HK/US 格式互转
- **基本面归一化：** `AkshareFundamentalAdapter`（A 股）和 `YfinanceFundamentalAdapter`（美/港）将不同源的基本面数据统一为相同结构

---

## 5. 核心编排层（src/core/）

### 5.1 文件清单与功能

| 文件 | 功能 | 实现方式 |
|------|------|----------|
| `pipeline.py` | **主分析流水线** | `StockAnalysisPipeline` 类，串联数据获取→技术分析→LLM分析→通知→持久化 |
| `market_review.py` | **大盘复盘** | 市场整体分析逻辑，含板块轮动、资金流向等 |
| `market_review_lock.py` | 大盘复盘分布式锁 | 防止多次触发的文件锁机制 |
| `market_review_runtime.py` | 大盘复盘运行时 | 运行时状态管理 |
| `market_profile.py` | 市场画像 | 市场整体特征刻画（牛熊、风格、情绪） |
| `market_strategy.py` | 市场策略 | 基于市场状态的策略建议 |
| `trading_calendar.py` | 交易日历 | `exchange_calendars` 库检测 A 股(XSHG/XSHE)、港股(XHKG)、美股(XNYS)交易日 |
| `backtest_engine.py` | 回测引擎 | 基于历史数据的策略回测执行器 |
| `config_manager.py` | 配置管理器 | WebUI 可写的运行时配置持久化，将修改写回 `.env` |
| `config_registry.py` | 配置注册中心 | 所有可配置项的元数据注册（类型、默认值、描述、验证规则） |

### 5.2 StockAnalysisPipeline 生命周期

```
pipeline.run()
  ├── 1. 初始化 ─── 数据库连接、获取器管理器、分析器、通知器
  ├── 2. 数据获取 ─── 历史K线、实时行情、筹码分布、资金流、新闻、基本面
  ├── 3. 技术分析 ─── 均线、趋势、动量、RSI、成交量分析
  ├── 4. LLM 分析 ─── AI 驱动的多维度分析（可选多智能体流程）
  ├── 5. 搜索上下文 ── 多引擎新闻聚合（Tavily / Google / SearXNG）
  ├── 6. 报告生成 ─── Jinja2 渲染分析报告
  ├── 7. 通知推送 ─── 多通道发送（含噪音控制）
  ├── 8. 持久化 ─── 分析结果、决策信号写入 SQLite
  └── 9. 回测（可选）─ 自动回测检查
```

### 5.3 交易日历

```python
# 支持三个主要市场的交易日判断
- XSHG / XSHE  → A 股（沪深）
- XHKG         → 港股
- XNYS         → 美股
```

使用 `exchange_calendars` 库，提供 `is_market_open()` 和 `build_market_phase_context()` 等运行时市场状态查询。

---

## 6. 业务服务层（src/services/）

### 6.1 文件清单与功能（共 37 个文件）

| 文件 | 功能 | 实现方式 |
|------|------|----------|
| `analyzer_service.py` | **公开分析 API** | CLI/Web/Bot 的统一分析入口，`SKILL.md` 对外接口 |
| `analysis_service.py` | 分析服务 | 内部分析流程封装 |
| `analysis_context_builder.py` | 分析上下文构建 | 构造 LLM 分析所需的完整上下文数据 |
| `backtest_service.py` | 回测服务 | 回测任务管理 |
| `history_service.py` | 历史 CRUD | 分析结果的增删改查 |
| `history_loader.py` | 历史加载器 | 从数据库加载历史分析数据 |
| `history_comparison_service.py` | 历史对比 | 多期分析结果对比分析 |
| `report_renderer.py` | 报告渲染 | Jinja2 模板引擎，支持 SIMPLE/FULL/BRIEF 三种类型 |
| `task_queue.py` | 异步任务队列 | 任务生命周期管理（创建/进度/完成/失败） |
| `task_service.py` | 任务服务 | 任务查询和管理 |
| `portfolio_service.py` | 投资组合 CRUD | 自选股组合管理 |
| `portfolio_risk_service.py` | 组合风险评估 | VaR、相关性、风险评分 |
| `portfolio_alerts.py` | 组合告警 | 组合级别的预警规则 |
| `portfolio_import_service.py` | 组合导入 | 从外部导入股票列表 |
| `alert_service.py` | 告警服务 | 价格提醒规则管理 |
| `alert_worker.py` | 告警工作线程 | `EventMonitor` 后台线程持续监控价格 |
| `alert_indicators.py` | 告警指标 | 告警触发条件计算（价格突破、均线交叉等） |
| `decision_signal_service.py` | **决策信号 (P1 合约)** | 结构化的买卖决策信号，含置信度和推理链 |
| `image_stock_extractor.py` | 图片识别股票代码 | 从截图/图片中提取股票代码 |
| `stock_code_utils.py` | 股票代码标准化 | 多格式代码归一化 |
| `stock_service.py` | 股票数据服务 | 股票信息查询 |
| `stock_index_remote_service.py` | 指数远程服务 | 指数行情远程查询 |
| `name_to_code_resolver.py` | 名称转代码 | 中文名称/拼音 → 股票代码 |
| `social_sentiment_service.py` | 社交情绪分析 | Reddit/X 社交媒体情绪（美股） |
| `market_light_service.py` | 市场信号灯 | 市场状态快速评估 |
| `market_light_alerts.py` | 信号灯告警 | 市场状态变化告警 |
| `run_flow.py` | 运行流程追踪 | 分析流程的执行追踪与快照 |
| `run_diagnostics.py` | 运行诊断 | 实时诊断分析过程中的问题和性能 |
| `notification_diagnostics.py` | 通知诊断 | 通知系统健康检查 |
| `import_parser.py` | 导入解析器 | 批量导入数据解析 |
| `system_config_service.py` | 系统配置服务 | 运行时配置的读写 API |
| `agent_model_service.py` | 智能体模型服务 | 管理不同智能体的模型选择和配置 |
| `alphasift_service.py` | AlphaSift 集成 | 量化因子筛选集成服务 |

### 6.2 关键设计要点

- **决策信号（DecisionSignal）** 是 P1 级前端契约，有明确的 JSON Schema 和隔离测试
- **任务队列** 提供异步任务生命周期管理，被分析、回测、告警等操作共用
- **代码标准化** `stock_code_utils.py` 和 `name_to_code_resolver.py` 是跨模块的基础依赖

---

## 7. 智能体系统（src/agent/）

### 7.1 架构总览

```
用户请求
  │
  ▼
AgentOrchestrator (orchestrator.py)
  ├── 架构选择: quick / standard / full / specialist
  │
  ├── 1. TechnicalAgent   → 技术面分析（均线/趋势/动量/量价）
  ├── 2. IntelAgent       → 新闻情报收集与情绪分析
  ├── 3. RiskAgent        → 风险评估（波动率/回撤/相关性）
  ├── 4. 技能/策略专家     → 可插拔领域知识（缠论/波浪/趋势跟踪等）
  ├── 5. DecisionAgent    → 综合决策信号生成
  └── 6. PortfolioAgent   → 组合层面分析
       │
       ▼
  ToolRegistry (@tool 装饰器注册)
  ├── data_tools       → fetch_price, fetch_fundamental, ...
  ├── analysis_tools   → calc_ma, calc_rsi, calc_macd, ...
  ├── market_tools     → market_sentiment, sector_rotation, ...
  ├── search_tools     → web_search, news_search, ...
  └── backtest_tools   → run_backtest, strategy_compare, ...
       │
       ▼
  LLMToolAdapter (llm_adapter.py)
  └── LiteLLM 统一适配 → Anthropic / OpenAI / Gemini / DeepSeek / 国产模型...
```

### 7.2 编排模式

| 模式 | LLM 调用次数 | 使用场景 |
|------|-------------|----------|
| `quick` | ~2 次 | 快速扫描，仅技术+决策 |
| `standard` | ~3 次 | **默认模式**，技术+情报+决策 |
| `full` | ~4 次 | 深度分析，技术+情报+风险+决策 |
| `specialist` | ~5+ 次 | 全面分析，含专家技能评估 |

### 7.3 核心文件

#### orchestrator.py — 多智能体协调器

```
AgentOrchestrator
├── 接收分析上下文 (AnalysisContextPack)
├── 按选定架构依次运行智能体
├── 管理会话上下文和对话历史
└── 汇总输出决策信号
```

#### executor.py — 单智能体执行器

```
AgentExecutor (架构A: 单智能体模式)
├── 单一 LLM 实例
├── 调用所有必要工具
└── 适用于简单问答场景
```

#### agents/ — 专业智能体（5 个）

| 智能体 | 基类 | 功能 |
|--------|------|------|
| `technical_agent.py` | `BaseAgent` | 技术指标分析（均线交叉、趋势判断、MACD/RSI/KDJ、成交量形态） |
| `intel_agent.py` | `BaseAgent` | 情报收集（新闻情绪、社交媒体热度、行业动态） |
| `risk_agent.py` | `BaseAgent` | 风险评估（波动率、最大回撤、夏普比率、相关性矩阵） |
| `decision_agent.py` | `BaseAgent` | 综合决策（买卖/持有评级、目标价、置信度、风险收益比） |
| `portfolio_agent.py` | `BaseAgent` | 组合分析（仓位优化、行业分散、再平衡建议） |

#### skills/ — 技能系统（可插拔）

| 文件 | 功能 |
|------|------|
| `base.py` | 技能抽象基类 |
| `router.py` | 技能路由器 — 根据上下文选择合适技能 |
| `aggregator.py` | 技能聚合器 — 多技能结果汇总 |
| `skill_agent.py` | 技能智能体执行器 |
| `defaults.py` | 默认内置技能 |

#### strategies/ — 策略系统（可插拔）

| 文件 | 功能 |
|------|------|
| `router.py` | 策略路由器 — 根据市场状态匹配策略 |
| `aggregator.py` | 策略聚合器 — 多策略信号融合 |
| `strategy_agent.py` | 策略智能体执行器 |

#### tools/ — 工具注册表

| 文件 | 功能 | 实现方式 |
|------|------|----------|
| `registry.py` | 工具注册中心 | 提供 `@tool` 装饰器和 `ToolRegistry` 类 |
| `data_tools.py` | 数据获取工具 | 对接 `data_provider/` 获取实时/历史数据 |
| `analysis_tools.py` | 分析工具 | 技术指标计算（MA/RSI/MACD/Bollinger/等） |
| `market_tools.py` | 市场工具 | 大盘数据、板块轮动、资金流向 |
| `search_tools.py` | 搜索工具 | 多引擎新闻和网页搜索 |
| `backtest_tools.py` | 回测工具 | 历史回测执行和结果分析 |

#### llm_adapter.py — LLM 适配器

```
LLMToolAdapter
├── 基于 LiteLLM 统一多提供商接口
├── 支持工具调用 (function calling) 标准化
├── 多模型 Fallback（主模型失败时切换备用模型）
├── JSON 输出修复（json-repair）
└── 支持的提供商：
    ├── Anthropic (Claude)
    ├── OpenAI (GPT)
    ├── Google (Gemini)
    ├── DeepSeek
    ├── 国内模型（通义千问/智谱/百川等，通过 Anspire API）
    └── 本地模型（Ollama 等）
```

---

## 8. 数据访问层（src/repositories/）

### 8.1 设计模式

采用 **Repository 模式**，每个实体有独立的 Repository 类：

```
src/storage.py (DatabaseManager 单例)
  │
  ├── AnalysisRepo      → 分析结果 CRUD
  ├── StockRepo         → 股票信息和行情数据
  ├── PortfolioRepo     → 自选股组合
  ├── AlertRepo         → 告警规则和触发记录
  ├── BacktestRepo      → 回测结果
  └── DecisionSignalRepo → 决策信号
```

### 8.2 存储实现

- **数据库：** SQLite（通过 SQLAlchemy ORM）
- **连接管理：** `DatabaseManager` 单例管理连接池
- **模型定义：** `storage.py` 中使用 `declarative_base()` 定义 ORM 模型
- **迁移策略：** 自动建表（`create_all`），无 Alembic 迁移
- **数据文件：** 数据库文件位于 `data/` 目录

---

## 9. API 层（api/）

### 9.1 架构

```
FastAPI Application (api/app.py)
  │
  ├── Middleware: 认证 + 错误处理
  ├── CORS 配置 + SPA 静态文件托管
  │
  └── /api/v1 (api/v1/router.py)
      ├── /auth            → 登录/登出/状态/密码修改
      ├── /analysis        → 触发分析、状态查询
      ├── /history         → 分析历史记录 CRUD
      ├── /stocks          → 股票数据查询
      ├── /agent           → 智能体对话 API
      ├── /backtest        → 回测 CRUD
      ├── /portfolio       → 投资组合管理
      ├── /alerts          → 告警规则 CRUD
      ├── /decision-signals → 决策信号查询
      ├── /system-config   → WebUI 运行时配置
      ├── /alphasift       → AlphaSift 集成
      ├── /usage           → 使用统计
      └── /health          → 健康检查
```

### 9.2 关键设计

- **应用工厂模式：** `create_app()` 创建 FastAPI 实例，支持测试和不同部署场景
- **SPA 托管：** 自动检测 Vite 构建产物，托管前端静态文件
- **股票索引：** `/api/v1/stocks/index.json` 提供前端自动补全的股票列表
- **认证中间件：** 基于 Cookie 的管理员会话认证（PBKDF2 哈希）
- **错误处理：** 全局异常处理器，统一错误响应格式
- **Pydantic 验证：** `api/v1/schemas/` 中定义所有请求/响应模型

---

## 10. 机器人层（bot/）

### 10.1 架构

```
消息平台 → Platform Adapter → CommandDispatcher → Command Handler → Response
```

### 10.2 平台适配器（platforms/）

| 平台 | 文件 | 模式 |
|------|------|------|
| 钉钉 | `dingtalk.py` | Webhook 回调 |
| 钉钉 Stream | `dingtalk_stream.py` | Stream 长连接 |
| 飞书 | `feishu_stream.py` | Stream 长连接 |
| Discord | `discord.py` | Bot Token WebSocket |

### 10.3 命令系统（commands/）

| 命令 | 文件 | 功能 |
|------|------|------|
| `/analyze` | `analyze.py` | 触发单个/批量股票分析 |
| `/ask` | `ask.py` | AI 问答（支持多轮对话上下文） |
| `/market` | `market.py` | 大盘复盘查询 |
| `/status` | `status.py` | 系统状态查看 |
| `/history` | `history.py` | 历史分析查询 |
| `/strategies` | `strategies.py` | 策略列表和说明 |
| `/batch` | `batch.py` | 批量操作 |
| `/research` | `research.py` | 深度研究 |
| `/help` | `help.py` | 帮助信息 |
| `/chat` | `chat.py` | 自由对话 |

### 10.4 消息分发

- `dispatcher.py` — `CommandDispatcher` 类，解析消息意图、路由到对应命令处理器
- `handler.py` — 消息处理器基类和通用逻辑
- `models.py` — `BotMessage` 统一消息数据模型

---

## 11. 通知系统

### 11.1 架构

```
NotificationService (src/notification.py)
  ├── 路由层 (notification_routing.py)
  │   └── 根据配置选择目标渠道
  ├── 噪音控制 (notification_noise.py)
  │   ├── 冷却期检测
  │   ├── 安静时段过滤
  │   └── 严重性阈值过滤
  └── 发送层 (notification_sender/ + 各 sender)
      ├── FeishuSender     → 飞书 Webhook（文本/富文本/图片）
      ├── WechatSender     → 企业微信（文本/Markdown/图片）
      ├── TelegramSender   → Telegram Bot（Markdown/图片）
      ├── DiscordSender    → Discord Webhook（Embed/文本）
      ├── SlackSender      → Slack Webhook（Block Kit）
      ├── EmailSender      → SMTP 邮件（HTML/附件）
      ├── PushoverSender   → Pushover 推送
      ├── PushplusSender   → PushPlus 推送
      ├── Serverchan3Sender → Server酱3
      ├── NtfySender       → ntfy.sh
      ├── GotifySender     → Gotify 自托管
      ├── AstrbotSender    → AstrBot 平台
      └── CustomWebhookSender → 自定义 Webhook
```

### 11.2 噪音控制机制

```
evaluate_notification_noise()
  ├── 1. 频率冷却 → 同股票同类型告警在冷却期内不重复发送
  ├── 2. 安静时段 → 非交易时段可配置静音
  └── 3. 严重性过滤 → 低严重性通知在特定条件下跳过
```

---

## 12. 前端应用（apps/）

### 12.1 Web 前端（apps/dsa-web/）

- **技术栈：** React 18 + TypeScript + Vite + Tailwind CSS + Zustand（状态管理）
- **构建：** `npm ci && npm run build` 生成 `dist/` 目录
- **测试：** Vitest（单元）+ Playwright（E2E）
- **代码检查：** ESLint 扁平配置
- **主要能力：** 仪表盘、自选股管理、分析报告查看、告警配置、回测管理、投资组合、系统设置

### 12.2 桌面端（apps/dsa-desktop/）

- **框架：** Electron
- **结构：**
  - `main.js` (~54K) — Electron 主进程
  - `preload.js` — 安全上下文桥接
  - `renderer/` — React 渲染器（使用 Vite 构建产物）
- **打包：** NSIS 安装程序（`installer.nsh`）

---

## 13. 策略系统（strategies/）

### 13.1 策略文件清单（YAML 格式，共 15 种）

| 策略 | 文件 | 类型 |
|------|------|------|
| 缩量回调 | `shrink_pullback.yaml` | 技术形态 |
| 均线金叉 | `ma_golden_cross.yaml` | 趋势跟踪 |
| 箱体震荡 | `box_oscillation.yaml` | 震荡策略 |
| 放量突破 | `volume_breakout.yaml` | 突破策略 |
| 底部放量 | `bottom_volume.yaml` | 反转策略 |
| 缠论 | `chan_theory.yaml` | 技术理论 |
| 波浪理论 | `wave_theory.yaml` | 技术理论 |
| 一阳三阴 | `one_yang_three_yin.yaml` | K 线形态 |
| 龙头战法 | `dragon_head.yaml` | 热点策略 |
| 情绪周期 | `emotion_cycle.yaml` | 行为金融 |
| 事件驱动 | `event_driven.yaml` | 事件策略 |
| 预期重估 | `expectation_repricing.yaml` | 基本面 |
| 成长质量 | `growth_quality.yaml` | 基本面 |
| 热门主题 | `hot_theme.yaml` | 主题投资 |
| 牛市趋势 | `bull_trend.yaml` | 趋势跟踪 |

### 13.2 策略结构

每个 YAML 文件定义：
- **元数据：** 名称、描述、适用市场
- **指标：** 需要的技术指标和参数
- **条件：** 进场/出场条件逻辑
- **信号：** 买卖信号生成规则

被 `src/agent/strategies/` 系统和 Bot `/strategies` 命令共同使用。

---

## 14. 报告系统（templates/）

### 14.1 模板文件

| 模板 | 用途 |
|------|------|
| `report_markdown.j2` | **完整分析报告** — 仪表盘风格，含技术面/基本面/新闻情绪 |
| `report_brief.j2` | 简要报告 — 移动端优化，关键指标摘要 |
| `report_wechat.j2` | 企业微信版 — 适配微信 Markdown 限制 |
| `_macros.j2` | 共享 Jinja2 宏 — 指标卡片、表格、K 线概要等复用组件 |

### 14.2 渲染流程

```
分析结果数据
  │
  ▼
ReportRenderer (src/services/report_renderer.py)
  ├── ReportType.SIMPLE → report_brief.j2
  ├── ReportType.FULL   → report_markdown.j2
  └── ReportType.BRIEF  → report_brief.j2
       │
       ▼
     Markdown 文本
       │
       ▼ (可选)
  md2img.py → 图片 (wkhtmltoimage/imgkit)
```

### 14.3 Markdown 转图片

`src/md2img.py` 提供 Markdown 到图片的转换能力，用于不支持 Markdown 的通知渠道（如企业微信图片推送）。

---

## 15. 脚本、测试与 CI/CD

### 15.1 脚本（scripts/）

| 脚本 | 功能 |
|------|------|
| `ci_gate.sh` | **本地 CI 门控** — 语法检查 → Flake8 → 确定性测试 → 离线测试（提交前执行） |
| `test.sh` | 测试运行器 |
| `check_ai_assets.py` | AI 治理资产一致性验证（AGENTS.md/CLAUDE.md/GitHub指令） |

### 15.2 测试（tests/）

- **框架：** pytest
- **规模：** 200+ 测试文件
- **标记：** `unit`（单元测试）/ `integration`（集成测试）/ `network`（网络测试）
- **夹具：** `tests/fixtures/`
- **LLM 模拟：** `tests/litellm_stub.py` 提供 LLM 调用的 stub

```
pytest -m "not network"    # 离线测试（CI 门控用）
pytest -m unit             # 仅单元测试
pytest -m network          # 仅网络测试
```

### 15.3 CI/CD（.github/workflows/）

| 工作流 | 触发 | 功能 |
|--------|------|------|
| `ci.yml` | PR → main | 全量门控（代码治理、后端、Web、Docker） |
| `00-daily-analysis.yml` | 定时（工作日 UTC 10:00） | 每日自动分析运行 |
| `docker-publish.yml` | Release | Docker 镜像构建推送 |
| `desktop-release.yml` | Tag | Electron 桌面端构建发布 |
| `auto-tag.yml` | Push | 版本自动标签（#patch/#minor/#major） |
| `network-smoke.yml` | 定时/手动 | 外部依赖连通性测试 |

### 15.4 Docker 部署

```
docker/
├── Dockerfile          # 多阶段构建（Node前端 → Python 3.11-slim）
├── docker-compose.yml  # analyzer(定时) + server(FastAPI) 双服务
└── entrypoint.sh       # bind-mount 权限修复
```

---

## 16. 二次开发建议

### 16.1 模块间依赖关系（按耦合度排序）

```
高耦合（修改需谨慎）:
  src/config.py       ← 全项目依赖，几乎每个模块都引用
  src/storage.py      ← 数据层基础，repositories 和 services 依赖
  data_provider/base.py ← 数据获取的统一接口，fetcher 和 pipeline 依赖
  src/enums.py        ← 共享枚举，跨模块引用

中耦合（关注接口契约）:
  src/services/decision_signal_service.py  ← P1 前端契约，前后端共享 Schema
  src/services/report_renderer.py          ← 模板路径和 ReportType 契约
  src/agent/orchestrator.py               ← 智能体流程编排，tools/skills/strategies 注册
  api/v1/schemas/                          ← API 契约，前后端桥接

低耦合（可独立修改）:
  src/notification_sender/<channel>.py  ← 每个渠道独立，互不影响
  bot/platforms/<platform>.py           ← 每个平台独立
  data_provider/<fetcher>.py            ← 每个获取器独立，通过 base.py 协议统一
  tests/                                ← 测试套件独立
  strategies/*.yaml                     ← YAML 策略定义，独立文件
```

### 16.2 常见二次开发场景与切入点

#### 场景 1：接入新数据源

1. 在 `data_provider/` 中新建 `<name>_fetcher.py`
2. 继承 `data_provider/base.py` 中的 `BaseFetcher` 抽象类
3. 实现 `fetch_daily_history()` 等必要方法
4. 在 `base.py` 的 `DataFetcherManager` 中注册优先级
5. 如需基本面数据，在 `fundamental_adapter.py` 中添加对应的 Adapter

#### 场景 2：增加新分析指标

1. **技术指标：** 在 `src/agent/tools/analysis_tools.py` 中新增计算函数，通过 `@tool` 装饰器注册
2. **智能体分析维度：** 新建 `src/agent/agents/<name>_agent.py`，继承 `BaseAgent`，在 `orchestrator.py` 编排中插入
3. **报告展示：** 在 `templates/report_markdown.j2` 和 `templates/_macros.j2` 中添加展示区块

#### 场景 3：新增通知渠道

1. 在 `src/notification_sender/` 中新建 `<name>_sender.py`
2. 实现发送方法（至少 `send_text()` / `send_markdown()` / `send_image()`）
3. 在 `src/notification_sender/__init__.py` 中注册导出
4. 在 `src/config.py` 中添加对应的配置字段
5. 在 `src/notification_routing.py` 中添加路由逻辑

#### 场景 4：新增 Bot 平台

1. 在 `bot/platforms/` 中新建 `<platform>.py`，继承平台基类
2. 在 `bot/commands/` 中复用或新增命令处理器
3. 在 `bot/dispatcher.py` 中注册平台适配器

#### 场景 5：新增分析策略

1. 在 `strategies/` 中新建 `<name>.yaml`，定义策略元数据、指标、条件、信号
2. 如是 AI 驱动策略，在 `src/agent/strategies/` 中注册
3. 如是量化回测策略，确认 `src/core/backtest_engine.py` 可解析你的策略格式

#### 场景 6：扩展 Web UI 功能

1. **纯前端功能：** 在 `apps/dsa-web/src/` 中开发，按 React/Zustand/Tailwind 模式
2. **需要后端 API：** 先在 `api/v1/schemas/` 定义 Pydantic 模型，在 `api/v1/endpoints/` 新增路由，然后在 `src/services/` 实现业务逻辑，最后调用 `src/repositories/` 持久化
3. **前后端契约：** 关键数据结构使用 Pydantic → TypeScript 类型映射

### 16.3 开发流程建议

```
1. 阅读 CLAUDE.md / AGENTS.md 了解规则
2. 阅读本文档了解全貌
3. 确认改动属于哪个目录边界
4. 搜索现有实现避免重复造轮子（优先复用）
5. 开发 → 运行 scripts/ci_gate.sh → 修复问题 → 提交
6. 如涉及报告/UI，PR 附截图
7. 如涉及新配置，同步更新 .env.example
```

### 16.4 关键技术决策点

| 决策点 | 当前选择 | 如需变更的方向 |
|--------|----------|---------------|
| 数据库 | SQLite | 如需支持更高并发，可迁移到 PostgreSQL |
| ORM | SQLAlchemy | 如需更轻量，可考虑原生 SQL |
| LLM 适配 | LiteLLM | 如需更精细控制，可直接使用各 SDK |
| 报告模板 | Jinja2 字符串渲染 | 如需更丰富的可视化，可引入 ECharts/Plotly |
| 配置存储 | .env 文件 + Config 单例 | 如需集群部署，可迁移到 etcd/Consul |
| 任务调度 | schedule 库（进程内） | 如需分布式，可迁移到 Celery + Redis |
| 前端状态 | Zustand | 如需更复杂状态管理，可升级到 Redux Toolkit |

### 16.5 架构风险点

| 风险 | 说明 | 缓解建议 |
|------|------|----------|
| `src/config.py` 过于庞大 | ~3000 行单文件，修改冲突频繁 | 考虑按功能域拆分配置文件 |
| `src/storage.py` 过于庞大 | ~108K 单文件，混合了 ORM 模型和数据库管理 | 考虑将 ORM 模型按实体拆分 |
| `src/search_service.py` 庞大 | ~159K 单文件，多搜索引擎逻辑集中 | 考虑按搜索引擎拆分策略类 |
| 异步任务单机 | 基于 ThreadPoolExecutor，不可分布式 | 如需水平扩展，引入任务队列中间件 |
| 无数据库迁移 | `create_all` 自动建表，无版本控制 | 如需生产级部署，引入 Alembic |
| LLM 工具调用依赖 JSON 解析 | 不稳定的 JSON 输出可能导致工具调用失败 | 已有 `json-repair` 缓解，但仍有边界情况 |

---

> **文档维护说明：** 本文档基于 2026-06-21 的代码库状态生成。项目结构或模块职责发生重大变化时，请更新本文档对应章节。
