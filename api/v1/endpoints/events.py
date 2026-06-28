# -*- coding: utf-8 -*-
"""事件 inbox API（Phase 2a）。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_database_manager
from api.v1.schemas.events import (
    EventFeedbackRequest,
    EventSignalItem,
    EventSignalListResponse,
    EventMutationResponse,
)
from src.repositories.event_signal_repo import EventSignalRepository
from src.repositories.exposure_feedback_repo import ExposureFeedbackRepository
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_event_signal_repo(
    db: DatabaseManager = Depends(get_database_manager),
) -> EventSignalRepository:
    return EventSignalRepository(db)


def _get_feedback_repo(
    db: DatabaseManager = Depends(get_database_manager),
) -> ExposureFeedbackRepository:
    return ExposureFeedbackRepository(db)


@router.get("/signals", response_model=EventSignalListResponse)
def list_event_signals(
    status: Optional[str] = Query(None, description="按状态筛选，如 analyzed / skipped"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: EventSignalRepository = Depends(_get_event_signal_repo),
) -> EventSignalListResponse:
    rows, total = repo.list_signals(status=status, limit=limit, offset=offset)
    items = [EventSignalItem(**EventSignalRepository.to_dict(row)) for row in rows]
    return EventSignalListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/signals/{signal_id}/feedback", response_model=EventMutationResponse)
def submit_event_feedback(
    signal_id: int,
    body: EventFeedbackRequest,
    repo: EventSignalRepository = Depends(_get_event_signal_repo),
    feedback_repo: ExposureFeedbackRepository = Depends(_get_feedback_repo),
) -> EventMutationResponse:
    target = repo.get_signal_by_id(signal_id)
    if target is None:
        raise HTTPException(status_code=404, detail="event signal not found")

    feedback_id = feedback_repo.insert_feedback(
        {
            "target_type": "event_signal",
            "target_id": signal_id,
            "feedback_type": body.feedback_type,
            "note": body.note,
        }
    )
    if feedback_id is None:
        raise HTTPException(status_code=400, detail="invalid feedback")

    if body.feedback_type in {"false_positive", "inaccurate"}:
        repo.update_status(
            signal_id,
            status="skipped",
            skip_reason="user_false_positive",
        )

    return EventMutationResponse(
        success=True,
        message=f"feedback #{feedback_id} recorded",
    )
