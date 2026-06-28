# -*- coding: utf-8 -*-
"""Tests for fundamental repository partial upsert behavior."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.config import Config
from src.repositories.fundamental_repo import FundamentalRepository, _listing_conflict_updates
from src.storage import DatabaseManager


class FundamentalRepoUpsertTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "fundamental_repo_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = FundamentalRepository(self.db)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def test_industry_update_does_not_clear_name(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.repo.upsert_stock_listing(
            {
                "code": "600519",
                "name": "贵州茅台",
                "market": "SH",
                "status": "listed",
                "updated_at": now,
            }
        )
        self.repo.upsert_stock_listing(
            {
                "code": "600519",
                "industry_ths": "白酒",
                "updated_at": now,
            }
        )

        row = self.repo.get_stock_listing_by_code("600519")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.name, "贵州茅台")
        self.assertEqual(row.industry_ths, "白酒")

    def test_listing_conflict_updates_skips_blank_name(self) -> None:
        updates = _listing_conflict_updates(
            {"code": "600519", "name": "", "industry_ths": "白酒"}
        )
        self.assertNotIn("name", updates)
        self.assertEqual(updates["industry_ths"], "白酒")


if __name__ == "__main__":
    unittest.main()
