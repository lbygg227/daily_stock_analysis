# -*- coding: utf-8 -*-
"""暴露图谱运营反馈数据访问层（Phase 5）。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import desc, select

from src.storage import DatabaseManager, ExposureFeedback

logger = logging.getLogger(__name__)

_DISABLE_EXPOSURE_TYPES = frozenset({"disable", "inaccurate"})
_VALID_TARGET_TYPES = frozenset({"exposure", "event_signal"})
_VALID_FEEDBACK_TYPES = frozenset(
    {"inaccurate", "false_positive", "confirm", "disable"}
)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ExposureFeedbackRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def insert_feedback(self, record: Dict[str, Any]) -> Optional[int]:
        target_type = str(record.get("target_type") or "").strip().lower()
        feedback_type = str(record.get("feedback_type") or "").strip().lower()
        try:
            target_id = int(record.get("target_id"))
        except (TypeError, ValueError):
            return None
        if target_type not in _VALID_TARGET_TYPES:
            return None
        if feedback_type not in _VALID_FEEDBACK_TYPES:
            return None

        row = ExposureFeedback(
            target_type=target_type,
            target_id=target_id,
            feedback_type=feedback_type,
            note=record.get("note"),
            code=record.get("code"),
            entity_id=record.get("entity_id"),
            created_at=_utc_now_naive(),
        )
        try:
            with self.db.get_session() as session:
                session.add(row)
                session.commit()
                session.refresh(row)
                return row.id
        except Exception as exc:
            logger.warning("insert_feedback failed: %s", exc)
            return None

    def list_disabled_exposure_ids(self) -> Set[int]:
        """返回应排除在反查之外的暴露边 id（disable / inaccurate）。"""
        disabled: Set[int] = set()
        with self.db.get_session() as session:
            rows = session.scalars(
                select(ExposureFeedback)
                .where(
                    ExposureFeedback.target_type == "exposure",
                    ExposureFeedback.feedback_type.in_(tuple(_DISABLE_EXPOSURE_TYPES)),
                )
                .order_by(desc(ExposureFeedback.created_at))
            ).all()
        for row in rows:
            disabled.add(int(row.target_id))
        return disabled

    def is_exposure_disabled(self, exposure_id: int) -> bool:
        return int(exposure_id) in self.list_disabled_exposure_ids()

    def list_feedback(
        self,
        *,
        target_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ExposureFeedback], int]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self.db.get_session() as session:
            query = select(ExposureFeedback)
            if target_type:
                query = query.where(
                    ExposureFeedback.target_type == str(target_type).strip().lower()
                )
            total = len(session.scalars(query).all())
            rows = session.scalars(
                query.order_by(desc(ExposureFeedback.created_at))
                .offset(offset)
                .limit(limit)
            ).all()
            return list(rows), total

    @staticmethod
    def to_dict(row: ExposureFeedback) -> Dict[str, Any]:
        created_at = row.created_at
        return {
            "id": row.id,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "feedback_type": row.feedback_type,
            "note": row.note,
            "code": row.code,
            "entity_id": row.entity_id,
            "created_at": created_at.isoformat() if created_at else None,
        }
