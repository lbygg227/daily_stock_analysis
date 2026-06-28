# -*- coding: utf-8 -*-
"""主题新闻/公告 ingest 与图谱驱动实体反查（Phase 2a）。"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.repositories.event_signal_repo import EventSignalRepository, normalize_dedup_key
from src.repositories.exposure_repo import ExposureRepository
from src.search_service import SearchResponse, SearchResult
from src.services.exposure_graph_sync import ExposureGraphSyncService

logger = logging.getLogger(__name__)

_STRENGTH_SCORE = {"high": 3, "medium": 2, "low": 1}
_LINK_BONUS = {
    "supply_chain": 1.0,
    "equity_investment": 0.8,
    "subsidiary": 1.0,
    "revenue_share": 1.0,
    "concept": 0.4,
    "substitute": 0.5,
}


def _score_exposure(edge) -> float:
    base = _STRENGTH_SCORE.get(getattr(edge, "strength", None), 1)
    bonus = _LINK_BONUS.get(getattr(edge, "link_type", None), 0.5)
    return base * bonus


class ExposureEventIngestService:
    """将新闻条目经图谱匹配到 A 股并写入 event_signal。"""

    def __init__(
        self,
        *,
        exposure_repo: Optional[ExposureRepository] = None,
        signal_repo: Optional[EventSignalRepository] = None,
        graph_sync: Optional[ExposureGraphSyncService] = None,
        search_fn: Optional[Callable[..., SearchResponse]] = None,
        watchlist_codes: Optional[Sequence[str]] = None,
        stock_name_resolver: Optional[Callable[[str], str]] = None,
    ):
        self.exposure_repo = exposure_repo or ExposureRepository()
        self.signal_repo = signal_repo or EventSignalRepository()
        self.graph_sync = graph_sync or ExposureGraphSyncService(self.exposure_repo)
        self.search_fn = search_fn
        self.watchlist_codes = list(watchlist_codes or [])
        self.stock_name_resolver = stock_name_resolver or (lambda _code: "")

    def _resolve_search_fn(self):
        if self.search_fn is not None:
            return self.search_fn
        from src.search_service import get_search_service

        service = get_search_service()
        if not service.is_available:
            return None

        def _search(keyword: str, *, max_results: int = 5) -> SearchResponse:
            return service.search_stock_news(
                stock_code="000001",
                stock_name="",
                max_results=max_results,
                focus_keywords=[f"{keyword} 最新消息"],
            )

        return _search

    def resolve_ingest_queries(self, config: Any) -> List[str]:
        """图谱驱动生成 ingest 查询词；配置关键词仅作可选 fallback。"""
        max_queries = max(1, int(getattr(config, "exposure_ingest_max_queries", 20) or 20))
        graph_queries = self.graph_sync.build_ingest_queries_from_graph(
            max_queries=max_queries,
            watchlist_codes=self.watchlist_codes,
            name_resolver=self.stock_name_resolver,
        )
        queries = ExposureGraphSyncService.resolve_ingest_query_lists(
            config,
            graph_queries,
        )
        logger.debug(
            "[ExposureEventIngest] ingest queries (%s): %s",
            getattr(config, "exposure_ingest_query_mode", "graph"),
            queries[:5],
        )
        return queries

    def build_matched_codes(self, entity_ids: List[str]) -> List[Dict[str, Any]]:
        seen: Dict[str, Dict[str, Any]] = {}
        for entity_id in entity_ids:
            for edge in self.exposure_repo.get_exposures_by_entity(entity_id):
                score = _score_exposure(edge)
                current = seen.get(edge.code)
                payload = {
                    "code": edge.code,
                    "edge_id": edge.id,
                    "target_entity_id": edge.target_entity_id,
                    "link_type": edge.link_type,
                    "strength": edge.strength,
                    "score": round(score, 2),
                }
                if current is None or payload["score"] > current.get("score", 0):
                    seen[edge.code] = payload
        return sorted(seen.values(), key=lambda item: item.get("score", 0), reverse=True)

    def process_item(
        self,
        *,
        source_type: str,
        title: str,
        source_url: str,
        snippet: Optional[str] = None,
        published_at: Optional[datetime] = None,
        force: bool = False,
    ) -> Optional[int]:
        text = f"{title} {snippet or ''}"
        entities = self.exposure_repo.resolve_entity_ids_from_text(text)
        if not entities:
            return None

        matched_codes = self.build_matched_codes(entities)
        if not matched_codes:
            return None

        if force:
            record = {
                "source_type": source_type,
                "source_url": source_url,
                "title": title,
                "snippet": snippet,
                "published_at": published_at,
                "entities": entities,
                "matched_codes": matched_codes,
                "status": "pending",
                "dedup_key": normalize_dedup_key(title),
            }
            return self._insert_force(record)

        return self.signal_repo.insert_signal(
            {
                "source_type": source_type,
                "source_url": source_url,
                "title": title,
                "snippet": snippet,
                "published_at": published_at,
                "entities": entities,
                "matched_codes": matched_codes,
                "status": "pending",
            }
        )

    def _insert_force(self, record: Dict[str, Any]) -> Optional[int]:
        if self.signal_repo.exists_by_url(record["source_url"]):
            return None
        return self.signal_repo.insert_signal(record)

    def ingest_search_result(
        self,
        item: SearchResult,
        *,
        source_type: str = "news",
    ) -> Optional[int]:
        if not item or not item.url or not item.title:
            return None
        return self.process_item(
            source_type=source_type,
            title=item.title,
            source_url=item.url,
            snippet=item.snippet,
            published_at=self._parse_published(item.published_date),
        )

    @staticmethod
    def _parse_published(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        return None

    def ingest_graph_driven_news(self, config: Any) -> Dict[str, int]:
        """用图谱推导的查询词拉取新闻，命中后沿边反查标的。"""
        stats = {
            "searched": 0,
            "results": 0,
            "inserted": 0,
            "matched": 0,
            "skipped": 0,
            "query_terms": 0,
        }
        queries = self.resolve_ingest_queries(config)
        stats["query_terms"] = len(queries)
        if not queries:
            logger.info("[ExposureEventIngest] no graph ingest queries; sync exposures first")
            stats["skipped"] += 1
            return stats

        search = self._resolve_search_fn()
        if search is None:
            logger.info("[ExposureEventIngest] search service unavailable")
            stats["skipped"] += 1
            return stats

        for keyword in queries:
            keyword = str(keyword).strip()
            if not keyword:
                continue
            stats["searched"] += 1
            try:
                response = search(keyword)
            except Exception as exc:
                logger.warning("[ExposureEventIngest] search failed for %s: %s", keyword, exc)
                stats["skipped"] += 1
                continue
            if not response or not response.success:
                stats["skipped"] += 1
                continue
            for result in response.results or []:
                stats["results"] += 1
                signal_id = self.ingest_search_result(result, source_type="news")
                if signal_id:
                    stats["inserted"] += 1
                    stats["matched"] += 1
        return stats

    def ingest_watchlist_announcements(self) -> Dict[str, int]:
        stats = {
            "searched": 0,
            "results": 0,
            "inserted": 0,
            "matched": 0,
            "skipped": 0,
        }
        search = self._resolve_search_fn()
        if search is None or not self.watchlist_codes:
            stats["skipped"] += 1
            return stats

        for code in self.watchlist_codes:
            code = str(code).strip()
            if not code:
                continue
            name = self.stock_name_resolver(code) or code
            stats["searched"] += 1
            try:
                response = search(f"{name} {code} 公告", max_results=3)
            except Exception as exc:
                logger.warning(
                    "[ExposureEventIngest] announcement search failed for %s: %s",
                    code,
                    exc,
                )
                stats["skipped"] += 1
                continue
            if not response or not response.success:
                stats["skipped"] += 1
                continue
            for result in response.results or []:
                stats["results"] += 1
                signal_id = self.ingest_search_result(result, source_type="announcement")
                if signal_id:
                    stats["inserted"] += 1
                    stats["matched"] += 1
        return stats

    def ingest_from_config(self, config: Any) -> Dict[str, int]:
        totals = {
            "searched": 0,
            "results": 0,
            "inserted": 0,
            "matched": 0,
            "skipped": 0,
            "query_terms": 0,
        }
        if getattr(config, "theme_news_ingest_enabled", False):
            part = self.ingest_graph_driven_news(config)
            for key in totals:
                totals[key] += part.get(key, 0)
        if getattr(config, "announcement_monitor_enabled", False):
            part = self.ingest_watchlist_announcements()
            for key in ("searched", "results", "inserted", "matched", "skipped"):
                totals[key] += part.get(key, 0)
        return totals
