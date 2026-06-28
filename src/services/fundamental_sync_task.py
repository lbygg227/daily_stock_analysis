# -*- coding: utf-8 -*-
"""Scheduled off-peak fundamental data synchronization."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.config import Config, get_config
from src.services.fundamental_sync import FundamentalSyncService

logger = logging.getLogger(__name__)


def run_scheduled_fundamental_sync(config: Optional[Config] = None) -> Dict[str, Any]:
    """Run the configured idle-time fundamental sync pipeline."""
    runtime_config = config or get_config()
    svc = FundamentalSyncService()

    logger.info(
        "[基本面闲时同步] 开始执行 (industry=%s, valuation=%s)",
        runtime_config.fundamental_sync_include_industry,
        runtime_config.fundamental_sync_include_valuation,
    )
    result = svc.full_sync(
        include_financials=True,
        include_industry_enrich=runtime_config.fundamental_sync_include_industry,
        include_valuation=runtime_config.fundamental_sync_include_valuation,
    )

    if runtime_config.stock_index_remote_update_enabled:
        try:
            from src.services.stock_index_remote_service import (
                refresh_remote_stock_index_cache,
                settings_from_config,
            )

            index_result = refresh_remote_stock_index_cache(settings_from_config(runtime_config))
            if index_result.refreshed:
                logger.info(
                    "[基本面闲时同步] 股票名称索引已刷新: %s",
                    index_result.cache_path,
                )
            elif index_result.error:
                logger.debug(
                    "[基本面闲时同步] 股票名称索引刷新跳过: %s",
                    index_result.error,
                )
        except Exception as exc:  # noqa: BLE001 - index refresh must not block sync
            logger.warning("[基本面闲时同步] 股票名称索引刷新失败: %s", exc)

    logger.info("[基本面闲时同步] 完成: %s", result)
    return result


def run_scheduled_industry_sync(config: Optional[Config] = None) -> Dict[str, Any]:
    """Run the weekly industry enrichment pipeline for stocks missing industry."""
    runtime_config = config or get_config()
    svc = FundamentalSyncService()

    logger.info("[基本面行业补全] 开始执行 weekly industry sync")
    result = svc.enrich_industry_all()
    logger.info("[基本面行业补全] 完成: %s", result)
    return result
