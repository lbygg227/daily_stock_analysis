# -*- coding: utf-8 -*-
"""Integration tests for fundamentals browser API."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from api.app import create_app
from src.config import Config
from src.repositories.fundamental_repo import UNCLASSIFIED_INDUSTRY_KEY
from src.storage import DatabaseManager, FinancialAbstract, StockListing


class FundamentalsApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "fundamentals_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self._seed_data()
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def _seed_data(self) -> None:
        with self.db.get_session() as session:
            session.add_all(
                [
                    StockListing(
                        code="600519",
                        name="贵州茅台",
                        market="SH",
                        industry_ths="白酒",
                        status="listed",
                        updated_at=datetime(2026, 6, 1, 2, 0, 0),
                    ),
                    StockListing(
                        code="000001",
                        name="平安银行",
                        market="SZ",
                        industry_ths="银行",
                        status="listed",
                        updated_at=datetime(2026, 6, 1, 2, 0, 0),
                    ),
                    StockListing(
                        code="430047",
                        name="诺思兰德",
                        market="BJ",
                        industry_ths=None,
                        status="listed",
                        updated_at=datetime(2026, 6, 1, 2, 0, 0),
                    ),
                ]
            )
            session.add_all(
                [
                    FinancialAbstract(
                        code="600519",
                        report_period=date(2025, 12, 31),
                        revenue=100.0,
                        revenue_yoy=5.0,
                        net_margin=50.0,
                        roe=30.0,
                        source="THS",
                    ),
                    FinancialAbstract(
                        code="000001",
                        report_period=date(2025, 9, 30),
                        revenue=80.0,
                        revenue_yoy=2.0,
                        net_margin=20.0,
                        roe=10.0,
                        source="THS",
                    ),
                ]
            )
            session.commit()

    def test_get_cache_stats(self) -> None:
        response = self.client.get("/api/v1/fundamentals/stats")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["listed_count"], 3)
        self.assertEqual(payload["financial_coverage_count"], 2)
        self.assertEqual(payload["industry_coverage_count"], 2)
        self.assertEqual(payload["latest_financial_report_period"], "2025-12-31")

    def test_list_industries_includes_unclassified_bucket(self) -> None:
        response = self.client.get("/api/v1/fundamentals/industries")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = {item["name"] for item in payload["items"]}
        self.assertIn("白酒", names)
        self.assertIn("银行", names)
        self.assertIn(UNCLASSIFIED_INDUSTRY_KEY, names)

    def test_list_stocks_supports_fuzzy_search_and_financial_columns(self) -> None:
        response = self.client.get(
            "/api/v1/fundamentals/stocks",
            params={"search": "茅台"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        item = payload["items"][0]
        self.assertEqual(item["code"], "600519")
        self.assertTrue(item["has_financial"])
        self.assertEqual(item["revenue"], 100.0)
        self.assertEqual(item["roe"], 30.0)

    def test_list_stocks_supports_industry_exact_filter(self) -> None:
        response = self.client.get(
            "/api/v1/fundamentals/stocks",
            params={"industry": "银行", "industry_exact": True},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["code"], "000001")

    def test_list_stocks_supports_sort_by_roe_desc(self) -> None:
        response = self.client.get(
            "/api/v1/fundamentals/stocks",
            params={"sort_by": "roe", "sort_order": "desc", "limit": 10},
        )
        self.assertEqual(response.status_code, 200)
        codes = [item["code"] for item in response.json()["items"] if item.get("roe") is not None]
        self.assertEqual(codes[0], "600519")


if __name__ == "__main__":
    unittest.main()
