# -*- coding: utf-8 -*-
"""
事件增量分析（Phase 3）。

轻量 delta：读取基线 + 暴露边 + 行情，规则或 LLM 输出结构化结论。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from json_repair import repair_json

from src.repositories.exposure_repo import ExposureRepository

logger = logging.getLogger(__name__)

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
_POSITIVE_HINTS = ("扩产", "涨价", "紧缺", "中标", "预增", "增持", "合作", "订单", "突破")
_NEGATIVE_HINTS = ("减持", "亏损", "下滑", "处罚", "诉讼", "停产", "下调", "暴雷", "退市")


@dataclass
class EventDeltaResult:
    code: str
    name: str = ""
    verdict: str = "不确定"
    vs_baseline: str = "维持"
    transmission_chain: str = ""
    confidence: str = "low"
    priced_in: bool = False
    invalidation: str = ""
    should_push: bool = False
    severity: str = "info"
    one_line_reason: str = ""
    baseline_age_days: Optional[int] = None
    used_llm: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _baseline_age_days(created_at: Optional[datetime], *, now: Optional[datetime] = None) -> Optional[int]:
    if created_at is None:
        return None
    current = now or _utc_now_naive()
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    return max(0, (current.date() - created_at.date()).days)


def _confidence_meets_minimum(confidence: str, minimum: str) -> bool:
    return _CONFIDENCE_RANK.get(confidence, 0) >= _CONFIDENCE_RANK.get(minimum, 2)


def _infer_sentiment_from_title(title: str) -> str:
    text = str(title or "")
    if any(hint in text for hint in _NEGATIVE_HINTS):
        return "negative"
    if any(hint in text for hint in _POSITIVE_HINTS):
        return "positive"
    return "neutral"


def _verdict_from_sentiment(sentiment: str, direction: str) -> str:
    if sentiment == "negative":
        return "利空"
    if sentiment == "positive":
        return "利好"
    if direction == "negative":
        return "利空"
    if direction == "positive":
        return "利好"
    return "中性"


def _vs_baseline_label(age_days: Optional[int], *, stale_days: int, max_age_days: int) -> str:
    if age_days is None:
        return "基线过期"
    if age_days > max_age_days:
        return "基线过期"
    if age_days > stale_days:
        return "基线偏旧"
    return "维持"


class EventDeltaAnalysisService:
    """对单股候选生成增量研判。"""

    def __init__(
        self,
        *,
        exposure_repo: Optional[ExposureRepository] = None,
        quote_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        llm_caller: Optional[Callable[[str], str]] = None,
    ):
        self.exposure_repo = exposure_repo or ExposureRepository()
        self.quote_provider = quote_provider
        self.llm_caller = llm_caller
        self._llm_calls_today = 0
        self._llm_budget_date = _utc_now_naive().date()

    def _resolve_quote_provider(self):
        if self.quote_provider is not None:
            return self.quote_provider

        def _quote(code: str) -> Optional[Dict[str, Any]]:
            try:
                from src.services.stock_service import StockService

                return StockService().get_realtime_quote(code)
            except Exception as exc:
                logger.debug("[EventDelta] quote failed for %s: %s", code, exc)
                return None

        return _quote

    def _reset_llm_budget_if_needed(self) -> None:
        today = _utc_now_naive().date()
        if today != self._llm_budget_date:
            self._llm_budget_date = today
            self._llm_calls_today = 0

    def _can_call_llm(self, config: Any) -> bool:
        if not getattr(config, "event_delta_analysis_enabled", False):
            return False
        self._reset_llm_budget_if_needed()
        raw_budget = getattr(config, "event_llm_daily_budget", 100)
        budget = max(0, int(raw_budget if raw_budget is not None else 100))
        return self._llm_calls_today < budget

    def _build_transmission_chain(
        self,
        *,
        entity_ids: Sequence[str],
        code: str,
        edge_payload: Optional[Dict[str, Any]],
    ) -> str:
        entity_label = ", ".join(entity_ids) if entity_ids else "主题"
        if edge_payload:
            link = edge_payload.get("link_type") or "exposure"
            target = edge_payload.get("target_entity_id") or ""
            return f"{entity_label} → {link}({target}) → {code}"
        return f"{entity_label} → {code}"

    def _heuristic_analyze(
        self,
        *,
        code: str,
        name: str,
        event_title: str,
        source_type: str,
        entities: Sequence[str],
        edge_payload: Optional[Dict[str, Any]],
        baseline: Any,
        quote: Optional[Dict[str, Any]],
        config: Any,
    ) -> EventDeltaResult:
        stale_days = max(1, int(getattr(config, "event_baseline_stale_days", 3) or 3))
        max_age_days = max(stale_days + 1, int(getattr(config, "event_baseline_max_age_days", 7) or 7))
        age_days = _baseline_age_days(getattr(baseline, "created_at", None))
        vs_baseline = _vs_baseline_label(age_days, stale_days=stale_days, max_age_days=max_age_days)

        direction = "positive"
        strength = "medium"
        if edge_payload:
            edge = self.exposure_repo.get_exposure_by_id(edge_payload.get("edge_id"))
            if edge:
                direction = edge.direction or "positive"
                strength = edge.strength or "medium"

        sentiment = _infer_sentiment_from_title(event_title)
        verdict = _verdict_from_sentiment(sentiment, direction)

        if source_type == "announcement":
            confidence = "high" if strength == "high" else "medium"
        elif strength == "high":
            confidence = "medium"
        else:
            confidence = "low"

        change_pct = quote.get("change_percent") if quote else None
        priced_in = change_pct is not None and abs(float(change_pct)) >= 5.0

        if vs_baseline == "基线过期":
            one_line = "无有效分析基线，建议先跑全量分析"
            should_push = False
        elif age_days is not None and age_days > max_age_days:
            one_line = "基线超过 7 天，不推送操作建议变化"
            should_push = False
        else:
            one_line = f"事件或主题传导，相对上次观点{vs_baseline}"
            should_push = True

        if priced_in and should_push:
            one_line = f"{one_line}；短线或已部分定价"
            if confidence == "low":
                should_push = False

        severity = "warning" if confidence == "high" else "info"
        return EventDeltaResult(
            code=code,
            name=name,
            verdict=verdict,
            vs_baseline=vs_baseline,
            transmission_chain=self._build_transmission_chain(
                entity_ids=entities,
                code=code,
                edge_payload=edge_payload,
            ),
            confidence=confidence,
            priced_in=priced_in,
            invalidation="跌破关键支撑或事件被证伪",
            should_push=should_push,
            severity=severity,
            one_line_reason=one_line,
            baseline_age_days=age_days,
            used_llm=False,
            extra={
                "price": quote.get("current_price") if quote else None,
                "change_pct": change_pct,
                "core_thesis": getattr(baseline, "core_thesis", None),
            },
        )

    def _build_llm_prompt(
        self,
        *,
        code: str,
        name: str,
        event_title: str,
        snippet: Optional[str],
        entities: Sequence[str],
        edge_payload: Optional[Dict[str, Any]],
        baseline: Any,
        quote: Optional[Dict[str, Any]],
    ) -> str:
        baseline_block = {
            "operation_advice": getattr(baseline, "operation_advice", None),
            "core_thesis": getattr(baseline, "core_thesis", None),
            "risks": getattr(baseline, "risks", None),
            "price_at_analysis": getattr(baseline, "price_at_analysis", None),
            "exposure_digest": getattr(baseline, "exposure_digest", None),
            "baseline_age_days": _baseline_age_days(getattr(baseline, "created_at", None)),
        }
        return (
            "你是 A 股盘中事件增量分析助手。基于下列输入输出 JSON，不要 markdown。\n"
            "字段：verdict(利好/利空/中性/不确定), vs_baseline(维持/上调/下调/基线偏旧/基线过期), "
            "transmission_chain, confidence(high/medium/low), priced_in(bool), "
            "invalidation, should_push(bool), severity(info/warning/critical), one_line_reason。\n"
            f"股票：{code} {name}\n"
            f"事件标题：{event_title}\n"
            f"摘要：{snippet or ''}\n"
            f"命中实体：{list(entities)}\n"
            f"暴露边：{edge_payload or {}}\n"
            f"基线：{json.dumps(baseline_block, ensure_ascii=False)}\n"
            f"现价：{quote}\n"
        )

    def _parse_llm_json(self, text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                return json.loads(repair_json(raw))
            except Exception:
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if not match:
                    return {}
                try:
                    return json.loads(repair_json(match.group(0)))
                except Exception:
                    return {}

    def _call_llm(self, config: Any, prompt: str) -> str:
        from src.config import get_configured_llm_models, resolve_litellm_wire_model
        from src.llm.errors import call_litellm_with_param_recovery
        import litellm

        model_override = (getattr(config, "event_delta_analysis_model", None) or "").strip()
        if model_override:
            model = resolve_litellm_wire_model(model_override)
        else:
            models = get_configured_llm_models(getattr(config, "llm_model_list", None) or [])
            if not models:
                raise RuntimeError("no LLM model configured")
            model = resolve_litellm_wire_model(models[0])

        call_kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 800,
        }
        response = call_litellm_with_param_recovery(
            litellm.completion,
            model=model,
            call_kwargs=call_kwargs,
        )
        self._llm_calls_today += 1
        content = response.choices[0].message.content
        return str(content or "")

    def analyze_candidate(
        self,
        *,
        code: str,
        event_title: str,
        source_type: str,
        entities: Sequence[str],
        edge_payload: Optional[Dict[str, Any]],
        snippet: Optional[str] = None,
        config: Any,
        stock_name: Optional[str] = None,
    ) -> EventDeltaResult:
        normalized = code.zfill(6) if code.isdigit() else code
        profile = self.exposure_repo.get_company_profile(normalized)
        name = stock_name or (profile.name if profile and profile.name else normalized)
        baseline = self.exposure_repo.get_baseline_cache(normalized)
        quote = self._resolve_quote_provider()(normalized)

        if self._can_call_llm(config) and self.llm_caller is not None:
            prompt = self._build_llm_prompt(
                code=normalized,
                name=name,
                event_title=event_title,
                snippet=snippet,
                entities=entities,
                edge_payload=edge_payload,
                baseline=baseline,
                quote=quote,
            )
            try:
                raw = self.llm_caller(config, prompt)
                payload = self._parse_llm_json(raw)
                if payload:
                    result = self._heuristic_analyze(
                        code=normalized,
                        name=name,
                        event_title=event_title,
                        source_type=source_type,
                        entities=entities,
                        edge_payload=edge_payload,
                        baseline=baseline,
                        quote=quote,
                        config=config,
                    )
                    result.verdict = str(payload.get("verdict") or result.verdict)
                    result.vs_baseline = str(payload.get("vs_baseline") or result.vs_baseline)
                    result.transmission_chain = str(
                        payload.get("transmission_chain") or result.transmission_chain
                    )
                    result.confidence = str(payload.get("confidence") or result.confidence)
                    result.priced_in = bool(payload.get("priced_in", result.priced_in))
                    result.invalidation = str(payload.get("invalidation") or result.invalidation)
                    result.should_push = bool(payload.get("should_push", result.should_push))
                    result.severity = str(payload.get("severity") or result.severity)
                    result.one_line_reason = str(
                        payload.get("one_line_reason") or result.one_line_reason
                    )
                    result.used_llm = True
                    return result
            except Exception as exc:
                logger.warning("[EventDelta] LLM failed for %s: %s", normalized, exc)

        if self._can_call_llm(config) and self.llm_caller is None:
            prompt = self._build_llm_prompt(
                code=normalized,
                name=name,
                event_title=event_title,
                snippet=snippet,
                entities=entities,
                edge_payload=edge_payload,
                baseline=baseline,
                quote=quote,
            )
            try:
                raw = self._call_llm(config, prompt)
                payload = self._parse_llm_json(raw)
                if payload:
                    base = self._heuristic_analyze(
                        code=normalized,
                        name=name,
                        event_title=event_title,
                        source_type=source_type,
                        entities=entities,
                        edge_payload=edge_payload,
                        baseline=baseline,
                        quote=quote,
                        config=config,
                    )
                    base.verdict = str(payload.get("verdict") or base.verdict)
                    base.vs_baseline = str(payload.get("vs_baseline") or base.vs_baseline)
                    base.transmission_chain = str(
                        payload.get("transmission_chain") or base.transmission_chain
                    )
                    base.confidence = str(payload.get("confidence") or base.confidence)
                    base.priced_in = bool(payload.get("priced_in", base.priced_in))
                    base.invalidation = str(payload.get("invalidation") or base.invalidation)
                    base.should_push = bool(payload.get("should_push", base.should_push))
                    base.severity = str(payload.get("severity") or base.severity)
                    base.one_line_reason = str(
                        payload.get("one_line_reason") or base.one_line_reason
                    )
                    base.used_llm = True
                    return base
            except Exception as exc:
                logger.warning("[EventDelta] LLM failed for %s: %s", normalized, exc)

        return self._heuristic_analyze(
            code=normalized,
            name=name,
            event_title=event_title,
            source_type=source_type,
            entities=entities,
            edge_payload=edge_payload,
            baseline=baseline,
            quote=quote,
            config=config,
        )


def format_event_push_markdown(
    result: EventDeltaResult,
    *,
    event_title: str,
    source_type_label: str,
) -> str:
    price = result.extra.get("price")
    change_pct = result.extra.get("change_pct")
    price_text = f"{price} ({change_pct}%)" if price is not None and change_pct is not None else (
        str(price) if price is not None else "—"
    )
    core_thesis = result.extra.get("core_thesis") or "—"
    baseline_note = ""
    if result.baseline_age_days is not None:
        baseline_note = f"{result.baseline_age_days} 天前"
    return (
        f"### ⚡ 事件增量 · {result.name}({result.code})\n\n"
        f"**触发**：{event_title}（{source_type_label}）\n\n"
        f"**传导**：{result.transmission_chain}\n"
        f"**相对基线**：{result.vs_baseline} — {result.one_line_reason}\n"
        f"**现价**：{price_text} · 置信度：{result.confidence}\n\n"
        f"> {baseline_note} 基线：{core_thesis}\n"
        f"> 失效：{result.invalidation}\n\n"
        f"*仅供参考，不构成投资建议*"
    )
