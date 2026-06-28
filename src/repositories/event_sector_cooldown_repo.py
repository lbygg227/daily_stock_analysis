# -*- coding: utf-8 -*-
"""板块共振推送冷却（Phase 4）。"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.storage import DatabaseManager, EventSectorCooldown

logger = logging.getLogger(__name__)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_sector_key(sector_name: str) -> str:
    text = re.sub(r"\s+", "", str(sector_name or "").strip().lower())
    if not text:
        return ""
    if len(text) <= 128:
        return text
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:24]
    return f"hash_{digest}"


class EventSectorCooldownRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def is_in_cooldown(self, sector_name: str, *, now: Optional[datetime] = None) -> bool:
        key = normalize_sector_key(sector_name)
        if not key:
            return False
        current = now or _utc_now_naive()
        with self.db.get_session() as session:
            row = session.get(EventSectorCooldown, key)
            if row is None or row.cooldown_until is None:
                return False
            return row.cooldown_until > current

    def set_cooldown(
        self,
        sector_name: str,
        *,
        cooldown_minutes: int,
        event_signal_id: Optional[int] = None,
        reason: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> None:
        key = normalize_sector_key(sector_name)
        if not key:
            return
        current = now or _utc_now_naive()
        until = current + timedelta(minutes=max(1, int(cooldown_minutes)))
        with self.db.get_session() as session:
            row = session.get(EventSectorCooldown, key)
            if row is None:
                row = EventSectorCooldown(sector_key=key)
                session.add(row)
            row.cooldown_until = until
            row.last_event_signal_id = event_signal_id
            row.reason = reason
            row.updated_at = current
            session.commit()
