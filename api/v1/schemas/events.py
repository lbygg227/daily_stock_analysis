# -*- coding: utf-8 -*-
"""事件 inbox API schemas（Phase 2a）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EventSignalItem(BaseModel):
    id: int
    source_type: str
    source_url: str
    title: str
    snippet: Optional[str] = None
    published_at: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    event_type: Optional[str] = None
    sentiment: Optional[str] = None
    matched_codes: List[Dict[str, Any]] = Field(default_factory=list)
    resonance_sector: Optional[str] = None
    dedup_key: Optional[str] = None
    status: str
    skip_reason: Optional[str] = None
    processed_at: Optional[str] = None


class EventSignalListResponse(BaseModel):
    items: List[EventSignalItem]
    total: int
    limit: int
    offset: int


class EventFeedbackRequest(BaseModel):
    feedback_type: str = Field(
        ...,
        description="false_positive | inaccurate | confirm",
    )
    note: Optional[str] = None


class EventMutationResponse(BaseModel):
    success: bool
    message: str = ""
