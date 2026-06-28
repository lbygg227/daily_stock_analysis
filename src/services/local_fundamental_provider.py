# -*- coding: utf-8 -*-
"""Read slow-changing fundamental blocks from the local SQLite cache."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from src.repositories.fundamental_repo import FundamentalRepository
from src.storage import FinancialAbstract, StockListing

logger = logging.getLogger(__name__)


def _normalize_code(code: str) -> str:
    return str(code or "").strip().split(".")[0]


def is_financial_record_fresh(
    financial: FinancialAbstract,
    *,
    max_age_days: int,
    today: Optional[date] = None,
) -> bool:
    """Return whether the latest report period is still within the allowed age."""
    report_period = financial.report_period
    if report_period is None:
        return False
    current_day = today or date.today()
    age_days = (current_day - report_period).days
    return age_days <= max(1, max_age_days)


def financial_to_growth_payload(financial: FinancialAbstract) -> Dict[str, Any]:
    """Map ORM financial row to the growth block payload shape."""
    return {
        "revenue_yoy": financial.revenue_yoy,
        "net_profit_yoy": financial.net_profit_yoy,
        "roe": financial.roe,
        "gross_margin": financial.gross_margin,
    }


def financial_to_earnings_payload(financial: FinancialAbstract) -> Dict[str, Any]:
    """Map ORM financial row to the earnings block payload shape."""
    report_date = (
        financial.report_period.isoformat()
        if financial.report_period is not None
        else None
    )
    return {
        "financial_report": {
            "report_date": report_date,
            "revenue": financial.revenue,
            "net_profit_parent": financial.net_profit,
            "operating_cash_flow": financial.operating_cf_ps,
            "roe": financial.roe,
        }
    }


def listing_to_boards_payload(listing: StockListing) -> Dict[str, Any]:
    """Expose persisted industry classification as a lightweight boards hint."""
    industry = (listing.industry_ths or listing.sector_name or "").strip()
    if not industry:
        return {}
    return {
        "industry_ths": industry,
        "source": "local_sqlite",
    }


class LocalFundamentalProvider:
    """Load slow-changing fundamental blocks from local SQLite."""

    def __init__(self, repo: Optional[FundamentalRepository] = None):
        self.repo = repo or FundamentalRepository()

    def get_stock_listing(self, code: str) -> Optional[StockListing]:
        return self.repo.get_stock_listing_by_code(_normalize_code(code))

    def get_local_fundamental_bundle(
        self,
        code: str,
        *,
        max_age_days: int = 180,
    ) -> Optional[Dict[str, Any]]:
        """Return growth/earnings blocks from local DB when data is fresh enough."""
        normalized_code = _normalize_code(code)
        financial = self.repo.get_latest_financial(normalized_code)
        if financial is None:
            return None
        if not is_financial_record_fresh(financial, max_age_days=max_age_days):
            logger.debug(
                "[本地基本面] %s 财务数据已过期（报告期 %s）",
                normalized_code,
                financial.report_period,
            )
            return None

        growth = financial_to_growth_payload(financial)
        earnings = financial_to_earnings_payload(financial)
        if not any(value is not None for value in growth.values()):
            return None

        listing = self.repo.get_stock_listing_by_code(normalized_code)
        boards = listing_to_boards_payload(listing) if listing is not None else {}

        return {
            "status": "partial",
            "growth": growth,
            "earnings": earnings,
            "institution": {},
            "boards_hint": boards,
            "source_chain": [
                {
                    "provider": "local_sqlite",
                    "result": "partial",
                    "duration_ms": 0,
                }
            ],
            "errors": [],
        }
