# -*- coding: utf-8 -*-
"""主题包 YAML 导入服务（Phase 1）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml

from src.repositories.exposure_repo import ExposureRepository

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_THEME_DIR = _PROJECT_ROOT / "config" / "themes"


def resolve_theme_pack_path(
    *,
    pack_id: Optional[str] = None,
    path: Optional[Union[str, Path]] = None,
) -> Path:
    if path is not None:
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = _PROJECT_ROOT / resolved
        return resolved
    if not pack_id:
        raise ValueError("pack_id or path is required")
    candidate = _DEFAULT_THEME_DIR / f"{pack_id}.yaml"
    if not candidate.exists():
        raise FileNotFoundError(f"Theme pack not found: {candidate}")
    return candidate


def load_theme_pack_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid theme pack YAML: {path}")
    return data


def import_theme_pack(
    *,
    pack_id: Optional[str] = None,
    path: Optional[Union[str, Path]] = None,
    repo: Optional[ExposureRepository] = None,
) -> Dict[str, int]:
    """将主题包 YAML 导入 entity_alias / company_profile / company_exposure。"""
    yaml_path = resolve_theme_pack_path(pack_id=pack_id, path=path)
    payload = load_theme_pack_yaml(yaml_path)
    pack_key = str(payload.get("id") or pack_id or yaml_path.stem)

    repository = repo or ExposureRepository()
    stats = {
        "entity_aliases": 0,
        "company_profiles": 0,
        "exposures": 0,
        "errors": 0,
    }

    for item in payload.get("entity_aliases") or []:
        if not isinstance(item, dict):
            stats["errors"] += 1
            continue
        if repository.upsert_entity_alias(item):
            stats["entity_aliases"] += 1
        else:
            stats["errors"] += 1

    for item in payload.get("company_profiles") or []:
        if not isinstance(item, dict):
            stats["errors"] += 1
            continue
        if repository.upsert_company_profile(item):
            stats["company_profiles"] += 1
        else:
            stats["errors"] += 1

    for item in payload.get("exposures") or []:
        if not isinstance(item, dict):
            stats["errors"] += 1
            continue
        record = dict(item)
        record.setdefault("source", "theme_pack")
        record.setdefault("source_ref", pack_key)
        if repository.upsert_company_exposure(record):
            stats["exposures"] += 1
        else:
            stats["errors"] += 1

    from src.services.exposure_graph_sync import ExposureGraphSyncService

    ExposureGraphSyncService(repository).ensure_entity_aliases_from_exposures()

    logger.info(
        "Imported theme pack %s from %s: %s",
        pack_key,
        yaml_path,
        stats,
    )
    return stats
