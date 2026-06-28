# -*- coding: utf-8 -*-
"""
暴露图谱数据访问层（Phase 1）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.storage import (
    AnalysisBaselineCache,
    CompanyExposure,
    CompanyProfile,
    DatabaseManager,
    EntityAlias,
)

logger = logging.getLogger(__name__)

_VALID_LINK_TYPES = frozenset(
    {
        "supply_chain",
        "equity_investment",
        "subsidiary",
        "revenue_share",
        "concept",
        "substitute",
    }
)
_VALID_STRENGTHS = frozenset({"high", "medium", "low"})
_VALID_DIRECTIONS = frozenset({"positive", "negative", "neutral"})
_VALID_PRICING_DRIVERS = frozenset(
    {"core_business", "theme_overlay", "mixed"}
)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_code(code: str) -> str:
    raw = str(code or "").strip()
    if raw.isdigit():
        return raw.zfill(6)
    return raw


class ExposureRepository:
    """暴露图谱 CRUD 与反查。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ------------------------------------------------------------------
    # EntityAlias
    # ------------------------------------------------------------------

    def upsert_entity_alias(self, record: Dict[str, Any]) -> bool:
        entity_id = str(record.get("entity_id") or "").strip()
        if not entity_id:
            return False
        aliases = record.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        display_name = str(record.get("display_name") or entity_id).strip()
        entity_type = str(record.get("entity_type") or "theme").strip()
        now = _utc_now_naive()
        values = {
            "entity_id": entity_id,
            "display_name": display_name,
            "aliases_json": json.dumps(list(aliases), ensure_ascii=False),
            "entity_type": entity_type,
            "updated_at": now,
        }
        try:
            with self.db.get_session() as session:
                stmt = sqlite_insert(EntityAlias).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["entity_id"],
                    set_={
                        "display_name": display_name,
                        "aliases_json": values["aliases_json"],
                        "entity_type": entity_type,
                        "updated_at": now,
                    },
                )
                session.execute(stmt)
                session.commit()
            return True
        except Exception as exc:
            logger.error("upsert_entity_alias failed for %s: %s", entity_id, exc)
            return False

    def get_entity_alias(self, entity_id: str) -> Optional[EntityAlias]:
        key = str(entity_id or "").strip()
        if not key:
            return None
        with self.db.get_session() as session:
            return session.get(EntityAlias, key)

    def list_entity_aliases(self) -> List[EntityAlias]:
        with self.db.get_session() as session:
            return list(session.scalars(select(EntityAlias).order_by(EntityAlias.entity_id)))

    # ------------------------------------------------------------------
    # CompanyProfile
    # ------------------------------------------------------------------

    def upsert_company_profile(self, record: Dict[str, Any]) -> bool:
        code = _normalize_code(record.get("code", ""))
        if not code:
            return False
        now = _utc_now_naive()
        values = {
            "code": code,
            "name": record.get("name"),
            "surface_business": record.get("surface_business"),
            "pricing_notes": record.get("pricing_notes"),
            "industry_ths": record.get("industry_ths"),
            "updated_at": now,
        }
        try:
            with self.db.get_session() as session:
                existing = session.get(CompanyProfile, code)
                if existing:
                    for field, value in values.items():
                        if field == "code":
                            continue
                        if value is not None:
                            setattr(existing, field, value)
                else:
                    session.add(CompanyProfile(**values))
                session.commit()
            return True
        except Exception as exc:
            logger.error("upsert_company_profile failed for %s: %s", code, exc)
            return False

    def get_company_profile(self, code: str) -> Optional[CompanyProfile]:
        key = _normalize_code(code)
        with self.db.get_session() as session:
            return session.get(CompanyProfile, key)

    # ------------------------------------------------------------------
    # CompanyExposure
    # ------------------------------------------------------------------

    def upsert_company_exposure(self, record: Dict[str, Any]) -> bool:
        code = _normalize_code(record.get("code", ""))
        target_entity_id = str(record.get("target_entity_id") or "").strip()
        link_type = str(record.get("link_type") or "").strip()
        if not code or not target_entity_id or link_type not in _VALID_LINK_TYPES:
            logger.warning(
                "skip invalid exposure: code=%s target=%s link_type=%s",
                code,
                target_entity_id,
                link_type,
            )
            return False

        strength = str(record.get("strength") or "medium")
        if strength not in _VALID_STRENGTHS:
            strength = "medium"
        direction = str(record.get("direction") or "positive")
        if direction not in _VALID_DIRECTIONS:
            direction = "positive"
        pricing_driver = str(record.get("pricing_driver") or "core_business")
        if pricing_driver not in _VALID_PRICING_DRIVERS:
            pricing_driver = "core_business"

        now = _utc_now_naive()
        verified_at = record.get("verified_at")
        if isinstance(verified_at, str):
            verified_at = None

        values = {
            "code": code,
            "target_entity_id": target_entity_id,
            "link_type": link_type,
            "role": record.get("role"),
            "strength": strength,
            "exposure_pct": record.get("exposure_pct"),
            "direction": direction,
            "pricing_driver": pricing_driver,
            "summary": record.get("summary"),
            "source": str(record.get("source") or "manual"),
            "source_ref": record.get("source_ref"),
            "verified_at": verified_at,
            "ttl_days": int(record.get("ttl_days") or 90),
            "updated_at": now,
        }
        try:
            with self.db.get_session() as session:
                stmt = sqlite_insert(CompanyExposure).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["code", "target_entity_id", "link_type"],
                    set_={k: v for k, v in values.items() if k not in {"code", "target_entity_id", "link_type"}},
                )
                session.execute(stmt)
                session.commit()
            return True
        except Exception as exc:
            logger.error(
                "upsert_company_exposure failed for %s -> %s: %s",
                code,
                target_entity_id,
                exc,
            )
            return False

    def get_exposures_by_code(
        self,
        code: str,
        *,
        active_only: bool = True,
    ) -> List[CompanyExposure]:
        key = _normalize_code(code)
        with self.db.get_session() as session:
            rows = list(
                session.scalars(
                    select(CompanyExposure)
                    .where(CompanyExposure.code == key)
                    .order_by(CompanyExposure.strength, CompanyExposure.id)
                )
            )
        if active_only:
            rows = self._filter_active_exposures(rows)
        return rows

    def get_exposures_by_entity(
        self,
        entity_id: str,
        *,
        active_only: bool = True,
    ) -> List[CompanyExposure]:
        key = str(entity_id or "").strip()
        with self.db.get_session() as session:
            rows = list(
                session.scalars(
                    select(CompanyExposure)
                    .where(CompanyExposure.target_entity_id == key)
                    .order_by(CompanyExposure.strength, CompanyExposure.code)
                )
            )
        if active_only:
            rows = self._filter_active_exposures(rows)
        return rows

    def _filter_active_exposures(
        self,
        rows: List[CompanyExposure],
    ) -> List[CompanyExposure]:
        if not rows:
            return rows
        try:
            from src.repositories.exposure_feedback_repo import ExposureFeedbackRepository

            disabled = ExposureFeedbackRepository(self.db).list_disabled_exposure_ids()
        except Exception:
            disabled = set()
        if not disabled:
            return rows
        return [row for row in rows if row.id not in disabled]

    def list_exposures(
        self,
        *,
        code: Optional[str] = None,
        entity_id: Optional[str] = None,
        source: Optional[str] = None,
        include_disabled: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[CompanyExposure], int]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self.db.get_session() as session:
            query = select(CompanyExposure)
            if code:
                query = query.where(CompanyExposure.code == _normalize_code(code))
            if entity_id:
                query = query.where(
                    CompanyExposure.target_entity_id == str(entity_id).strip()
                )
            if source:
                query = query.where(CompanyExposure.source == str(source).strip())
            rows = list(session.scalars(query.order_by(CompanyExposure.id.desc())).all())
        if not include_disabled:
            rows = self._filter_active_exposures(rows)
        total = len(rows)
        return rows[offset : offset + limit], total

    def update_exposure_fields(self, edge_id: int, fields: Dict[str, Any]) -> bool:
        allowed = {
            "role",
            "strength",
            "exposure_pct",
            "direction",
            "pricing_driver",
            "summary",
            "source_ref",
            "verified_at",
            "ttl_days",
        }
        patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not patch:
            return False
        if "strength" in patch and patch["strength"] not in _VALID_STRENGTHS:
            patch["strength"] = "medium"
        if "direction" in patch and patch["direction"] not in _VALID_DIRECTIONS:
            patch["direction"] = "positive"
        patch["updated_at"] = _utc_now_naive()
        with self.db.get_session() as session:
            row = session.get(CompanyExposure, edge_id)
            if row is None:
                return False
            for key, value in patch.items():
                setattr(row, key, value)
            session.commit()
            return True

    def delete_exposure(self, edge_id: int) -> bool:
        with self.db.get_session() as session:
            row = session.get(CompanyExposure, edge_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def get_exposure_by_id(self, edge_id: Any) -> Optional[CompanyExposure]:
        try:
            key = int(edge_id)
        except (TypeError, ValueError):
            return None
        with self.db.get_session() as session:
            return session.get(CompanyExposure, key)

    def resolve_entity_ids_from_text(self, text: str) -> List[str]:
        """文本与 entity_alias 子串匹配，返回命中的 entity_id 列表。"""
        haystack = str(text or "").strip()
        if not haystack:
            return []
        matched: List[str] = []
        for row in self.list_entity_aliases():
            candidates = [row.display_name, row.entity_id]
            try:
                candidates.extend(json.loads(row.aliases_json or "[]"))
            except json.JSONDecodeError:
                pass
            for term in candidates:
                term = str(term or "").strip()
                if term and term in haystack and row.entity_id not in matched:
                    matched.append(row.entity_id)
                    break
        return matched

    def reverse_lookup_codes(self, entity_id: str) -> List[str]:
        return sorted({row.code for row in self.get_exposures_by_entity(entity_id)})

    def list_distinct_target_entity_ids(self) -> List[str]:
        with self.db.get_session() as session:
            rows = session.scalars(
                select(CompanyExposure.target_entity_id).distinct()
            ).all()
        return sorted({str(row).strip() for row in rows if row})

    def count_edges_per_entity(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entity_id in self.list_distinct_target_entity_ids():
            counts[entity_id] = len(self.get_exposures_by_entity(entity_id))
        return counts

    # ------------------------------------------------------------------
    # AnalysisBaselineCache
    # ------------------------------------------------------------------

    def upsert_baseline_cache(self, record: Dict[str, Any]) -> bool:
        code = _normalize_code(record.get("code", ""))
        if not code:
            return False
        now = _utc_now_naive()
        key_levels = record.get("key_levels")
        if isinstance(key_levels, dict):
            key_levels_json = json.dumps(key_levels, ensure_ascii=False)
        else:
            key_levels_json = record.get("key_levels_json")

        values = {
            "code": code,
            "baseline_history_id": record.get("baseline_history_id"),
            "operation_advice": record.get("operation_advice"),
            "core_thesis": record.get("core_thesis"),
            "risks": record.get("risks"),
            "key_levels_json": key_levels_json,
            "price_at_analysis": record.get("price_at_analysis"),
            "tech_summary": record.get("tech_summary"),
            "exposure_digest": record.get("exposure_digest"),
            "created_at": now,
        }
        try:
            with self.db.get_session() as session:
                existing = session.get(AnalysisBaselineCache, code)
                if existing:
                    for field, value in values.items():
                        if field == "code":
                            continue
                        setattr(existing, field, value)
                else:
                    session.add(AnalysisBaselineCache(**values))
                session.commit()
            return True
        except Exception as exc:
            logger.error("upsert_baseline_cache failed for %s: %s", code, exc)
            return False

    def get_baseline_cache(self, code: str) -> Optional[AnalysisBaselineCache]:
        key = _normalize_code(code)
        with self.db.get_session() as session:
            return session.get(AnalysisBaselineCache, key)
