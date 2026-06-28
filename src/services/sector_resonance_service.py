# -*- coding: utf-8 -*-
"""
板块共振检测与 digest 推送（Phase 4）。

概念/行业成分异动 + 板块涨跌榜交叉验证；推送 1 条板块摘要，不逐股刷屏。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from src.repositories.event_sector_cooldown_repo import EventSectorCooldownRepository
from src.repositories.exposure_repo import ExposureRepository

logger = logging.getLogger(__name__)


@dataclass
class SectorResonanceResult:
    sector_name: str
    sector_change_pct: float
    sector_kind: str  # concept | industry
    matched_entities: List[str] = field(default_factory=list)
    member_codes: List[str] = field(default_factory=list)
    up_count: int = 0
    total_count: int = 0
    up_ratio: float = 0.0
    leaders: List[Dict[str, Any]] = field(default_factory=list)
    should_push: bool = False
    event_title: str = ""
    skip_reason: str = ""


def _text_overlap(a: str, b: str) -> bool:
    left = str(a or "").strip().lower()
    right = str(b or "").strip().lower()
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    # 共享连续 2+ 汉字/字母片段
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", left):
        if token in right:
            return True
    return False


def format_sector_resonance_markdown(result: SectorResonanceResult) -> str:
    leader_lines = []
    for item in result.leaders[:5]:
        name = item.get("name") or item.get("code")
        pct = item.get("change_pct")
        if pct is None:
            leader_lines.append(f"- {name}")
        else:
            leader_lines.append(f"- {name} ({pct:+.2f}%)")
    leaders_text = "\n".join(leader_lines) if leader_lines else "- （无自选股命中）"
    return (
        f"### 📊 板块共振 · {result.sector_name}\n\n"
        f"**类型**：{result.sector_kind} · 板块涨跌 **{result.sector_change_pct:+.2f}%**\n"
        f"**触发**：{result.event_title}\n"
        f"**成分异动**：{result.up_count}/{result.total_count} 上涨 "
        f"（{result.up_ratio * 100:.0f}%）\n"
        f"**命中实体**：{', '.join(result.matched_entities) or '—'}\n\n"
        f"**自选股相对强弱**：\n{leaders_text}\n\n"
        f"*板块 digest；个股边传导已合并抑制（§7.6）*\n"
        f"*仅供参考，不构成投资建议*"
    )


class SectorResonanceService:
    """检测主题事件是否伴随板块集体异动。"""

    def __init__(
        self,
        *,
        exposure_repo: Optional[ExposureRepository] = None,
        sector_cooldown_repo: Optional[EventSectorCooldownRepository] = None,
        rankings_provider: Optional[Callable[[int], List[Dict[str, Any]]]] = None,
        quote_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        member_codes_provider: Optional[
            Callable[[str, Set[str]], List[str]]
        ] = None,
    ):
        self.exposure_repo = exposure_repo or ExposureRepository()
        self.sector_cooldown_repo = sector_cooldown_repo or EventSectorCooldownRepository()
        self.rankings_provider = rankings_provider
        self.quote_provider = quote_provider
        self.member_codes_provider = member_codes_provider

    def _resolve_rankings_provider(self):
        if self.rankings_provider is not None:
            return self.rankings_provider

        def _fetch(n: int) -> List[Dict[str, Any]]:
            try:
                from data_provider.base import DataFetcherManager

                manager = DataFetcherManager()
                merged: List[Dict[str, Any]] = []
                concept = manager.get_concept_rankings(n=n)
                if concept and concept[0]:
                    for item in concept[0]:
                        merged.append(
                            {
                                "name": item.get("name"),
                                "change_pct": float(item.get("change_pct") or 0),
                                "kind": "concept",
                            }
                        )
                industry = manager.get_sector_rankings(n=n)
                if industry and industry[0]:
                    for item in industry[0]:
                        merged.append(
                            {
                                "name": item.get("name"),
                                "change_pct": float(item.get("change_pct") or 0),
                                "kind": "industry",
                            }
                        )
                merged.sort(key=lambda row: row.get("change_pct", 0), reverse=True)
                return merged[: max(n, 1)]
            except Exception as exc:
                logger.warning("[SectorResonance] rankings fetch failed: %s", exc)
                return []

        return _fetch

    def _resolve_quote_provider(self):
        if self.quote_provider is not None:
            return self.quote_provider

        def _quote(code: str) -> Optional[Dict[str, Any]]:
            try:
                from src.services.stock_service import StockService

                return StockService().get_realtime_quote(code)
            except Exception as exc:
                logger.debug("[SectorResonance] quote failed for %s: %s", code, exc)
                return None

        return _quote

    def _resolve_member_codes_provider(self):
        if self.member_codes_provider is not None:
            return self.member_codes_provider

        def _members(sector_name: str, watchlist: Set[str]) -> List[str]:
            codes: List[str] = []
            try:
                from src.repositories.fundamental_repo import FundamentalRepository

                repo = FundamentalRepository()
                _total, items = repo.search_stock_listings(
                    industry=sector_name,
                    industry_exact=False,
                    limit=80,
                    page=1,
                )
                for item in items:
                    code = str(item.get("code") or "").strip()
                    if code.isdigit():
                        code = code.zfill(6)
                        if not watchlist or code in watchlist:
                            codes.append(code)
            except Exception as exc:
                logger.debug("[SectorResonance] industry members failed: %s", exc)

            if codes:
                return codes
            return [code for code in watchlist if code]

        return _members

    def _entity_labels(self, entity_ids: Sequence[str]) -> List[str]:
        labels: List[str] = []
        for entity_id in entity_ids:
            row = self.exposure_repo.get_entity_alias(entity_id)
            if row is None:
                labels.append(str(entity_id))
                continue
            labels.append(row.display_name or entity_id)
            try:
                import json

                aliases = json.loads(row.aliases_json or "[]")
                labels.extend(str(a) for a in aliases if a)
            except Exception:
                pass
        return labels

    def _match_hot_sector(
        self,
        entity_ids: Sequence[str],
        hot_sectors: Sequence[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        labels = self._entity_labels(entity_ids)
        for sector in hot_sectors:
            sector_name = str(sector.get("name") or "").strip()
            if not sector_name:
                continue
            if float(sector.get("change_pct") or 0) <= 0:
                continue
            for label in labels:
                if _text_overlap(label, sector_name):
                    return sector
        return None

    def _measure_up_ratio(
        self,
        codes: Sequence[str],
        *,
        stock_name_resolver: Callable[[str], str],
    ) -> tuple[int, int, List[Dict[str, Any]]]:
        quote_fn = self._resolve_quote_provider()
        leaders: List[Dict[str, Any]] = []
        up = 0
        total = 0
        for raw_code in codes:
            code = str(raw_code or "").strip()
            if not code:
                continue
            if code.isdigit():
                code = code.zfill(6)
            quote = quote_fn(code)
            if not quote:
                continue
            total += 1
            change_pct = quote.get("change_percent")
            try:
                pct = float(change_pct) if change_pct is not None else 0.0
            except (TypeError, ValueError):
                pct = 0.0
            if pct > 0:
                up += 1
            leaders.append(
                {
                    "code": code,
                    "name": quote.get("stock_name") or stock_name_resolver(code) or code,
                    "change_pct": pct,
                }
            )
        leaders.sort(key=lambda item: item.get("change_pct", 0), reverse=True)
        return up, total, leaders

    def evaluate(
        self,
        *,
        entity_ids: Sequence[str],
        event_title: str,
        config: Any,
        watchlist_codes: Set[str],
        stock_name_resolver: Callable[[str], str],
    ) -> Optional[SectorResonanceResult]:
        if not getattr(config, "sector_resonance_enabled", False):
            return None
        if not entity_ids:
            return None

        min_members = max(1, int(getattr(config, "sector_resonance_min_members", 5) or 5))
        min_up_ratio = float(getattr(config, "sector_resonance_min_up_ratio", 0.6) or 0.6)
        min_up_ratio = min(1.0, max(0.1, min_up_ratio))

        hot_sectors = self._resolve_rankings_provider()(10)
        matched_sector = self._match_hot_sector(entity_ids, hot_sectors)
        if not matched_sector:
            return None

        sector_name = str(matched_sector.get("name") or "").strip()
        if self.sector_cooldown_repo.is_in_cooldown(sector_name):
            return SectorResonanceResult(
                sector_name=sector_name,
                sector_change_pct=float(matched_sector.get("change_pct") or 0),
                sector_kind=str(matched_sector.get("kind") or "concept"),
                matched_entities=list(entity_ids),
                event_title=event_title,
                should_push=False,
                skip_reason="sector_cooldown",
            )

        members = self._resolve_member_codes_provider()(sector_name, watchlist_codes)
        if len(members) < min_members:
            # 成分不足时，用自选股 ∩ 同行业 exposure 边补足样本
            extra: List[str] = []
            for entity_id in entity_ids:
                for edge in self.exposure_repo.get_exposures_by_entity(entity_id):
                    if edge.code in watchlist_codes and edge.code not in members:
                        extra.append(edge.code)
            members = list(dict.fromkeys(members + extra))

        if len(members) < min_members:
            return SectorResonanceResult(
                sector_name=sector_name,
                sector_change_pct=float(matched_sector.get("change_pct") or 0),
                sector_kind=str(matched_sector.get("kind") or "concept"),
                matched_entities=list(entity_ids),
                member_codes=members,
                event_title=event_title,
                should_push=False,
                skip_reason="insufficient_members",
            )

        up_count, total_count, leaders = self._measure_up_ratio(
            members[:30],
            stock_name_resolver=stock_name_resolver,
        )
        if total_count < min_members:
            return SectorResonanceResult(
                sector_name=sector_name,
                sector_change_pct=float(matched_sector.get("change_pct") or 0),
                sector_kind=str(matched_sector.get("kind") or "concept"),
                matched_entities=list(entity_ids),
                member_codes=members,
                event_title=event_title,
                should_push=False,
                skip_reason="insufficient_quotes",
            )

        up_ratio = up_count / total_count if total_count else 0.0
        should_push = up_ratio >= min_up_ratio
        return SectorResonanceResult(
            sector_name=sector_name,
            sector_change_pct=float(matched_sector.get("change_pct") or 0),
            sector_kind=str(matched_sector.get("kind") or "concept"),
            matched_entities=list(entity_ids),
            member_codes=members,
            up_count=up_count,
            total_count=total_count,
            up_ratio=up_ratio,
            leaders=leaders,
            should_push=should_push,
            event_title=event_title,
            skip_reason="" if should_push else "low_up_ratio",
        )
