# -*- coding: utf-8 -*-
"""事件增量处理：门控、Top-N 分析、推送（Phase 3）。"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from src.core.trading_calendar import build_market_phase_context
from src.repositories.event_push_cooldown_repo import EventPushCooldownRepository
from src.repositories.event_signal_repo import EventSignalRepository
from src.repositories.exposure_repo import ExposureRepository
from src.services.event_delta_analysis import (
    EventDeltaAnalysisService,
    EventDeltaResult,
    _confidence_meets_minimum,
    format_event_push_markdown,
)
from src.services.sector_resonance_service import (
    SectorResonanceService,
    format_sector_resonance_markdown,
)

logger = logging.getLogger(__name__)

_SOURCE_LABELS = {
    "news": "主题新闻",
    "announcement": "公告",
}


class EventDeltaProcessor:
    """消费 pending 的 event_signal，执行增量分析与条件推送。"""

    def __init__(
        self,
        *,
        signal_repo: Optional[EventSignalRepository] = None,
        cooldown_repo: Optional[EventPushCooldownRepository] = None,
        exposure_repo: Optional[ExposureRepository] = None,
        analysis_service: Optional[EventDeltaAnalysisService] = None,
        notifier: Optional[Any] = None,
        portfolio_codes_provider: Optional[Callable[[], Set[str]]] = None,
        stock_name_resolver: Optional[Callable[[str], str]] = None,
        sector_service: Optional[SectorResonanceService] = None,
    ):
        self.signal_repo = signal_repo or EventSignalRepository()
        self.cooldown_repo = cooldown_repo or EventPushCooldownRepository()
        self.exposure_repo = exposure_repo or ExposureRepository()
        self.analysis_service = analysis_service or EventDeltaAnalysisService(
            exposure_repo=self.exposure_repo,
        )
        self.notifier = notifier
        self.portfolio_codes_provider = portfolio_codes_provider
        self.stock_name_resolver = stock_name_resolver or (lambda _c: "")
        self.sector_service = sector_service or SectorResonanceService(
            exposure_repo=self.exposure_repo,
        )

    def _resolve_notifier(self):
        if self.notifier is not None:
            return self.notifier
        from src.notification import NotificationService

        return NotificationService()

    def _portfolio_codes(self) -> Set[str]:
        if self.portfolio_codes_provider is not None:
            return set(self.portfolio_codes_provider())
        try:
            from src.repositories.portfolio_repo import PortfolioRepository

            identities = PortfolioRepository().list_cached_position_identities()
            codes: Set[str] = set()
            for market, symbol in identities:
                if str(market).lower() in {"cn", "a", "ashare", ""}:
                    sym = str(symbol).strip()
                    if sym.isdigit():
                        codes.add(sym.zfill(6))
            return codes
        except Exception:
            return set()

    def _watchlist_codes(self, config: Any) -> Set[str]:
        raw = getattr(config, "stock_list", None) or []
        codes = set()
        for item in raw:
            text = str(item).strip()
            if text.isdigit():
                codes.add(text.zfill(6))
        return codes

    def _session_allows_push(self, config: Any, *, force: bool = False) -> bool:
        if force:
            return True
        if getattr(config, "exposure_event_ingest_outside_session", False):
            return True
        ctx = build_market_phase_context("cn", requested_phase="auto")
        return bool(ctx.is_market_open_now)

    def _rank_candidates(
        self,
        matched_codes: Sequence[Dict[str, Any]],
        *,
        portfolio_codes: Set[str],
    ) -> List[Dict[str, Any]]:
        ranked = list(matched_codes)
        for item in ranked:
            code = str(item.get("code") or "")
            boost = 0.0
            if code in portfolio_codes:
                boost += 0.5
            item["score"] = float(item.get("score") or 0) + boost
        ranked.sort(key=lambda row: row.get("score", 0), reverse=True)
        return ranked

    def _filter_by_scope(
        self,
        candidates: Sequence[Dict[str, Any]],
        *,
        config: Any,
    ) -> List[Dict[str, Any]]:
        scope = (getattr(config, "event_push_scope", "watchlist") or "watchlist").strip().lower()
        watchlist = self._watchlist_codes(config)
        if scope == "discover":
            return list(candidates)
        return [item for item in candidates if str(item.get("code") or "") in watchlist]

    def _send_push(self, result: EventDeltaResult, *, title: str, body: str) -> bool:
        from src.notification import NotificationBuilder

        notifier = self._resolve_notifier()
        alert_text = NotificationBuilder.build_simple_alert(
            title=title,
            content=body,
            alert_type="warning" if result.severity == "warning" else "info",
        )
        dispatch = notifier.send_with_results(alert_text, route_type="alert")
        return bool(getattr(dispatch, "success", False) or getattr(dispatch, "dispatched", False))

    def _send_sector_digest(self, *, title: str, body: str) -> bool:
        from src.notification import NotificationBuilder

        notifier = self._resolve_notifier()
        alert_text = NotificationBuilder.build_simple_alert(
            title=title,
            content=body,
            alert_type="info",
        )
        dispatch = notifier.send_with_results(alert_text, route_type="alert")
        return bool(getattr(dispatch, "success", False) or getattr(dispatch, "dispatched", False))

    def _try_sector_resonance_digest(
        self,
        signal: Any,
        *,
        entities: Sequence[str],
        config: Any,
    ) -> Optional[Dict[str, int]]:
        """公告优先走边传导；主题新闻命中板块共振时推 1 条 digest。"""
        if signal.source_type == "announcement":
            return None
        if not getattr(config, "sector_resonance_enabled", False):
            return None

        watchlist = self._watchlist_codes(config)
        resonance = self.sector_service.evaluate(
            entity_ids=entities,
            event_title=signal.title,
            config=config,
            watchlist_codes=watchlist,
            stock_name_resolver=self.stock_name_resolver,
        )
        if resonance is None or not resonance.should_push:
            return None

        body = format_sector_resonance_markdown(resonance)
        title = f"板块共振 | {resonance.sector_name}"
        if not self._send_sector_digest(title=title, body=body):
            return {"analyzed": 0, "pushed": 0, "skipped": 1}

        cooldown_minutes = max(1, int(getattr(config, "event_push_cooldown_minutes", 45) or 45))
        self.sector_service.sector_cooldown_repo.set_cooldown(
            resonance.sector_name,
            cooldown_minutes=cooldown_minutes,
            event_signal_id=signal.id,
            reason="sector_resonance_digest",
        )
        self.signal_repo.update_resonance_sector(
            signal.id,
            resonance_sector=resonance.sector_name,
            status="pushed",
        )
        return {"analyzed": 1, "pushed": 1, "skipped": 0}

    def process_signal(
        self,
        signal: Any,
        *,
        config: Any,
        force: bool = False,
    ) -> Dict[str, int]:
        stats = {"analyzed": 0, "pushed": 0, "skipped": 0}
        if not getattr(config, "exposure_graph_enabled", False):
            self.signal_repo.update_status(signal.id, status="skipped", skip_reason="graph_disabled")
            stats["skipped"] += 1
            return stats

        if not getattr(config, "event_delta_analysis_enabled", False) and not force:
            stats["skipped"] += 1
            return stats

        if not self._session_allows_push(config, force=force):
            self.signal_repo.update_status(
                signal.id,
                status="skipped",
                skip_reason="non_trading_hours",
            )
            stats["skipped"] += 1
            return stats

        try:
            entities = json.loads(signal.entities_json or "[]")
        except json.JSONDecodeError:
            entities = []
        try:
            matched_codes = json.loads(signal.matched_codes_json or "[]")
        except json.JSONDecodeError:
            matched_codes = []

        if not matched_codes:
            self.signal_repo.update_status(signal.id, status="skipped", skip_reason="no_candidates")
            stats["skipped"] += 1
            return stats

        sector_stats = self._try_sector_resonance_digest(
            signal,
            entities=entities,
            config=config,
        )
        if sector_stats is not None and sector_stats.get("pushed"):
            stats["analyzed"] += sector_stats.get("analyzed", 0)
            stats["pushed"] += sector_stats.get("pushed", 0)
            return stats

        portfolio_codes = self._portfolio_codes()
        ranked = self._rank_candidates(matched_codes, portfolio_codes=portfolio_codes)
        scoped = self._filter_by_scope(ranked, config=config)
        max_stocks = max(1, int(getattr(config, "event_analysis_max_stocks", 5) or 5))
        scoped = scoped[:max_stocks]

        if not scoped:
            self.signal_repo.update_status(
                signal.id,
                status="skipped",
                skip_reason="out_of_push_scope",
            )
            stats["skipped"] += 1
            return stats

        min_confidence = getattr(config, "event_push_min_confidence", "medium") or "medium"
        cooldown_minutes = max(1, int(getattr(config, "event_push_cooldown_minutes", 45) or 45))
        pushed_any = False

        for candidate in scoped:
            code = str(candidate.get("code") or "").strip()
            if not code:
                continue
            if self.cooldown_repo.is_in_cooldown(code):
                stats["skipped"] += 1
                continue

            stats["analyzed"] += 1
            result = self.analysis_service.analyze_candidate(
                code=code,
                event_title=signal.title,
                source_type=signal.source_type,
                entities=entities,
                edge_payload=candidate,
                snippet=signal.snippet,
                config=config,
                stock_name=self.stock_name_resolver(code),
            )

            if not result.should_push:
                stats["skipped"] += 1
                continue
            if not _confidence_meets_minimum(result.confidence, min_confidence):
                stats["skipped"] += 1
                continue
            if result.vs_baseline == "基线过期":
                stats["skipped"] += 1
                continue

            body = format_event_push_markdown(
                result,
                event_title=signal.title,
                source_type_label=_SOURCE_LABELS.get(signal.source_type, signal.source_type),
            )
            title = f"事件增量 | {result.name}({result.code})"
            if self._send_push(result, title=title, body=body):
                self.cooldown_repo.set_cooldown(
                    code,
                    cooldown_minutes=cooldown_minutes,
                    event_signal_id=signal.id,
                    reason="event_delta_push",
                )
                pushed_any = True
                stats["pushed"] += 1
            else:
                stats["skipped"] += 1

        if pushed_any:
            self.signal_repo.update_status(signal.id, status="pushed")
        else:
            self.signal_repo.update_status(
                signal.id,
                status="skipped",
                skip_reason="gate_or_cooldown",
            )
            stats["skipped"] += 1
        return stats

    def process_pending(
        self,
        config: Any,
        *,
        limit: int = 20,
        force: bool = False,
    ) -> Dict[str, int]:
        totals = {
            "signals": 0,
            "analyzed": 0,
            "pushed": 0,
            "skipped": 0,
        }
        pending = self.signal_repo.list_by_status("pending", limit=limit)
        for signal in pending:
            totals["signals"] += 1
            result = self.process_signal(signal, config=config, force=force)
            totals["analyzed"] += result.get("analyzed", 0)
            totals["pushed"] += result.get("pushed", 0)
            totals["skipped"] += result.get("skipped", 0)
        return totals
