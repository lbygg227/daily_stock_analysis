# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================

职责：
1. 协调各模块完成股票分析流程
2. 实现低并发的线程池调度
3. 全局异常处理，确保单股失败不影响整体
4. 提供命令行入口

使用方式：
    python main.py              # 正常运行
    python main.py --debug      # 调试模式
    python main.py --dry-run    # 仅获取数据不分析

交易理念（已融入分析）：
- 严进策略：不追高，乖离率 > 5% 不买入
- 趋势交易：只做 MA5>MA10>MA20 多头排列
- 效率优先：关注筹码集中度好的股票
- 买点偏好：缩量回踩 MA5/MA10 支撑
"""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import dotenv_values
from src.config import setup_env

_INITIAL_PROCESS_ENV = dict(os.environ)
setup_env()

# 代理配置 - 通过 USE_PROXY 环境变量控制，默认关闭
# GitHub Actions 环境自动跳过代理配置
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    # 本地开发环境，启用代理（可在 .env 中配置 PROXY_HOST 和 PROXY_PORT）
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

if os.getenv("DSA_PACKAGED_ALPHASIFT_IMPORT_PROBE") == "1":
    import importlib
    import sys

    try:
        importlib.import_module("alphasift.dsa_adapter")
    except Exception as exc:
        print(f"ERROR: packaged AlphaSift adapter import failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print("OK: packaged AlphaSift adapter import succeeded")
    sys.exit(0)

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta

from data_provider.base import canonical_stock_code
from src.webui_frontend import prepare_webui_frontend_assets
from src.config import get_config, Config
from src.logging_config import setup_logging


logger = logging.getLogger(__name__)
_RUNTIME_ENV_FILE_KEYS = set()
_PUBLIC_BIND_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*"})


def _get_active_env_path() -> Path:
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parent / ".env"


def _is_public_bind_host(host: str) -> bool:
    return (host or "").strip().lower() in _PUBLIC_BIND_HOSTS


def _warn_if_public_webui_without_auth(host: str) -> None:
    if not _is_public_bind_host(host):
        return

    from src.auth import is_auth_enabled

    if is_auth_enabled():
        return
    logger.warning(
        "WEBUI_HOST=%s binds the Web UI to a public interface while "
        "ADMIN_AUTH_ENABLED=false. Keep this service behind a trusted network "
        "boundary or enable admin authentication before exposing it.",
        host,
    )


def _read_active_env_values() -> Optional[Dict[str, str]]:
    env_path = _get_active_env_path()
    if not env_path.exists():
        return {}

    try:
        values = dotenv_values(env_path)
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("读取配置文件 %s 失败，继续沿用当前环境变量: %s", env_path, exc)
        return None

    return {
        str(key): "" if value is None else str(value)
        for key, value in values.items()
        if key is not None
    }


_ACTIVE_ENV_FILE_VALUES = _read_active_env_values() or {}
_RUNTIME_ENV_FILE_KEYS = {
    key for key in _ACTIVE_ENV_FILE_VALUES
    if key not in _INITIAL_PROCESS_ENV
}

# setup_env() already ran at import time above.
_env_bootstrapped = True


def _bootstrap_environment() -> None:
    """Load .env and apply optional local proxy settings.

    Guarded to be idempotent so it can safely be called from lazy-import
    paths used by API / bot consumers.
    """
    global _env_bootstrapped
    if _env_bootstrapped:
        return

    from src.config import setup_env

    setup_env()

    if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
        proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
        proxy_port = os.getenv("PROXY_PORT", "10809")
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url

    _env_bootstrapped = True


def _setup_bootstrap_logging(debug: bool = False) -> None:
    """Initialize stderr-only logging before config is loaded.

    File handlers are deferred until ``config.log_dir`` is known (via the
    subsequent ``setup_logging()`` call) so that healthy runs never create
    log files in a hard-coded directory.
    """
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)


def _setup_runtime_logging(log_dir: str, debug: bool = False) -> bool:
    """Switch to configured logging, falling back to console on file I/O errors."""
    try:
        setup_logging(log_prefix="stock_analysis", debug=debug, log_dir=log_dir)
        return True
    except OSError as exc:
        logger.warning(
            "文件日志初始化失败，已降级为控制台日志输出；日志目录 %r 当前不可写或不可创建: %s。"
            "官方 Docker 镜像启动入口会自动修复默认挂载目录权限；若仍失败，"
            "请检查是否使用了 --user、只读挂载、rootless Docker 或 NFS 等限制写入的环境。",
            log_dir,
            exc,
        )
        return False


def _get_stock_analysis_pipeline():
    """Lazily import StockAnalysisPipeline for external consumers.

    Also ensures env/proxy bootstrap has run so that API / bot consumers
    that never call ``main()`` still get ``USE_PROXY`` applied.
    """
    _bootstrap_environment()
    from src.core.pipeline import StockAnalysisPipeline as _Pipeline

    return _Pipeline


class _LazyPipelineDescriptor:
    """Descriptor that resolves StockAnalysisPipeline on first attribute access."""

    _resolved = None

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if self._resolved is None:
            self._resolved = _get_stock_analysis_pipeline()
        return self._resolved


class _ModuleExports:
    StockAnalysisPipeline = _LazyPipelineDescriptor()


_exports = _ModuleExports()


def __getattr__(name: str):
    if name == "StockAnalysisPipeline":
        return _exports.StockAnalysisPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _reload_env_file_values_preserving_overrides() -> None:
    """Refresh `.env`-managed env vars without clobbering process env overrides."""
    global _RUNTIME_ENV_FILE_KEYS

    latest_values = _read_active_env_values()
    if latest_values is None:
        return

    managed_keys = {
        key for key in latest_values
        if key not in _INITIAL_PROCESS_ENV
    }

    for key in _RUNTIME_ENV_FILE_KEYS - managed_keys:
        os.environ.pop(key, None)

    for key in managed_keys:
        os.environ[key] = latest_values[key]

    _RUNTIME_ENV_FILE_KEYS = managed_keys


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='A股自选股智能分析系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python main.py                    # 正常运行
  python main.py --debug            # 调试模式
  python main.py --dry-run          # 仅获取数据，不进行 AI 分析
  python main.py --stocks 600519,000001  # 指定分析特定股票
  python main.py --no-notify        # 不发送推送通知
  python main.py --check-notify     # 检查通知配置，不发送通知
  python main.py --single-notify    # 启用单股推送模式（每分析完一只立即推送）
  python main.py --schedule         # 启用定时任务模式
  python main.py --market-review    # 仅运行大盘复盘
  python main.py --sync-fundamentals            # 同步全A股基本面数据
  python main.py --sync-fundamentals --stocks 600519  # 仅同步指定股票
  python main.py --sync-fundamentals --sync-valuation  # 同步基本面+估值快照
  python main.py --import-theme-pack                   # 导入冷启动示例主题包（可选）
  python main.py --sync-exposure-graph               # 图谱同步：补全别名与自选股节点
  python main.py --extract-exposure-edges            # 从公告抽取暴露边写入图谱
  python main.py --run-exposure-ingest --force-exposure-ingest
  python main.py --run-event-delta --force-event-delta   # 处理 pending 事件并推送

环境变量（暴露图谱 / 事件增量）:
  EXPOSURE_GRAPH_ENABLED=true
  EXPOSURE_EVENT_WORKER_ENABLED=true
  EVENT_DELTA_ANALYSIS_ENABLED=true
  EVENT_PUSH_SCOPE=watchlist
  EVENT_PUSH_COOLDOWN_MINUTES=45

环境变量（慢变数据本地缓存 / 闲时同步）:
  FUNDAMENTAL_LOCAL_PREFER_ENABLED=true     # 分析时优先读本地 SQLite（默认 true）
  FUNDAMENTAL_LOCAL_MAX_AGE_DAYS=180        # 本地财务数据有效期（天）
  FUNDAMENTAL_SYNC_ENABLED=true             # 启用闲时基本面自动同步（清单+财务）
  FUNDAMENTAL_SYNC_TIME=02:00               # 每日同步执行时间
  FUNDAMENTAL_SYNC_RUN_IMMEDIATELY=false    # 启动时是否立即同步一次
  FUNDAMENTAL_SYNC_INCLUDE_INDUSTRY=false   # 是否在每日同步中附带行业（较慢，建议用每周任务）
  FUNDAMENTAL_SYNC_INCLUDE_VALUATION=false  # 同步估值快照（日变数据）
  FUNDAMENTAL_SYNC_INDUSTRY_ENABLED=true    # 启用每周行业补全
  FUNDAMENTAL_SYNC_INDUSTRY_TIME=03:00      # 每周行业补全执行时间
  FUNDAMENTAL_SYNC_INDUSTRY_WEEKDAY=6       # 每周执行日（0=周一 .. 6=周日）
  FUNDAMENTAL_SYNC_INDUSTRY_RUN_IMMEDIATELY=false  # 启动时是否立即补全行业
        '''
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='启用调试模式，输出详细日志'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='仅获取数据，不进行 AI 分析'
    )

    parser.add_argument(
        '--stocks',
        type=str,
        help='指定要分析的股票代码，逗号分隔（覆盖配置文件）'
    )

    parser.add_argument(
        '--no-notify',
        action='store_true',
        help='不发送推送通知'
    )

    parser.add_argument(
        '--check-notify',
        action='store_true',
        help='只读检查通知渠道配置，不发送通知'
    )

    parser.add_argument(
        '--single-notify',
        action='store_true',
        help='启用单股推送模式：每分析完一只股票立即推送，而不是汇总推送'
    )

    parser.add_argument(
        '--workers',
        type=int,
        default=None,
        help='并发线程数（默认使用配置值）'
    )

    parser.add_argument(
        '--schedule',
        action='store_true',
        help='启用定时任务模式，每日定时执行'
    )

    parser.add_argument(
        '--no-run-immediately',
        action='store_true',
        help='定时任务启动时不立即执行一次'
    )

    parser.add_argument(
        '--market-review',
        action='store_true',
        help='仅运行大盘复盘分析'
    )

    parser.add_argument(
        '--no-market-review',
        action='store_true',
        help='跳过大盘复盘分析'
    )

    parser.add_argument(
        '--force-run',
        action='store_true',
        help='跳过交易日检查，强制执行全量分析（Issue #373）'
    )

    parser.add_argument(
        '--webui',
        action='store_true',
        help='启动 Web 管理界面'
    )

    parser.add_argument(
        '--webui-only',
        action='store_true',
        help='仅启动 Web 服务，不执行自动分析'
    )

    parser.add_argument(
        '--serve',
        action='store_true',
        help='启动 FastAPI 后端服务（同时执行分析任务）'
    )

    parser.add_argument(
        '--serve-only',
        action='store_true',
        help='仅启动 FastAPI 后端服务，不自动执行分析'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=8000,
        help='FastAPI 服务端口（默认 8000）'
    )

    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='FastAPI 服务监听地址（默认 0.0.0.0）'
    )

    parser.add_argument(
        '--no-context-snapshot',
        action='store_true',
        help='不保存分析上下文快照'
    )

    # === Backtest ===
    parser.add_argument(
        '--backtest',
        action='store_true',
        help='运行回测（对历史分析结果进行评估）'
    )

    parser.add_argument(
        '--backtest-code',
        type=str,
        default=None,
        help='仅回测指定股票代码'
    )

    parser.add_argument(
        '--backtest-days',
        type=int,
        default=None,
        help='回测评估窗口（交易日数，默认使用配置）'
    )

    parser.add_argument(
        '--backtest-force',
        action='store_true',
        help='强制回测（即使已有回测结果也重新计算）'
    )

    # === 基本面数据同步 ===
    parser.add_argument(
        '--sync-fundamentals',
        action='store_true',
        help='同步全A股基本面数据到本地数据库（股票清单+财务指标）'
    )

    parser.add_argument(
        '--sync-valuation',
        action='store_true',
        help='同步全A股估值快照（百度估值源，较慢，建议配合 --sync-fundamentals 使用）'
    )

    parser.add_argument(
        '--sync-industry',
        action='store_true',
        help='从 THS 补充行业分类（较慢，建议配合 --sync-fundamentals 使用）'
    )

    parser.add_argument(
        '--import-theme-pack',
        nargs='?',
        const='changxin_chain',
        metavar='PACK_OR_PATH',
        help='导入冷启动主题包 YAML（示例：changxin_chain；非日常业务配置）'
    )

    parser.add_argument(
        '--run-exposure-ingest',
        action='store_true',
        help='运行一次事件 ingest；若启用 EVENT_DELTA_ANALYSIS 则衔接增量推送'
    )

    parser.add_argument(
        '--force-exposure-ingest',
        action='store_true',
        help='配合 --run-exposure-ingest，非交易时段也执行'
    )

    parser.add_argument(
        '--run-event-delta',
        action='store_true',
        help='处理 pending 的 event_signal，执行增量分析与条件推送'
    )

    parser.add_argument(
        '--force-event-delta',
        action='store_true',
        help='配合 --run-event-delta，忽略交易时段门控'
    )

    parser.add_argument(
        '--sync-exposure-graph',
        action='store_true',
        help='从暴露边补全实体别名，并为自选股同步图谱节点（不拉新闻）'
    )

    parser.add_argument(
        '--extract-exposure-edges',
        action='store_true',
        help='从自选股相关公告抽取参股/投资/合作等暴露边并写入图谱'
    )

    return parser.parse_args()


def _compute_trading_day_filter(
    config: Config,
    args: argparse.Namespace,
    stock_codes: List[str],
) -> Tuple[List[str], Optional[str], bool]:
    """
    Compute filtered stock list and effective market review region (Issue #373).

    Returns:
        (filtered_codes, effective_region, should_skip_all)
        - effective_region None = use config default (check disabled)
        - effective_region '' = all relevant markets closed, skip market review
        - should_skip_all: skip entire run when no stocks and no market review to run
    """
    force_run = getattr(args, 'force_run', False)
    if force_run or not getattr(config, 'trading_day_check_enabled', True):
        return (stock_codes, None, False)

    from src.core.trading_calendar import (
        get_market_for_stock,
        get_open_markets_today,
        compute_effective_region,
    )

    open_markets = get_open_markets_today()
    filtered_codes = []
    for code in stock_codes:
        mkt = get_market_for_stock(code)
        if mkt in open_markets or mkt is None:
            filtered_codes.append(code)

    if config.market_review_enabled and not getattr(args, 'no_market_review', False):
        effective_region = compute_effective_region(
            getattr(config, 'market_review_region', 'cn') or 'cn', open_markets
        )
    else:
        effective_region = None

    should_skip_all = (not filtered_codes) and (effective_region or '') == ''
    return (filtered_codes, effective_region, should_skip_all)


def _run_market_review_with_shared_lock(
    config: Config,
    run_market_review_func: Callable[..., Optional[str]],
    **kwargs: Any,
) -> Optional[str]:
    from src.core.market_review_lock import (
        release_market_review_lock,
        try_acquire_market_review_lock,
    )

    lock_token = try_acquire_market_review_lock(config)
    if lock_token is None:
        logger.warning("大盘复盘正在执行中，跳过本次大盘复盘")
        return None

    try:
        return run_market_review_func(**kwargs)
    finally:
        release_market_review_lock(lock_token)


def _refresh_stock_index_cache_for_analysis(config: Config) -> None:
    """Best-effort stock-index refresh for CLI/scheduled analysis paths."""
    try:
        from src.services.stock_index_remote_service import (
            refresh_remote_stock_index_cache,
            settings_from_config,
        )

        result = refresh_remote_stock_index_cache(settings_from_config(config))
        if result.refreshed:
            logger.info("[stock-index] 分析前已刷新股票索引缓存: %s", result.cache_path)
        elif result.error:
            logger.debug("[stock-index] 分析前刷新未完成，继续使用本地索引: %s", result.error)
    except Exception as exc:  # noqa: BLE001 - stock index freshness must not block analysis.
        logger.warning("[stock-index] 分析前刷新股票索引失败，继续执行分析: %s", exc)


def run_full_analysis(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None
):
    """
    执行完整的分析流程（个股 + 大盘复盘）

    这是定时任务调用的主函数
    """
    # Import pipeline modules outside the broad try/except so that import-time
    # failures propagate to the caller instead of being silently swallowed.
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline

    try:
        _refresh_stock_index_cache_for_analysis(config)

        # Issue #529: Hot-reload STOCK_LIST from .env on each scheduled run
        if stock_codes is None:
            config.refresh_stock_list()

        # Issue #373: Trading day filter (per-stock, per-market)
        effective_codes = stock_codes if stock_codes is not None else config.stock_list
        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(
            config, args, effective_codes
        )
        if should_skip:
            logger.info(
                "今日所有相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。"
            )
            return
        if set(filtered_codes) != set(effective_codes):
            skipped = set(effective_codes) - set(filtered_codes)
            logger.info("今日休市股票已跳过: %s", skipped)
        stock_codes = filtered_codes

        # 命令行参数 --single-notify 覆盖配置（#55）
        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        # Issue #190: 个股与大盘复盘合并推送
        merge_notification = (
            getattr(config, 'merge_email_notification', False)
            and config.market_review_enabled
            and not getattr(args, 'no_market_review', False)
            and not config.single_stock_notify
        )

        # 创建调度器
        save_context_snapshot = None
        if getattr(args, 'no_context_snapshot', False):
            save_context_snapshot = False
        query_id = uuid.uuid4().hex
        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=args.workers,
            query_id=query_id,
            query_source="cli",
            save_context_snapshot=save_context_snapshot
        )

        # 1. 运行个股分析
        results = pipeline.run(
            stock_codes=stock_codes,
            dry_run=args.dry_run,
            send_notification=not args.no_notify,
            merge_notification=merge_notification
        )

        # Issue #128: 分析间隔 - 在个股分析和大盘分析之间添加延迟
        analysis_delay = getattr(config, 'analysis_delay', 0)
        if (
            analysis_delay > 0
            and config.market_review_enabled
            and not args.no_market_review
            and effective_region != ''
        ):
            logger.info(f"等待 {analysis_delay} 秒后执行大盘复盘（避免API限流）...")
            time.sleep(analysis_delay)

        # 2. 运行大盘复盘（如果启用且不是仅个股模式）
        market_report = ""
        if (
            config.market_review_enabled
            and not args.no_market_review
            and effective_region != ''
        ):
            schedule_mode = bool(
                getattr(args, 'schedule', False)
                or getattr(config, 'schedule_enabled', False)
            )
            review_trigger_source = "schedule" if schedule_mode else "cli"
            review_result = _run_market_review_with_shared_lock(
                config,
                run_market_review,
                notifier=pipeline.notifier,
                analyzer=pipeline.analyzer,
                search_service=pipeline.search_service,
                send_notification=not args.no_notify,
                merge_notification=merge_notification,
                override_region=effective_region,
                trigger_source=review_trigger_source,
            )
            # 如果有结果，赋值给 market_report 用于后续飞书文档生成
            if review_result:
                market_report = review_result

        # Issue #190: 合并推送（个股+大盘复盘）
        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report:
                parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            if results:
                dashboard_content = pipeline.notifier.generate_aggregate_report(
                    results,
                    getattr(config, 'report_type', 'simple'),
                )
                parts.append(f"# 🚀 个股决策仪表盘\n\n{dashboard_content}")
            if parts:
                combined_content = "\n\n---\n\n".join(parts)
                if pipeline.notifier.is_available():
                    if pipeline.notifier.send(combined_content, email_send_to_all=True, route_type="report"):
                        logger.info("已合并推送（个股+大盘复盘）")
                    else:
                        logger.warning("合并推送失败")

        # 输出摘要
        if results:
            logger.info("\n===== 分析结果摘要 =====")
            for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
                emoji = r.get_emoji()
                logger.info(
                    f"{emoji} {r.name}({r.code}): {r.operation_advice} | "
                    f"评分 {r.sentiment_score} | {r.trend_prediction}"
                )

        logger.info("\n任务执行完成")

        # === 新增：生成飞书云文档 ===
        try:
            from src.feishu_doc import FeishuDocManager

            feishu_doc = FeishuDocManager()
            if feishu_doc.is_configured() and (results or market_report):
                logger.info("正在创建飞书云文档...")

                # 1. 准备标题 "01-01 13:01大盘复盘"
                tz_cn = timezone(timedelta(hours=8))
                now = datetime.now(tz_cn)
                doc_title = f"{now.strftime('%Y-%m-%d %H:%M')} 大盘复盘"

                # 2. 准备内容 (拼接个股分析和大盘复盘)
                full_content = ""

                # 添加大盘复盘内容（如果有）
                if market_report:
                    full_content += f"# 📈 大盘复盘\n\n{market_report}\n\n---\n\n"

                # 添加个股决策仪表盘（使用 NotificationService 生成，按 report_type 分支）
                if results:
                    dashboard_content = pipeline.notifier.generate_aggregate_report(
                        results,
                        getattr(config, 'report_type', 'simple'),
                    )
                    full_content += f"# 🚀 个股决策仪表盘\n\n{dashboard_content}"

                # 3. 创建文档
                doc_url = feishu_doc.create_daily_doc(doc_title, full_content)
                if doc_url:
                    logger.info(f"飞书云文档创建成功: {doc_url}")
                    # 可选：将文档链接也推送到群里
                    if not args.no_notify:
                        pipeline.notifier.send(
                            f"[{now.strftime('%Y-%m-%d %H:%M')}] 复盘文档创建成功: {doc_url}",
                            route_type="report",
                        )

        except Exception as e:
            logger.error(f"飞书文档生成失败: {e}")

        # === Auto backtest ===
        try:
            if getattr(config, 'backtest_enabled', False):
                from src.services.backtest_service import BacktestService

                logger.info("开始自动回测...")
                service = BacktestService()
                stats = service.run_backtest(
                    force=False,
                    eval_window_days=getattr(config, 'backtest_eval_window_days', 10),
                    min_age_days=getattr(config, 'backtest_min_age_days', 14),
                    limit=200,
                )
                logger.info(
                    f"自动回测完成: processed={stats.get('processed')} saved={stats.get('saved')} "
                    f"completed={stats.get('completed')} insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
                )
        except Exception as e:
            logger.warning(f"自动回测失败（已忽略）: {e}")

    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")


def start_api_server(host: str, port: int, config: Config) -> None:
    """
    在后台线程启动 FastAPI 服务

    Args:
        host: 监听地址
        port: 监听端口
        config: 配置对象
    """
    import socket
    import threading
    import uvicorn

    probe = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(f"FastAPI port is not available: {host}:{port}") from exc
    finally:
        probe.close()

    def run_server():
        level_name = (config.log_level or "INFO").lower()
        uvicorn.run(
            "api.app:app",
            host=host,
            port=port,
            log_level=level_name,
            log_config=None,
        )

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    logger.info(f"FastAPI 服务已启动: http://{host}:{port}")


def _is_truthy_env(var_name: str, default: str = "true") -> bool:
    """Parse common truthy / falsy environment values."""
    value = os.getenv(var_name, default).strip().lower()
    return value not in {"0", "false", "no", "off"}


def start_bot_stream_clients(config: Config) -> None:
    """Start bot stream clients when enabled in config."""
    # 启动钉钉 Stream 客户端
    if config.dingtalk_stream_enabled:
        try:
            from bot.platforms import start_dingtalk_stream_background, DINGTALK_STREAM_AVAILABLE
            if DINGTALK_STREAM_AVAILABLE:
                if start_dingtalk_stream_background():
                    logger.info("[Main] Dingtalk Stream client started in background.")
                else:
                    logger.warning("[Main] Dingtalk Stream client failed to start.")
            else:
                logger.warning("[Main] Dingtalk Stream enabled but SDK is missing.")
                logger.warning("[Main] Run: pip install dingtalk-stream")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Dingtalk Stream client: {exc}")

    # 启动飞书 Stream 客户端
    if getattr(config, 'feishu_stream_enabled', False):
        try:
            from bot.platforms import start_feishu_stream_background, FEISHU_SDK_AVAILABLE
            if FEISHU_SDK_AVAILABLE:
                if start_feishu_stream_background():
                    logger.info("[Main] Feishu Stream client started in background.")
                else:
                    logger.warning("[Main] Feishu Stream client failed to start.")
            else:
                logger.warning("[Main] Feishu Stream enabled but SDK is missing.")
                logger.warning("[Main] Run: pip install lark-oapi")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Feishu Stream client: {exc}")


def _resolve_scheduled_stock_codes(stock_codes: Optional[List[str]]) -> Optional[List[str]]:
    """Scheduled runs should always read the latest persisted watchlist."""
    if stock_codes is not None:
        logger.warning(
            "定时模式下检测到 --stocks 参数；计划执行将忽略启动时股票快照，并在每次运行前重新读取最新的 STOCK_LIST。"
        )
    return None


def _reload_runtime_config() -> Config:
    """Reload config from the latest persisted `.env` values for scheduled runs."""
    _reload_env_file_values_preserving_overrides()
    Config.reset_instance()
    return get_config()


def _build_schedule_time_provider(default_schedule_time: str):
    """Read the latest schedule time directly from the active config file.

    Fallback order:
    1. Process-level env override (set before launch) → honour it.
    2. Persisted config file value (written by WebUI) → use it.
    3. Documented system default ``"18:00"`` → always fall back here so
       that clearing SCHEDULE_TIME in WebUI correctly resets the schedule.
    """
    from src.core.config_manager import ConfigManager

    _SYSTEM_DEFAULT_SCHEDULE_TIME = "18:00"
    manager = ConfigManager()

    def _provider() -> str:
        if "SCHEDULE_TIME" in _INITIAL_PROCESS_ENV:
            return os.getenv("SCHEDULE_TIME", default_schedule_time)

        config_map = manager.read_config_map()
        schedule_time = (config_map.get("SCHEDULE_TIME", "") or "").strip()
        if schedule_time:
            return schedule_time
        return _SYSTEM_DEFAULT_SCHEDULE_TIME

    return _provider


def _build_fundamental_sync_time_provider(default_sync_time: str):
    """Read the latest fundamental sync time from the active config file."""
    from src.core.config_manager import ConfigManager

    _SYSTEM_DEFAULT_SYNC_TIME = "02:00"
    manager = ConfigManager()

    def _provider() -> str:
        if "FUNDAMENTAL_SYNC_TIME" in _INITIAL_PROCESS_ENV:
            return os.getenv("FUNDAMENTAL_SYNC_TIME", default_sync_time)

        config_map = manager.read_config_map()
        sync_time = (config_map.get("FUNDAMENTAL_SYNC_TIME", "") or "").strip()
        if sync_time:
            return sync_time
        return _SYSTEM_DEFAULT_SYNC_TIME

    return _provider


def _build_fundamental_industry_time_provider(default_sync_time: str):
    """Read the latest industry sync time from the active config file."""
    from src.core.config_manager import ConfigManager

    _SYSTEM_DEFAULT_SYNC_TIME = "03:00"
    manager = ConfigManager()

    def _provider() -> str:
        if "FUNDAMENTAL_SYNC_INDUSTRY_TIME" in _INITIAL_PROCESS_ENV:
            return os.getenv("FUNDAMENTAL_SYNC_INDUSTRY_TIME", default_sync_time)

        config_map = manager.read_config_map()
        sync_time = (config_map.get("FUNDAMENTAL_SYNC_INDUSTRY_TIME", "") or "").strip()
        if sync_time:
            return sync_time
        return _SYSTEM_DEFAULT_SYNC_TIME

    return _provider


def _build_fundamental_sync_daily_task(config: Config) -> Dict[str, Any]:
    """Build the scheduled off-peak fundamental sync daily task definition."""
    def _task() -> None:
        from src.services.fundamental_sync_task import run_scheduled_fundamental_sync

        run_scheduled_fundamental_sync(_reload_runtime_config())

    return {
        "name": "fundamental_sync",
        "task": _task,
        "schedule_time": config.fundamental_sync_time,
        "run_immediately": config.fundamental_sync_run_immediately,
        "schedule_time_provider": _build_fundamental_sync_time_provider(
            config.fundamental_sync_time
        ),
    }


def _build_fundamental_industry_weekly_task(config: Config) -> Dict[str, Any]:
    """Build the scheduled weekly industry enrichment task definition."""

    def _task() -> None:
        from src.services.fundamental_sync_task import run_scheduled_industry_sync

        run_scheduled_industry_sync(_reload_runtime_config())

    return {
        "name": "fundamental_industry_sync",
        "task": _task,
        "schedule_time": config.fundamental_sync_industry_time,
        "weekday": config.fundamental_sync_industry_weekday,
        "run_immediately": config.fundamental_sync_industry_run_immediately,
        "schedule_time_provider": _build_fundamental_industry_time_provider(
            config.fundamental_sync_industry_time
        ),
    }


def _build_exposure_event_background_task(config_provider: Callable[[], Config]) -> Optional[Dict[str, Any]]:
    """Register ExposureEventWorker when enabled in config."""
    config = config_provider()
    if not getattr(config, "exposure_event_worker_enabled", False):
        return None
    if not (
        getattr(config, "theme_news_ingest_enabled", False)
        or getattr(config, "announcement_monitor_enabled", False)
    ):
        return None

    from src.services.exposure_event_worker import ExposureEventWorker

    worker = ExposureEventWorker(config_provider=config_provider)
    interval_minutes = max(1, getattr(config, "theme_news_interval_minutes", 15))

    def exposure_event_task() -> None:
        stats = worker.run_once()
        if stats.get("inserted"):
            logger.info(
                "[ExposureEventWorker] 本轮写入 %d 条 event_signal",
                stats.get("inserted", 0),
            )

    return {
        "task": exposure_event_task,
        "interval_seconds": interval_minutes * 60,
        "run_immediately": False,
        "name": "exposure_event_worker",
    }


def _collect_background_tasks(config_provider: Callable[[], Config]) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    config = config_provider()

    if getattr(config, "agent_event_monitor_enabled", False):
        from src.services.alert_worker import AlertWorker

        interval_minutes = max(1, getattr(config, "agent_event_monitor_interval_minutes", 5))
        alert_worker = AlertWorker(config_provider=config_provider)

        def event_monitor_task() -> None:
            stats = alert_worker.run_once()
            triggered_count = stats.get("triggered", 0)
            if triggered_count:
                logger.info("[EventMonitor] 本轮触发 %d 条提醒", triggered_count)

        tasks.append({
            "task": event_monitor_task,
            "interval_seconds": interval_minutes * 60,
            "run_immediately": True,
            "name": "agent_event_monitor",
        })

    exposure_task = _build_exposure_event_background_task(config_provider)
    if exposure_task is not None:
        tasks.append(exposure_task)

    return tasks


def _build_exposure_extraction_daily_task(config: Config) -> Dict[str, Any]:
    """Build daily exposure edge extraction from announcements."""

    def _task() -> None:
        from src.services.exposure_extraction_task import run_scheduled_exposure_extraction

        run_scheduled_exposure_extraction(_reload_runtime_config())

    return {
        "name": "exposure_extraction",
        "task": _task,
        "schedule_time": config.fundamental_sync_time,
        "run_immediately": False,
        "schedule_time_provider": _build_fundamental_sync_time_provider(
            config.fundamental_sync_time
        ),
    }


def _collect_extra_daily_tasks(config: Config) -> List[Dict[str, Any]]:
    """Collect optional daily tasks that should run alongside analysis scheduling."""
    tasks: List[Dict[str, Any]] = []
    if config.fundamental_sync_enabled:
        tasks.append(_build_fundamental_sync_daily_task(config))
    if getattr(config, "exposure_extraction_enabled", False):
        tasks.append(_build_exposure_extraction_daily_task(config))
    return tasks


def _collect_extra_weekly_tasks(config: Config) -> List[Dict[str, Any]]:
    """Collect optional weekly tasks that should run alongside scheduling."""
    tasks: List[Dict[str, Any]] = []
    if config.fundamental_sync_industry_enabled:
        tasks.append(_build_fundamental_industry_weekly_task(config))
    return tasks


def main() -> int:
    """
    主入口函数

    Returns:
        退出码（0 表示成功）
    """
    # 解析命令行参数
    args = parse_arguments()

    # 在配置加载前先初始化 bootstrap 日志，确保早期失败也能落盘
    try:
        _setup_bootstrap_logging(debug=args.debug)
    except Exception as exc:
        logging.basicConfig(
            level=logging.DEBUG if getattr(args, "debug", False) else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stderr,
        )
        logger.warning("Bootstrap 日志初始化失败，已回退到 stderr: %s", exc)

    # 加载配置（在 bootstrap logging 之后执行，确保异常有日志）
    try:
        config = get_config()
    except Exception as exc:
        logger.exception("加载配置失败: %s", exc)
        return 1

    # 配置日志（输出到控制台和文件）
    try:
        _setup_runtime_logging(config.log_dir, debug=args.debug)
    except Exception as exc:
        logger.exception("切换到配置日志目录失败: %s", exc)
        return 1

    logger.info("=" * 60)
    logger.info("A股自选股智能分析系统 启动")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # 验证配置
    warnings = config.validate()
    for warning in warnings:
        logger.warning(warning)

    if getattr(args, "check_notify", False):
        from src.services.notification_diagnostics import (
            format_notification_diagnostics,
            run_notification_diagnostics,
        )

        result = run_notification_diagnostics(config)
        print(format_notification_diagnostics(result))
        return 0 if result.ok else 1

    # 解析股票列表（统一为大写 Issue #355）
    stock_codes = None
    if args.stocks:
        stock_codes = [canonical_stock_code(c) for c in args.stocks.split(',') if (c or "").strip()]
        logger.info(f"使用命令行指定的股票列表: {stock_codes}")

    # === 处理 --webui / --webui-only 参数，映射到 --serve / --serve-only ===
    if args.webui:
        args.serve = True
    if args.webui_only:
        args.serve_only = True

    # 兼容旧版 WEBUI_ENABLED 环境变量
    if config.webui_enabled and not (args.serve or args.serve_only):
        args.serve = True

    # === 启动 Web 服务 (如果启用) ===
    start_serve = (args.serve or args.serve_only) and os.getenv("GITHUB_ACTIONS") != "true"

    # 兼容旧版 WEBUI_HOST/WEBUI_PORT：如果用户未通过 --host/--port 指定，则使用旧变量
    if start_serve:
        if args.host == '0.0.0.0' and os.getenv('WEBUI_HOST'):
            args.host = os.getenv('WEBUI_HOST')
        if args.port == 8000 and os.getenv('WEBUI_PORT'):
            args.port = int(os.getenv('WEBUI_PORT'))
        _warn_if_public_webui_without_auth(args.host)

    bot_clients_started = False
    if start_serve:
        if not prepare_webui_frontend_assets():
            logger.warning("前端静态资源未就绪，继续启动 FastAPI 服务（Web 页面可能不可用）")
        try:
            start_api_server(host=args.host, port=args.port, config=config)
            bot_clients_started = True
        except Exception as e:
            logger.error(f"启动 FastAPI 服务失败: {e}")
            if args.serve_only:
                return 1
            start_serve = False

    if bot_clients_started:
        start_bot_stream_clients(config)

    # === 仅 Web 服务模式：不自动执行分析 ===
    if args.serve_only:
        logger.info("模式: 仅 Web 服务")
        logger.info(f"Web 服务运行中: http://{args.host}:{args.port}")
        logger.info("通过 /api/v1/analysis/analyze 接口触发分析")
        logger.info(f"API 文档: http://{args.host}:{args.port}/docs")
        logger.info("按 Ctrl+C 退出...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n用户中断，程序退出")
        return 0

    try:
        # 模式0: 回测
        if getattr(args, 'backtest', False):
            logger.info("模式: 回测")
            from src.services.backtest_service import BacktestService

            service = BacktestService()
            stats = service.run_backtest(
                code=getattr(args, 'backtest_code', None),
                force=getattr(args, 'backtest_force', False),
                eval_window_days=getattr(args, 'backtest_days', None),
            )
            logger.info(
                f"回测完成: processed={stats.get('processed')} saved={stats.get('saved')} "
                f"completed={stats.get('completed')} insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
            )
            return 0

        # 模式0.5: 基本面数据同步
        if getattr(args, 'sync_fundamentals', False):
            logger.info("模式: 基本面数据同步")
            from src.services.fundamental_sync import FundamentalSyncService

            svc = FundamentalSyncService()
            sync_stocks = stock_codes  # None = 全量

            result = svc.full_sync(
                stocks=sync_stocks,
                include_financials=True,
                include_valuation=getattr(args, 'sync_valuation', False),
                include_industry_enrich=getattr(args, 'sync_industry', False),
            )
            logger.info(
                f"基本面同步完成: stock_list={result['stock_list_count']} "
                f"industry_enriched={result['industry_enriched']} "
                f"financials={result['financials']} "
                f"valuation={result['valuation_count']}"
            )
            return 0

        # 模式0.6: 主题包导入（暴露图谱 Phase 1）
        import_theme_pack_arg = getattr(args, 'import_theme_pack', None)
        if import_theme_pack_arg is not None:
            from src.services.theme_pack_importer import import_theme_pack

            target = import_theme_pack_arg
            if str(target).lower().endswith(('.yaml', '.yml')):
                stats = import_theme_pack(path=target)
            else:
                stats = import_theme_pack(pack_id=str(target))
            logger.info(
                "主题包导入完成: aliases=%s profiles=%s exposures=%s errors=%s",
                stats.get("entity_aliases"),
                stats.get("company_profiles"),
                stats.get("exposures"),
                stats.get("errors"),
            )
            return 0 if stats.get("errors", 0) == 0 else 1

        # 模式0.7: 单次事件 ingest（Phase 2a）
        if getattr(args, 'run_exposure_ingest', False):
            from src.services.exposure_event_worker import ExposureEventWorker

            worker = ExposureEventWorker(config_provider=_reload_runtime_config)
            stats = worker.run_once(
                force=getattr(args, 'force_exposure_ingest', False),
                ignore_enable_flags=True,
            )
            logger.info(
                "事件 ingest 完成: ingested=%s inserted=%s matched=%s session_skipped=%s "
                "delta_pushed=%s",
                stats.get("ingested"),
                stats.get("inserted"),
                stats.get("matched"),
                stats.get("session_skipped"),
                stats.get("delta_pushed", 0),
            )
            return 0

        # 模式0.72: 事件增量分析 + 推送（Phase 3）
        if getattr(args, 'run_event_delta', False):
            from src.services.event_delta_processor import EventDeltaProcessor
            from src.services.exposure_event_worker import ExposureEventWorker

            runtime_config = _reload_runtime_config()
            worker = ExposureEventWorker(config_provider=lambda: runtime_config)
            processor = EventDeltaProcessor(
                stock_name_resolver=worker.stock_name_provider,
            )
            stats = processor.process_pending(
                runtime_config,
                force=getattr(args, 'force_event_delta', False),
            )
            logger.info(
                "事件增量处理完成: signals=%s analyzed=%s pushed=%s skipped=%s",
                stats.get("signals"),
                stats.get("analyzed"),
                stats.get("pushed"),
                stats.get("skipped"),
            )
            return 0

        # 模式0.75: 图谱同步（补全实体别名 / 自选股节点）
        if getattr(args, 'sync_exposure_graph', False):
            from src.services.exposure_event_worker import ExposureEventWorker
            from src.services.exposure_graph_sync import ExposureGraphSyncService

            runtime_config = _reload_runtime_config()
            sync = ExposureGraphSyncService()
            codes = stock_codes if stock_codes is not None else list(runtime_config.stock_list or [])
            worker = ExposureEventWorker(config_provider=lambda: runtime_config)
            created = sync.ensure_entity_aliases_from_exposures()
            watchlist = sync.sync_watchlist_company_entities(
                codes,
                name_resolver=worker.stock_name_provider,
            )
            queries = sync.build_ingest_queries_from_graph(
                max_queries=runtime_config.exposure_ingest_max_queries,
                watchlist_codes=codes,
                name_resolver=worker.stock_name_provider,
            )
            logger.info(
                "图谱同步完成: entity_stubs=%s watchlist_nodes=%s ingest_queries=%s",
                created,
                watchlist,
                len(queries),
            )
            return 0

        # 模式0.8: 公告抽取暴露边（Phase 2b）
        if getattr(args, 'extract_exposure_edges', False):
            from src.services.exposure_event_worker import ExposureEventWorker
            from src.services.exposure_edge_extractor import ExposureEdgeExtractor

            runtime_config = _reload_runtime_config()
            worker = ExposureEventWorker(config_provider=lambda: runtime_config)
            extractor = ExposureEdgeExtractor(
                stock_name_resolver=worker.stock_name_provider,
            )
            if stock_codes is not None:
                stats = extractor.extract_for_codes(
                    stock_codes,
                    max_results_per_code=runtime_config.exposure_extraction_max_per_code,
                )
            else:
                stats = extractor.extract_from_config(runtime_config)
            logger.info(
                "公告抽取暴露边完成: codes=%s edges_saved=%s parsed=%s skipped=%s",
                stats.get("codes"),
                stats.get("edges_saved"),
                stats.get("parsed"),
                stats.get("skipped"),
            )
            return 0

        # 模式1: 仅大盘复盘
        if args.market_review:
            from src.core.market_review import run_market_review
            from src.core.market_review_runtime import build_market_review_runtime

            # Issue #373: Trading day check for market-review-only mode.
            # Do NOT use _compute_trading_day_filter here: that helper checks
            # config.market_review_enabled, which would wrongly block an
            # explicit --market-review invocation when the flag is disabled.
            effective_region = None
            if not getattr(args, 'force_run', False) and getattr(config, 'trading_day_check_enabled', True):
                from src.core.trading_calendar import get_open_markets_today, compute_effective_region as _compute_region
                open_markets = get_open_markets_today()
                effective_region = _compute_region(
                    getattr(config, 'market_review_region', 'cn') or 'cn', open_markets
                )
                if effective_region == '':
                    logger.info("今日大盘复盘相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。")
                    return 0

            logger.info("模式: 仅大盘复盘")
            notifier, analyzer, search_service = build_market_review_runtime(config)

            _run_market_review_with_shared_lock(
                config,
                run_market_review,
                notifier=notifier,
                analyzer=analyzer,
                search_service=search_service,
                send_notification=not args.no_notify,
                override_region=effective_region,
                trigger_source="cli",
            )
            return 0

        # 模式2: 定时任务模式
        if args.schedule or config.schedule_enabled:
            logger.info("模式: 定时任务")
            logger.info(f"每日执行时间: {config.schedule_time}")

            # Determine whether to run immediately:
            # Command line arg --no-run-immediately overrides config if present.
            # Otherwise use config (defaults to True).
            should_run_immediately = config.schedule_run_immediately
            if getattr(args, 'no_run_immediately', False):
                should_run_immediately = False

            logger.info(f"启动时立即执行: {should_run_immediately}")

            from src.scheduler import run_with_schedule
            scheduled_stock_codes = _resolve_scheduled_stock_codes(stock_codes)
            schedule_time_provider = _build_schedule_time_provider(config.schedule_time)

            def scheduled_task():
                runtime_config = _reload_runtime_config()
                run_full_analysis(runtime_config, args, scheduled_stock_codes)

            background_tasks = _collect_background_tasks(_reload_runtime_config)

            run_with_schedule(
                task=scheduled_task,
                schedule_time=config.schedule_time,
                run_immediately=should_run_immediately,
                background_tasks=background_tasks,
                schedule_time_provider=schedule_time_provider,
                extra_daily_tasks=_collect_extra_daily_tasks(config),
                extra_weekly_tasks=_collect_extra_weekly_tasks(config),
            )
            return 0

        # 模式2.5: 仅基本面闲时同步调度（不跑分析）
        if config.fundamental_sync_enabled or config.fundamental_sync_industry_enabled:
            logger.info("模式: 基本面闲时同步调度")
            if config.fundamental_sync_enabled:
                logger.info("每日同步时间: %s", config.fundamental_sync_time)
            if config.fundamental_sync_industry_enabled:
                logger.info(
                    "每周行业补全: weekday=%s time=%s",
                    config.fundamental_sync_industry_weekday,
                    config.fundamental_sync_industry_time,
                )
            from src.scheduler import run_with_schedule

            run_with_schedule(
                task=None,
                enable_primary_daily_task=False,
                background_tasks=_collect_background_tasks(_reload_runtime_config),
                extra_daily_tasks=_collect_extra_daily_tasks(config),
                extra_weekly_tasks=_collect_extra_weekly_tasks(config),
            )
            return 0

        # 模式3: 正常单次运行
        if config.run_immediately:
            run_full_analysis(config, args, stock_codes)
        else:
            logger.info("配置为不立即运行分析 (RUN_IMMEDIATELY=false)")

        logger.info("\n程序执行完成")

        # 如果启用了服务且是非定时任务模式，保持程序运行
        keep_running = start_serve and not (args.schedule or config.schedule_enabled)
        if keep_running:
            logger.info("API 服务运行中 (按 Ctrl+C 退出)...")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        return 0

    except KeyboardInterrupt:
        logger.info("\n用户中断，程序退出")
        return 130

    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
