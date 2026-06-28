# -*- coding: utf-8 -*-
"""暴露图谱 API schemas。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class EntityAliasItem(BaseModel):
    entity_id: str
    display_name: str
    aliases: List[str] = Field(default_factory=list)
    entity_type: str = "theme"


class CompanyProfileItem(BaseModel):
    code: str
    name: Optional[str] = None
    surface_business: Optional[str] = None
    pricing_notes: Optional[str] = None
    industry_ths: Optional[str] = None


class CompanyExposureItem(BaseModel):
    id: int
    code: str
    target_entity_id: str
    link_type: str
    role: Optional[str] = None
    strength: str
    exposure_pct: Optional[float] = None
    direction: str
    pricing_driver: str
    summary: Optional[str] = None
    source: str
    source_ref: Optional[str] = None
    verified_at: Optional[str] = None
    ttl_days: int
    is_disabled: bool = False


class ExposureByCodeResponse(BaseModel):
    code: str
    profile: Optional[CompanyProfileItem] = None
    exposures: List[CompanyExposureItem] = Field(default_factory=list)
    baseline: Optional[dict] = None


class ExposureByEntityResponse(BaseModel):
    entity_id: str
    entity: Optional[EntityAliasItem] = None
    codes: List[str] = Field(default_factory=list)
    exposures: List[CompanyExposureItem] = Field(default_factory=list)


class ExposureEdgeListResponse(BaseModel):
    items: List[CompanyExposureItem] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class ExposureEdgeUpdateRequest(BaseModel):
    strength: Optional[str] = None
    summary: Optional[str] = None
    role: Optional[str] = None
    exposure_pct: Optional[float] = None
    direction: Optional[str] = None
    source_ref: Optional[str] = None


class ExposureFeedbackRequest(BaseModel):
    feedback_type: str = Field(
        ...,
        description="inaccurate | false_positive | confirm | disable",
    )
    note: Optional[str] = None


class ExposureFeedbackItem(BaseModel):
    id: int
    target_type: str
    target_id: int
    feedback_type: str
    note: Optional[str] = None
    code: Optional[str] = None
    entity_id: Optional[str] = None
    created_at: Optional[str] = None


class ExposureFeedbackListResponse(BaseModel):
    items: List[ExposureFeedbackItem] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class ExposureMutationResponse(BaseModel):
    success: bool
    message: str = ""
