# -*- coding: utf-8 -*-
"""event_push_cooldown 数据访问层（Phase 3）。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.storage import DatabaseManager, EventPushCooldown

logger = logging.getLogger(__name__)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EventPushCooldownRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def is_in_cooldown(self, code: str, *, now: Optional[datetime] = None) -> bool:
        key = str(code or "").strip()
        if not key:
            return False
        if key.isdigit():
            key = key.zfill(6)
        current = now or _utc_now_naive()
        with self.db.get_session() as session:
            row = session.get(EventPushCooldown, key)
            if row is None or row.cooldown_until is None:
                return False
            return row.cooldown_until > current

    def set_cooldown(
        self,
        code: str,
        *,
        cooldown_minutes: int,
        event_signal_id: Optional[int] = None,
        reason: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> None:
        key = str(code or "").strip()
        if not key:
            return
        if key.isdigit():
            key = key.zfill(6)
        current = now or _utc_now_naive()
        until = current + timedelta(minutes=max(1, int(cooldown_minutes)))
        with self.db.get_session() as session:
            row = session.get(EventPushCooldown, key)
            if row is None:
                row = EventPushCooldown(code=key)
                session.add(row)
            row.cooldown_until = until
            row.last_event_signal_id = event_signal_id
            row.reason = reason
            row.updated_at = current
            session.commit()
