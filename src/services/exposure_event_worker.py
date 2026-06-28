# -*- coding: utf-8 -*-
"""ExposureEventWorker — 消息面 ingest + 可选增量推送（Phase 2a/3）。"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from src.core.trading_calendar import build_market_phase_context
from src.repositories.event_signal_repo import EventSignalRepository
from src.repositories.exposure_repo import ExposureRepository
from src.services.exposure_event_ingest import ExposureEventIngestService

logger = logging.getLogger(__name__)


class ExposureEventWorker:
    """周期性主题新闻/公告 ingest；Phase 3 可衔接增量分析与推送。"""

    def __init__(
        self,
        *,
        config_provider: Optional[Callable[[], Any]] = None,
        ingest_service: Optional[ExposureEventIngestService] = None,
        signal_repo: Optional[EventSignalRepository] = None,
        stock_list_provider: Optional[Callable[[Any], List[str]]] = None,
        stock_name_provider: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.config_provider = config_provider or self._default_config_provider
        self.signal_repo = signal_repo or EventSignalRepository()
        self.ingest_service = ingest_service
        self.stock_list_provider = stock_list_provider or self._default_stock_list
        self.stock_name_provider = stock_name_provider or self._default_stock_name

    @staticmethod
    def _default_config_provider():
        from src.config import get_config

        return get_config()

    @staticmethod
    def _default_stock_list(config: Any) -> List[str]:
        raw = getattr(config, "stock_list", None) or []
        return [str(code).strip() for code in raw if str(code).strip()]

    def _default_stock_name(self, code: str) -> str:
        row = ExposureRepository().get_company_profile(code)
        if row and row.name:
            return row.name
        try:
            from src.repositories.fundamental_repo import FundamentalRepository

            listing = FundamentalRepository().get_stock_listing_by_code(code)
            if listing and listing.name:
                return listing.name
        except Exception:
            pass
        return ""

    def _build_ingest_service(self, config: Any) -> ExposureEventIngestService:
        if self.ingest_service is not None:
            return self.ingest_service
        return ExposureEventIngestService(
            watchlist_codes=self.stock_list_provider(config),
            stock_name_resolver=self.stock_name_provider,
        )

    def should_run_ingest(self, config: Any, *, force: bool = False) -> tuple[bool, Optional[str]]:
        if force:
            return True, None
        if getattr(config, "exposure_event_ingest_outside_session", False):
            return True, None
        ctx = build_market_phase_context("cn", requested_phase="auto")
        if ctx.is_market_open_now:
            return True, None
        return False, "non_trading_hours"

    def run_once(
        self,
        *,
        force: bool = False,
        ignore_enable_flags: bool = False,
    ) -> Dict[str, int]:
        stats = {
            "enabled": 0,
            "ingested": 0,
            "inserted": 0,
            "matched": 0,
            "skipped": 0,
            "session_skipped": 0,
            "delta_analyzed": 0,
            "delta_pushed": 0,
            "delta_skipped": 0,
        }
        try:
            config = self.config_provider()
        except Exception as exc:
            logger.warning("[ExposureEventWorker] config load failed: %s", exc)
            stats["skipped"] += 1
            return stats

        if not ignore_enable_flags and not getattr(
            config, "exposure_event_worker_enabled", False
        ):
            return stats

        ingest_enabled = (
            getattr(config, "theme_news_ingest_enabled", False)
            or getattr(config, "announcement_monitor_enabled", False)
        )
        if not ignore_enable_flags and not ingest_enabled:
            return stats

        stats["enabled"] = 1

        allowed, skip_reason = self.should_run_ingest(config, force=force)
        if not allowed:
            stats["session_skipped"] = 1
            logger.info("[ExposureEventWorker] skipped ingest: %s", skip_reason)
            return stats

        service = self._build_ingest_service(config)
        if ignore_enable_flags and not ingest_enabled:
            ingest_stats = service.ingest_graph_driven_news(config)
        else:
            ingest_stats = service.ingest_from_config(config)

        stats["ingested"] = ingest_stats.get("searched", 0)
        stats["inserted"] = ingest_stats.get("inserted", 0)
        stats["matched"] = ingest_stats.get("matched", 0)
        stats["skipped"] += ingest_stats.get("skipped", 0)

        if stats["inserted"]:
            logger.info(
                "[ExposureEventWorker] inserted %s event_signal(s)",
                stats["inserted"],
            )

        if getattr(config, "event_delta_analysis_enabled", False) and (
            stats["inserted"] or force
        ):
            from src.services.event_delta_processor import EventDeltaProcessor

            processor = EventDeltaProcessor(
                signal_repo=self.signal_repo,
                stock_name_resolver=self.stock_name_provider,
            )
            delta_stats = processor.process_pending(config, force=force)
            stats["delta_analyzed"] = delta_stats.get("analyzed", 0)
            stats["delta_pushed"] = delta_stats.get("pushed", 0)
            stats["delta_skipped"] = delta_stats.get("skipped", 0)
            if delta_stats.get("pushed"):
                logger.info(
                    "[ExposureEventWorker] event delta pushed %s",
                    delta_stats.get("pushed", 0),
                )

        return stats
