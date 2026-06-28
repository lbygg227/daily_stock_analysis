# -*- coding: utf-8 -*-
"""
从公告/公开文本抽取暴露边并写入图谱（Phase 2b）。

原则：关系来自公告与财报文本，而非 .env 关键词或手工枚举股票映射。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.repositories.exposure_repo import ExposureRepository
from src.services.exposure_graph_sync import ExposureGraphSyncService

if TYPE_CHECKING:
    from src.search_service import SearchResponse, SearchResult

logger = logging.getLogger(__name__)

# 参股 / 投资 / 联营等 → equity_investment
_EQUITY_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"参股\s*[「\"']?([^」\"'\s，,；;]{2,24})"), "equity_investment"),
    (re.compile(r"投资\s*[「\"']?([^」\"'\s，,；;]{2,24})"), "equity_investment"),
    (re.compile(r"对\s*([^，,；;\s]{2,20}?)\s*(增资|进行投资|投资)"), "equity_investment"),
    (re.compile(r"联营企业[：:\s]\s*([^，,；;\s]{2,24})"), "equity_investment"),
    (re.compile(r"持有\s*([^，,；;\s]{2,20}?)\s*股权"), "equity_investment"),
    (re.compile(r"长期股权投资[^。；;]{0,30}?([^，,；;\s]{2,20})"), "equity_investment"),
]
# 供货 / 采购 / 合作 → supply_chain
_SUPPLY_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"向\s*([^，,；;\s]{2,20}?)\s*供货"), "supply_chain"),
    (re.compile(r"与\s*([^，,；;\s]{2,20}?)\s*(签署|签订).*?(合同|协议|订单)"), "supply_chain"),
    (re.compile(r"采购\s*([^，,；;\s]{2,20}?)\s*的?(产品|设备|材料)"), "supply_chain"),
]

_STOP_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "股份公司",
    "集团",
    "公司",
)


@dataclass
class ExtractedExposure:
    code: str
    target_name: str
    target_entity_id: str
    link_type: str
    summary: str
    source_ref: str
    exposure_pct: Optional[float] = None


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_target_name(raw: str) -> str:
    name = str(raw or "").strip()
    for suffix in _STOP_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            name = name[: -len(suffix)]
            break
    return name.strip("「」\"' 　")


def _entity_id_for_new_name(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    return f"auto_{digest}"


def _parse_pct(text: str) -> Optional[float]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


class ExposureEdgeExtractor:
    """解析公告标题/摘要，写入 company_exposure + entity_alias。"""

    def __init__(
        self,
        *,
        exposure_repo: Optional[ExposureRepository] = None,
        graph_sync: Optional[ExposureGraphSyncService] = None,
        search_fn: Optional[Callable[..., Any]] = None,
        stock_name_resolver: Optional[Callable[[str], str]] = None,
    ):
        self.repo = exposure_repo or ExposureRepository()
        self.graph_sync = graph_sync or ExposureGraphSyncService(self.repo)
        self.search_fn = search_fn
        self.stock_name_resolver = stock_name_resolver or (lambda _c: "")

    def _resolve_search_fn(self):
        if self.search_fn is not None:
            return self.search_fn
        from src.search_service import get_search_service

        service = get_search_service()
        if not service.is_available:
            return None

        def _search(keyword: str, *, max_results: int = 5):
            return service.search_stock_news(
                stock_code="000001",
                stock_name="",
                max_results=max_results,
                focus_keywords=[keyword],
            )

        return _search

    def resolve_or_create_entity_id(self, target_name: str) -> str:
        clean = _normalize_target_name(target_name)
        if not clean or len(clean) < 2:
            return ""
        hits = self.repo.resolve_entity_ids_from_text(clean)
        if hits:
            return hits[0]
        entity_id = _entity_id_for_new_name(clean)
        self.repo.upsert_entity_alias(
            {
                "entity_id": entity_id,
                "display_name": clean,
                "aliases": [clean],
                "entity_type": "theme",
            }
        )
        return entity_id

    def extract_from_text(
        self,
        *,
        code: str,
        text: str,
        source_ref: str,
    ) -> List[ExtractedExposure]:
        if not code or not text:
            return []
        body = str(text)
        found: List[ExtractedExposure] = []
        seen: set[Tuple[str, str]] = set()

        for patterns in (_EQUITY_PATTERNS, _SUPPLY_PATTERNS):
            for pattern, link_type in patterns:
                for match in pattern.finditer(body):
                    target_name = _normalize_target_name(match.group(1))
                    if not target_name or len(target_name) < 2:
                        continue
                    if target_name in {"公司", "本公司", "集团"}:
                        continue
                    key = (target_name, link_type)
                    if key in seen:
                        continue
                    entity_id = self.resolve_or_create_entity_id(target_name)
                    if not entity_id:
                        continue
                    seen.add(key)
                    pct = _parse_pct(body[match.start() : match.end() + 40])
                    found.append(
                        ExtractedExposure(
                            code=code.zfill(6) if code.isdigit() else code,
                            target_name=target_name,
                            target_entity_id=entity_id,
                            link_type=link_type,
                            summary=f"公告抽取：与 {target_name} 存在{link_type}关系",
                            source_ref=source_ref,
                            exposure_pct=pct,
                        )
                    )
        return found

    def persist_extractions(self, items: Sequence[ExtractedExposure]) -> int:
        saved = 0
        for item in items:
            strength = "medium"
            pricing_driver = (
                "theme_overlay" if item.link_type == "equity_investment" else "core_business"
            )
            if self.repo.upsert_company_exposure(
                {
                    "code": item.code,
                    "target_entity_id": item.target_entity_id,
                    "link_type": item.link_type,
                    "role": "公告抽取",
                    "strength": strength,
                    "exposure_pct": item.exposure_pct,
                    "direction": "positive",
                    "pricing_driver": pricing_driver,
                    "summary": item.summary,
                    "source": "announcement",
                    "source_ref": item.source_ref,
                    "verified_at": _utc_now_naive(),
                }
            ):
                saved += 1
        if saved:
            self.graph_sync.ensure_entity_aliases_from_exposures()
        return saved

    def extract_from_announcement_item(
        self,
        *,
        code: str,
        item: Any,
    ) -> int:
        if not item or not item.title:
            return 0
        text = f"{item.title} {item.snippet or ''}"
        source_ref = item.url or item.title
        items = self.extract_from_text(code=code, text=text, source_ref=source_ref)
        return self.persist_extractions(items)

    def extract_for_codes(
        self,
        codes: Sequence[str],
        *,
        max_results_per_code: int = 5,
    ) -> Dict[str, int]:
        stats = {
            "codes": 0,
            "searched": 0,
            "parsed": 0,
            "edges_saved": 0,
            "skipped": 0,
        }
        search = self._resolve_search_fn()
        if search is None:
            stats["skipped"] += 1
            return stats

        for raw_code in codes:
            code = str(raw_code or "").strip()
            if not code:
                continue
            code = code.zfill(6) if code.isdigit() else code
            name = self.stock_name_resolver(code) or code
            stats["codes"] += 1
            stats["searched"] += 1
            query = f"{name} {code} 公告 参股 投资 合作"
            try:
                response = search(query, max_results=max_results_per_code)
            except Exception as exc:
                logger.warning(
                    "[ExposureEdgeExtractor] search failed for %s: %s",
                    code,
                    exc,
                )
                stats["skipped"] += 1
                continue
            if not response or not response.success:
                stats["skipped"] += 1
                continue
            for result in response.results or []:
                stats["parsed"] += 1
                stats["edges_saved"] += self.extract_from_announcement_item(
                    code=code,
                    item=result,
                )
        return stats

    def extract_from_config(self, config: Any) -> Dict[str, int]:
        codes = [
            str(c).strip()
            for c in (getattr(config, "stock_list", None) or [])
            if str(c).strip()
        ]
        if not codes:
            return {"codes": 0, "searched": 0, "parsed": 0, "edges_saved": 0, "skipped": 1}
        max_results = max(1, int(getattr(config, "exposure_extraction_max_per_code", 5) or 5))
        return self.extract_for_codes(codes, max_results_per_code=max_results)
