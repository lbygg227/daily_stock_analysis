# -*- coding: utf-8 -*-
"""
暴露图谱同步：从边关系推导实体与 ingest 查询词（非配置枚举业务逻辑）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from src.repositories.exposure_repo import ExposureRepository

logger = logging.getLogger(__name__)

_VALID_INGEST_MODES = frozenset({"graph", "keywords", "both"})


class ExposureGraphSyncService:
    """维护图谱可发现性，并从图谱生成新闻 ingest 查询词。"""

    def __init__(self, repo: Optional[ExposureRepository] = None):
        self.repo = repo or ExposureRepository()

    def ensure_entity_aliases_from_exposures(self) -> int:
        """为 company_exposure 中的 target 补全缺失的 entity_alias（占位）。"""
        created = 0
        for entity_id in self.repo.list_distinct_target_entity_ids():
            if self.repo.get_entity_alias(entity_id):
                continue
            if self.repo.upsert_entity_alias(
                {
                    "entity_id": entity_id,
                    "display_name": entity_id,
                    "aliases": [],
                    "entity_type": "theme",
                }
            ):
                created += 1
        if created:
            logger.info(
                "[ExposureGraphSync] created %s stub entity_alias from exposures",
                created,
            )
        return created

    def sync_watchlist_company_entities(
        self,
        codes: Sequence[str],
        *,
        name_resolver: Optional[Any] = None,
    ) -> int:
        """为自选股注册可命中实体（公司名 / 代码），便于标题直接点名时走图谱。"""
        updated = 0
        for raw_code in codes:
            code = str(raw_code or "").strip()
            if not code or not code.isdigit():
                continue
            code = code.zfill(6)
            entity_id = f"cn:{code}"
            name = ""
            if name_resolver is not None:
                name = str(name_resolver(code) or "").strip()
            profile = self.repo.get_company_profile(code)
            if not name and profile and profile.name:
                name = profile.name.strip()
            aliases = [name] if name else []
            if code not in aliases:
                aliases.append(code)
            display = name or code
            if self.repo.upsert_entity_alias(
                {
                    "entity_id": entity_id,
                    "display_name": display,
                    "aliases": aliases,
                    "entity_type": "company",
                }
            ):
                updated += 1
            if name and self.repo.upsert_company_profile(
                {"code": code, "name": name}
            ):
                pass
            # 公司自身作为「直接节点」：新闻点名该股时可命中
            self.repo.upsert_company_exposure(
                {
                    "code": code,
                    "target_entity_id": entity_id,
                    "link_type": "revenue_share",
                    "role": "直接提及",
                    "strength": "high",
                    "direction": "neutral",
                    "pricing_driver": "core_business",
                    "summary": f"{display} 自身作为图谱节点，用于标题直接命中",
                    "source": "graph_sync",
                    "source_ref": "watchlist_company_entity",
                }
            )
        return updated

    def build_ingest_queries_from_graph(
        self,
        *,
        max_queries: int = 20,
        watchlist_codes: Optional[Sequence[str]] = None,
        name_resolver: Optional[Any] = None,
    ) -> List[str]:
        """从 entity_alias + 暴露边权重生成搜索词列表（图谱驱动，非 .env 枚举）。"""
        self.ensure_entity_aliases_from_exposures()
        if watchlist_codes:
            self.sync_watchlist_company_entities(
                watchlist_codes,
                name_resolver=name_resolver,
            )

        edge_counts = self.repo.count_edges_per_entity()
        ranked_entities = sorted(
            self.repo.list_entity_aliases(),
            key=lambda row: (
                -edge_counts.get(row.entity_id, 0),
                row.entity_id,
            ),
        )

        terms: List[str] = []
        seen: set[str] = set()
        for row in ranked_entities:
            candidates = [row.display_name, row.entity_id]
            try:
                import json

                candidates.extend(json.loads(row.aliases_json or "[]"))
            except json.JSONDecodeError:
                pass
            for term in candidates:
                text = str(term or "").strip()
                if not text or len(text) < 2:
                    continue
                key = text.lower()
                if key in seen:
                    continue
                seen.add(key)
                terms.append(text)
                if len(terms) >= max_queries:
                    return terms
        return terms

    @staticmethod
    def resolve_ingest_query_lists(
        config: Any,
        graph_queries: List[str],
    ) -> List[str]:
        """按 ingest 模式合并图谱查询词与可选 fallback 关键词。"""
        mode = str(getattr(config, "exposure_ingest_query_mode", "graph") or "graph").lower()
        if mode not in _VALID_INGEST_MODES:
            mode = "graph"

        raw = str(getattr(config, "theme_news_keywords", "") or "")
        fallback = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]

        if mode == "keywords":
            return fallback or graph_queries
        if mode == "both":
            merged: List[str] = []
            seen: set[str] = set()
            for term in list(graph_queries) + list(fallback):
                key = term.lower()
                if key in seen:
                    continue
                seen.add(key)
                merged.append(term)
            return merged
        return graph_queries
