# -*- coding: utf-8 -*-
"""闲时任务：公告抽取暴露边。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.services.exposure_edge_extractor import ExposureEdgeExtractor
from src.services.exposure_event_worker import ExposureEventWorker

logger = logging.getLogger(__name__)


def run_scheduled_exposure_extraction(config: Optional[Any] = None) -> dict:
    if config is None:
        from src.config import get_config

        config = get_config()
    if not getattr(config, "exposure_extraction_enabled", False):
        logger.info("[ExposureExtraction] disabled, skip")
        return {"skipped": 1}

    worker = ExposureEventWorker(config_provider=lambda: config)
    extractor = ExposureEdgeExtractor(
        stock_name_resolver=worker.stock_name_provider,
    )
    stats = extractor.extract_from_config(config)
    logger.info("[ExposureExtraction] finished: %s", stats)
    return stats
