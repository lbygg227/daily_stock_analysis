# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.local_fundamental_provider import (
    LocalFundamentalProvider,
    financial_to_earnings_payload,
    financial_to_growth_payload,
    is_financial_record_fresh,
)


class TestLocalFundamentalProvider(unittest.TestCase):
    def test_is_financial_record_fresh_uses_report_period(self) -> None:
        financial = SimpleNamespace(report_period=date(2026, 1, 1))
        self.assertTrue(
            is_financial_record_fresh(
                financial,
                max_age_days=180,
                today=date(2026, 6, 1),
            )
        )
        self.assertFalse(
            is_financial_record_fresh(
                financial,
                max_age_days=30,
                today=date(2026, 6, 1),
            )
        )

    def test_financial_payload_mapping(self) -> None:
        financial = SimpleNamespace(
            report_period=date(2025, 12, 31),
            revenue_yoy=12.5,
            net_profit_yoy=8.0,
            roe=18.2,
            gross_margin=55.0,
            revenue=100.0,
            net_profit=20.0,
            operating_cf_ps=1.5,
        )
        growth = financial_to_growth_payload(financial)
        earnings = financial_to_earnings_payload(financial)

        self.assertEqual(growth["revenue_yoy"], 12.5)
        self.assertEqual(earnings["financial_report"]["report_date"], "2025-12-31")
        self.assertEqual(earnings["financial_report"]["net_profit_parent"], 20.0)

    def test_get_local_fundamental_bundle_returns_none_when_missing(self) -> None:
        repo = MagicMock()
        repo.get_latest_financial.return_value = None
        provider = LocalFundamentalProvider(repo=repo)

        self.assertIsNone(provider.get_local_fundamental_bundle("600519"))

    def test_get_local_fundamental_bundle_returns_growth_and_earnings(self) -> None:
        financial = SimpleNamespace(
            report_period=date.today(),
            revenue_yoy=10.0,
            net_profit_yoy=5.0,
            roe=12.0,
            gross_margin=30.0,
            revenue=100.0,
            net_profit=10.0,
            operating_cf_ps=1.0,
        )
        listing = SimpleNamespace(industry_ths="白酒", sector_name=None)
        repo = MagicMock()
        repo.get_latest_financial.return_value = financial
        repo.get_stock_listing_by_code.return_value = listing
        provider = LocalFundamentalProvider(repo=repo)

        bundle = provider.get_local_fundamental_bundle("600519", max_age_days=180)

        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(bundle["growth"]["roe"], 12.0)
        self.assertEqual(bundle["earnings"]["financial_report"]["revenue"], 100.0)
        self.assertEqual(bundle["boards_hint"]["industry_ths"], "白酒")


if __name__ == "__main__":
    unittest.main()
