# -*- coding: utf-8 -*-
"""event_signal 数据访问层（Phase 2a）。"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select

from src.storage import DatabaseManager, EventSignal

logger = logging.getLogger(__name__)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_dedup_key(title: str) -> str:
    text = re.sub(r"\s+", "", str(title or "").strip().lower())
    return text[:128] if text else ""


class EventSignalRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def exists_by_url(self, source_url: str) -> bool:
        url = str(source_url or "").strip()
        if not url:
            return False
        with self.db.get_session() as session:
            row = session.scalar(
                select(EventSignal.id).where(EventSignal.source_url == url).limit(1)
            )
            return row is not None

    def exists_recent_dedup_key(
        self,
        dedup_key: str,
        *,
        within_minutes: int = 15,
    ) -> bool:
        key = normalize_dedup_key(dedup_key)
        if not key:
            return False
        cutoff = _utc_now_naive() - timedelta(minutes=max(1, within_minutes))
        with self.db.get_session() as session:
            row = session.scalar(
                select(EventSignal.id)
                .where(
                    EventSignal.dedup_key == key,
                    EventSignal.processed_at >= cutoff,
                )
                .limit(1)
            )
            return row is not None

    def insert_signal(self, record: Dict[str, Any]) -> Optional[int]:
        url = str(record.get("source_url") or "").strip()
        title = str(record.get("title") or "").strip()
        if not url or not title:
            return None
        if self.exists_by_url(url):
            return None

        dedup_key = record.get("dedup_key") or normalize_dedup_key(title)
        if self.exists_recent_dedup_key(dedup_key):
            return None

        entities = record.get("entities") or []
        matched_codes = record.get("matched_codes") or []
        now = _utc_now_naive()

        row = EventSignal(
            source_type=str(record.get("source_type") or "news"),
            source_url=url,
            title=title,
            snippet=record.get("snippet"),
            published_at=record.get("published_at"),
            entities_json=json.dumps(entities, ensure_ascii=False),
            event_type=record.get("event_type"),
            sentiment=record.get("sentiment"),
            matched_codes_json=json.dumps(matched_codes, ensure_ascii=False),
            resonance_sector=record.get("resonance_sector"),
            dedup_key=dedup_key,
            status=str(record.get("status") or "pending"),
            skip_reason=record.get("skip_reason"),
            processed_at=record.get("processed_at") or now,
        )
        try:
            with self.db.get_session() as session:
                session.add(row)
                session.commit()
                session.refresh(row)
                return row.id
        except Exception as exc:
            logger.warning("insert_signal failed for %s: %s", url, exc)
            return None

    def list_signals(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[EventSignal], int]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self.db.get_session() as session:
            query = select(EventSignal)
            if status:
                query = query.where(EventSignal.status == status)
            total = len(session.scalars(query).all())
            rows = session.scalars(
                query.order_by(desc(EventSignal.processed_at))
                .offset(offset)
                .limit(limit)
            ).all()
            return list(rows), total

    def list_by_status(
        self,
        status: str,
        *,
        limit: int = 50,
    ) -> List[EventSignal]:
        limit = max(1, min(int(limit), 200))
        with self.db.get_session() as session:
            rows = session.scalars(
                select(EventSignal)
                .where(EventSignal.status == status)
                .order_by(desc(EventSignal.processed_at))
                .limit(limit)
            ).all()
            return list(rows)

    def get_signal_by_id(self, signal_id: int) -> Optional[EventSignal]:
        with self.db.get_session() as session:
            return session.get(EventSignal, signal_id)

    def update_status(
        self,
        signal_id: int,
        *,
        status: str,
        skip_reason: Optional[str] = None,
    ) -> bool:
        with self.db.get_session() as session:
            row = session.get(EventSignal, signal_id)
            if row is None:
                return False
            row.status = status
            row.skip_reason = skip_reason
            row.processed_at = _utc_now_naive()
            session.commit()
            return True

    def update_resonance_sector(
        self,
        signal_id: int,
        *,
        resonance_sector: str,
        status: Optional[str] = None,
    ) -> bool:
        with self.db.get_session() as session:
            row = session.get(EventSignal, signal_id)
            if row is None:
                return False
            row.resonance_sector = resonance_sector
            if status:
                row.status = status
            row.processed_at = _utc_now_naive()
            session.commit()
            return True

    @staticmethod
    def to_dict(row: EventSignal) -> Dict[str, Any]:
        try:
            entities = json.loads(row.entities_json or "[]")
        except json.JSONDecodeError:
            entities = []
        try:
            matched_codes = json.loads(row.matched_codes_json or "[]")
        except json.JSONDecodeError:
            matched_codes = []
        processed_at = row.processed_at
        published_at = row.published_at
        return {
            "id": row.id,
            "source_type": row.source_type,
            "source_url": row.source_url,
            "title": row.title,
            "snippet": row.snippet,
            "published_at": published_at.isoformat() if published_at else None,
            "entities": entities,
            "event_type": row.event_type,
            "sentiment": row.sentiment,
            "matched_codes": matched_codes,
            "resonance_sector": row.resonance_sector,
            "dedup_key": row.dedup_key,
            "status": row.status,
            "skip_reason": row.skip_reason,
            "processed_at": processed_at.isoformat() if processed_at else None,
        }
