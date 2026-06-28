# -*- coding: utf-8 -*-
"""暴露图谱 API（Phase 1 只读 + Phase 5 运营）。"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_database_manager
from api.v1.schemas.exposure import (
    CompanyExposureItem,
    CompanyProfileItem,
    EntityAliasItem,
    ExposureByCodeResponse,
    ExposureByEntityResponse,
    ExposureEdgeListResponse,
    ExposureEdgeUpdateRequest,
    ExposureFeedbackItem,
    ExposureFeedbackListResponse,
    ExposureFeedbackRequest,
    ExposureMutationResponse,
)
from src.repositories.exposure_feedback_repo import ExposureFeedbackRepository
from src.repositories.exposure_repo import ExposureRepository
from src.services.baseline_cache_service import format_baseline_for_api
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_exposure_repo(
    db: DatabaseManager = Depends(get_database_manager),
) -> ExposureRepository:
    return ExposureRepository(db)


def _get_feedback_repo(
    db: DatabaseManager = Depends(get_database_manager),
) -> ExposureFeedbackRepository:
    return ExposureFeedbackRepository(db)


def _exposure_to_item(row, *, feedback_repo: ExposureFeedbackRepository) -> CompanyExposureItem:
    verified = row.verified_at.isoformat() if row.verified_at is not None else None
    return CompanyExposureItem(
        id=row.id,
        code=row.code,
        target_entity_id=row.target_entity_id,
        link_type=row.link_type,
        role=row.role,
        strength=row.strength,
        exposure_pct=row.exposure_pct,
        direction=row.direction,
        pricing_driver=row.pricing_driver,
        summary=row.summary,
        source=row.source,
        source_ref=row.source_ref,
        verified_at=verified,
        ttl_days=row.ttl_days,
        is_disabled=feedback_repo.is_exposure_disabled(row.id),
    )


@router.get("/edges", response_model=ExposureEdgeListResponse)
def list_exposure_edges(
    code: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    include_disabled: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo: ExposureRepository = Depends(_get_exposure_repo),
    feedback_repo: ExposureFeedbackRepository = Depends(_get_feedback_repo),
) -> ExposureEdgeListResponse:
    rows, total = repo.list_exposures(
        code=code,
        entity_id=entity_id,
        source=source,
        include_disabled=include_disabled,
        limit=limit,
        offset=offset,
    )
    items = [_exposure_to_item(row, feedback_repo=feedback_repo) for row in rows]
    return ExposureEdgeListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/edges/{edge_id}", response_model=ExposureMutationResponse)
def update_exposure_edge(
    edge_id: int,
    body: ExposureEdgeUpdateRequest,
    repo: ExposureRepository = Depends(_get_exposure_repo),
) -> ExposureMutationResponse:
    row = repo.get_exposure_by_id(edge_id)
    if row is None:
        raise HTTPException(status_code=404, detail="exposure edge not found")
    payload = body.model_dump(exclude_unset=True)
    if not repo.update_exposure_fields(edge_id, payload):
        raise HTTPException(status_code=400, detail="no valid fields to update")
    return ExposureMutationResponse(success=True, message="updated")


@router.delete("/edges/{edge_id}", response_model=ExposureMutationResponse)
def delete_exposure_edge(
    edge_id: int,
    repo: ExposureRepository = Depends(_get_exposure_repo),
) -> ExposureMutationResponse:
    if not repo.delete_exposure(edge_id):
        raise HTTPException(status_code=404, detail="exposure edge not found")
    return ExposureMutationResponse(success=True, message="deleted")


@router.post("/edges/{edge_id}/feedback", response_model=ExposureMutationResponse)
def submit_exposure_feedback(
    edge_id: int,
    body: ExposureFeedbackRequest,
    repo: ExposureRepository = Depends(_get_exposure_repo),
    feedback_repo: ExposureFeedbackRepository = Depends(_get_feedback_repo),
) -> ExposureMutationResponse:
    row = repo.get_exposure_by_id(edge_id)
    if row is None:
        raise HTTPException(status_code=404, detail="exposure edge not found")
    feedback_id = feedback_repo.insert_feedback(
        {
            "target_type": "exposure",
            "target_id": edge_id,
            "feedback_type": body.feedback_type,
            "note": body.note,
            "code": row.code,
            "entity_id": row.target_entity_id,
        }
    )
    if feedback_id is None:
        raise HTTPException(status_code=400, detail="invalid feedback")
    return ExposureMutationResponse(success=True, message=f"feedback #{feedback_id} recorded")


@router.get("/feedback", response_model=ExposureFeedbackListResponse)
def list_exposure_feedback(
    target_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    feedback_repo: ExposureFeedbackRepository = Depends(_get_feedback_repo),
) -> ExposureFeedbackListResponse:
    rows, total = feedback_repo.list_feedback(
        target_type=target_type,
        limit=limit,
        offset=offset,
    )
    items = [ExposureFeedbackItem(**ExposureFeedbackRepository.to_dict(row)) for row in rows]
    return ExposureFeedbackListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/by-code/{code}", response_model=ExposureByCodeResponse)
def get_exposure_by_code(
    code: str,
    include_disabled: bool = Query(False),
    repo: ExposureRepository = Depends(_get_exposure_repo),
    feedback_repo: ExposureFeedbackRepository = Depends(_get_feedback_repo),
) -> ExposureByCodeResponse:
    normalized = code.strip().zfill(6) if code.strip().isdigit() else code.strip()
    profile_row = repo.get_company_profile(normalized)
    profile = None
    if profile_row is not None:
        profile = CompanyProfileItem(
            code=profile_row.code,
            name=profile_row.name,
            surface_business=profile_row.surface_business,
            pricing_notes=profile_row.pricing_notes,
            industry_ths=profile_row.industry_ths,
        )
    rows = repo.get_exposures_by_code(
        normalized,
        active_only=not include_disabled,
    )
    exposures = [_exposure_to_item(row, feedback_repo=feedback_repo) for row in rows]
    baseline_row = repo.get_baseline_cache(normalized)
    baseline = format_baseline_for_api(baseline_row) if baseline_row is not None else None
    return ExposureByCodeResponse(
        code=normalized,
        profile=profile,
        exposures=exposures,
        baseline=baseline,
    )


@router.get("/by-entity/{entity_id}", response_model=ExposureByEntityResponse)
def get_exposure_by_entity(
    entity_id: str,
    include_disabled: bool = Query(False),
    repo: ExposureRepository = Depends(_get_exposure_repo),
    feedback_repo: ExposureFeedbackRepository = Depends(_get_feedback_repo),
) -> ExposureByEntityResponse:
    key = entity_id.strip()
    if not key:
        raise HTTPException(status_code=400, detail="entity_id is required")
    entity_row = repo.get_entity_alias(key)
    entity = None
    if entity_row is not None:
        try:
            aliases = json.loads(entity_row.aliases_json or "[]")
        except json.JSONDecodeError:
            aliases = []
        entity = EntityAliasItem(
            entity_id=entity_row.entity_id,
            display_name=entity_row.display_name,
            aliases=aliases,
            entity_type=entity_row.entity_type,
        )
    rows = repo.get_exposures_by_entity(key, active_only=not include_disabled)
    exposures = [_exposure_to_item(row, feedback_repo=feedback_repo) for row in rows]
    codes = sorted({row.code for row in rows})
    return ExposureByEntityResponse(
        entity_id=key,
        entity=entity,
        codes=codes,
        exposures=exposures,
    )
