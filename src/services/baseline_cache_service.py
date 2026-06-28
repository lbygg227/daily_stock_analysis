# -*- coding: utf-8 -*-
"""分析基线缓存服务（Phase 1）。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from src.repositories.exposure_repo import ExposureRepository
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


class BaselineCacheService:
    """在全量分析成功后写入 analysis_baseline_cache。"""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        exposure_repo: Optional[ExposureRepository] = None,
    ):
        self.db = db_manager or DatabaseManager.get_instance()
        self.exposure_repo = exposure_repo or ExposureRepository(self.db)

    def _latest_history_id(self, code: str, query_id: str) -> Optional[int]:
        records = self.db.get_analysis_history(code=code, query_id=query_id, limit=1)
        if not records:
            records = self.db.get_analysis_history(code=code, limit=1)
        if not records:
            return None
        return records[0].id

    def _build_exposure_digest(self, code: str) -> str:
        rows = self.exposure_repo.get_exposures_by_code(code)
        if not rows:
            return ""
        parts = []
        for row in rows[:5]:
            snippet = (row.summary or row.role or row.target_entity_id or "").strip()
            parts.append(f"{row.target_entity_id}({row.link_type}): {snippet}")
        return "；".join(parts)

    def _extract_key_levels(self, result: Any) -> Dict[str, Any]:
        levels: Dict[str, Any] = {}
        for attr in ("ideal_buy", "secondary_buy", "stop_loss", "take_profit"):
            value = getattr(result, attr, None)
            if value is None and hasattr(result, "dashboard") and isinstance(result.dashboard, dict):
                sniper = result.dashboard.get("sniper_points") or {}
                if isinstance(sniper, dict):
                    value = sniper.get(attr)
            if value is not None:
                levels[attr] = value
        return levels

    def upsert_from_analysis_result(
        self,
        result: Any,
        query_id: str,
        *,
        context_snapshot: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if result is None or not getattr(result, "success", False):
            return False
        code = getattr(result, "code", None)
        if not code:
            return False

        tech_summary = (
            getattr(result, "trend_prediction", None)
            or getattr(result, "technical_analysis", None)
            or ""
        )
        if isinstance(tech_summary, str) and len(tech_summary) > 240:
            tech_summary = tech_summary[:240] + "…"

        core_thesis = (
            getattr(result, "analysis_summary", None)
            or getattr(result, "buy_reason", None)
            or ""
        )
        risks = getattr(result, "risk_warning", None) or ""

        record = {
            "code": code,
            "baseline_history_id": self._latest_history_id(code, query_id),
            "operation_advice": getattr(result, "operation_advice", None),
            "core_thesis": core_thesis,
            "risks": risks,
            "key_levels": self._extract_key_levels(result),
            "price_at_analysis": getattr(result, "current_price", None),
            "tech_summary": tech_summary,
            "exposure_digest": self._build_exposure_digest(code),
        }
        return self.exposure_repo.upsert_baseline_cache(record)


def refresh_baseline_cache_after_history_save(
    db: DatabaseManager,
    result: Any,
    query_id: str,
    *,
    context_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    """Pipeline 钩子：保存 analysis_history 后刷新基线缓存。"""
    try:
        service = BaselineCacheService(db)
        if not service.upsert_from_analysis_result(
            result,
            query_id,
            context_snapshot=context_snapshot,
        ):
            logger.debug("baseline cache not updated for %s", getattr(result, "code", "?"))
    except Exception as exc:
        logger.warning(
            "refresh baseline cache failed for %s: %s",
            getattr(result, "code", "?"),
            exc,
        )


def format_baseline_for_api(row: Any) -> Dict[str, Any]:
    key_levels = None
    if getattr(row, "key_levels_json", None):
        try:
            key_levels = json.loads(row.key_levels_json)
        except json.JSONDecodeError:
            key_levels = None
    created_at = getattr(row, "created_at", None)
    return {
        "code": row.code,
        "baseline_history_id": row.baseline_history_id,
        "operation_advice": row.operation_advice,
        "core_thesis": row.core_thesis,
        "risks": row.risks,
        "key_levels": key_levels,
        "price_at_analysis": row.price_at_analysis,
        "tech_summary": row.tech_summary,
        "exposure_digest": row.exposure_digest,
        "created_at": created_at.isoformat() if created_at is not None else None,
    }
