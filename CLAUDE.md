# CLAUDE.md

See `AGENTS.md` for the definitive development rules, review process, stability guardrails, and contribution quality standards. This file provides quick-reference architecture and commands.

## Entry Points

- `main.py` — CLI entry point for daily analysis (`--debug`, `--dry-run`, `--stocks`, `--market-review`, `--schedule`, `--serve`, `--serve-only`, `--webui`, `--webui-only`)
- `server.py` — FastAPI service (uvicorn entry, imports from `api.app`)
- `webui.py` — Standalone Web UI launcher
- `api/app.py` — FastAPI application factory, router registration, CORS setup

## Architecture

```
main.py / server.py / webui.py         ← entry points
├── src/config.py                       ← Config dataclass (singleton), .env loading
├── src/core/                           ← orchestration pipeline, trading calendar, market review
│   ├── pipeline.py                     ← main analysis pipeline orchestration
│   ├── market_review.py                ← market review logic
│   ├── trading_calendar.py             ← A/H/US trading day detection
│   └── backtest_engine.py              ← backtesting engine
├── src/services/                       ← business service layer
│   ├── analyzer_service.py             ← stock/market analysis entry point (SKILL.md API)
│   ├── analysis_service.py             │
│   ├── report_renderer.py              ← report generation (Jinja2)
│   ├── history_service.py              ← analysis history CRUD
│   ├── task_queue.py / task_service.py  ← async task management
│   ├── portfolio_service.py            ← portfolio CRUD
│   ├── alert_service.py / alert_worker.py  ← price alerts
│   ├── decision_signal_service.py      ← DecisionSignal (P1 contract)
│   ├── image_stock_extractor.py        ← image → stock code extraction
│   ├── stock_code_utils.py             ← stock code normalization
│   ├── run_flow.py / run_diagnostics.py  ← run-flow tracking & diagnostics
│   └── social_sentiment_service.py     ← Reddit/X sentiment (US stocks)
├── data_provider/                      ← multi-source data with fallback chain
│   ├── base.py                         ← abstract fetcher, canonical_stock_code
│   ├── efinance_fetcher.py             ← Priority 0 (East Money)
│   ├── akshare_fetcher.py              ← Priority 1
│   ├── tushare_fetcher.py / pytdx_fetcher.py / baostock_fetcher.py
│   ├── yfinance_fetcher.py             ← Priority 4 (Yahoo, US/HK fallback)
│   ├── longbridge_fetcher.py           ← Priority 5 (US/HK)
│   ├── tickflow_fetcher.py             ← market review enhancement
│   └── fundamental_adapter.py          ← fundamental data normalization
├── api/                                ← FastAPI REST API
│   ├── v1/endpoints/                   ← agents, analysis, auth, backtest, portfolio, stocks, alerts, etc.
│   ├── v1/schemas/                     ← Pydantic request/response models
│   ├── middlewares/auth.py             ← admin auth middleware
│   └── middlewares/error_handler.py    ← global error handler
├── bot/                                ← chat bot platforms
│   ├── platforms/                      ← dingtalk, discord, feishu_stream
│   ├── commands/                       ← analyze, ask, market, strategies, etc.
│   └── dispatcher.py / handler.py
├── src/agent/                          ← Agent strategy system
│   ├── agents/                         ← technical, intel, risk, decision, portfolio agents
│   ├── skills/                         ← aggregator, router, skill_agent
│   ├── strategies/                     ← strategy agent & router
│   ├── tools/                          ← data, analysis, market, search, backtest tool registry
│   ├── llm_adapter.py                  ← litellm-based LLM adapter
│   └── orchestrator.py                 ← multi-agent orchestration
├── src/schemas/                        ← shared schema/contracts
│   ├── analysis_context_pack.py
│   ├── decision_action.py
│   ├── report_schema.py
│   └── market_light.py
├── src/notification_sender/            ← individual channel senders (discord, feishu, email, telegram, etc.)
├── src/llm/                            ← LLM generation params, error handling
├── src/repositories/                   ← data access layer (SQLAlchemy)
├── src/enums.py                        ← shared enums (ReportType, etc.)
├── src/storage.py                      ← key-value file-based storage
├── src/notification.py                 ← NotificationService (routing, noise control)
├── src/auth.py                         ← admin authentication
├── src/formatters.py / md2img.py       ← format utilities
├── src/market_context.py / market_analyzer.py / market_phase_*.py  ← market analysis
├── apps/
│   ├── dsa-web/                        ← React/TypeScript web frontend (npm ci, npm run build)
│   └── dsa-desktop/                    ← Electron desktop app
├── scripts/
│   ├── ci_gate.sh                      ← local pre-CI gate (syntax, flake8, tests)
│   └── test.sh                         ← test runner
├── .github/workflows/                  ← CI, daily analysis, docker publish, desktop release
├── docker/                             ← Dockerfile & compose
├── docs/                               ← documentation (see INDEX.md)
└── tests/                              ← pytest (markers: unit, integration, network)
```

## Key Design Patterns

- **Data source fallback chain**: `efinance → akshare → tushare/pytdx → baostock → yfinance → longbridge`. Each fetcher implements the same protocol. Failure in one data source does not block the whole analysis.
- **Notification channel abstraction**: Each channel (feishu, discord, email, etc.) is a standalone sender in `src/notification_sender/`. `NotificationService` handles routing, noise control, quiet hours, and channel-level fallback.
- **Agent system**: Multi-agent orchestration (`orchestrator.py`) with specialized agents (technical, intel, risk, decision, portfolio). Tools registered via `tools/registry.py`. Supports strategy plugins and multi-turn chat.
- **Report rendering**: Jinja2 templates in `templates/`. `ReportType` enum controls detail level (SIMPLE / FULL / BRIEF).
- **Config**: `Config` dataclass in `src/config.py`, loaded from `.env`. All secrets and environment-specific values go through `get_config()`.
- **Task queue**: `src/services/task_queue.py` provides async task lifecycle (create, progress, complete/fail). Used by analysis, backtest, and alert operations.

## Common Commands

### Python Backend
```bash
pip install -r requirements.txt          # install deps
python main.py --debug --stocks 600519  # analyze specific stock in debug mode
python main.py --serve                   # API + analysis
python main.py --webui                   # launch web UI

# Validation
python -m py_compile main.py src/config.py src/auth.py  # syntax check
python -m pytest -m "not network"                       # offline tests
python -m pytest -m "unit" tests/test_config.py         # single test file
python -m pytest tests/test_auth.py::test_func_name -k  # single test
./scripts/ci_gate.sh                   # full backend gate
flake8 . --count --select=E9,F63,F7,F82 --show-source   # critical flake8
```

### Web Frontend
```bash
cd apps/dsa-web
npm ci
npm run lint
npm run build
```

### Desktop
```bash
cd apps/dsa-desktop
npm install
npm run build
```

### CI / Git
```bash
python scripts/check_ai_assets.py       # verify AI governance asset consistency
git commit --allow-empty -m "feat: ..." # commit messages in English
```

## Key Conventions (from AGENTS.md)

- Backend logic → `src/`, `data_provider/`, `api/`, `bot/`
- Web frontend → `apps/dsa-web/`
- Desktop → `apps/dsa-desktop/`
- Deploy/CI → `scripts/`, `.github/workflows/`, `docker/`
- No hardcoded keys, paths, model names, ports
- New config items → update `.env.example` + docs simultaneously
- PRs that change reports/UI must include screenshots
- Prefer reuse over new parallel implementations
- `docs/CHANGELOG.md` [Unreleased] uses flat format; no `###` sub-headings
