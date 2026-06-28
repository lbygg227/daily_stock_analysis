# -*- coding: utf-8 -*-
"""Tests for announcement-based exposure edge extraction."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.config import Config
from src.repositories.exposure_repo import ExposureRepository
from src.services.exposure_edge_extractor import ExposureEdgeExtractor
from src.services.theme_pack_importer import import_theme_pack, resolve_theme_pack_path
from src.storage import DatabaseManager


class ExposureEdgeExtractorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "extractor_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = ExposureRepository(self.db)
        import_theme_pack(
            path=resolve_theme_pack_path(pack_id="changxin_chain"),
            repo=self.repo,
        )
        self.extractor = ExposureEdgeExtractor(exposure_repo=self.repo)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def test_extract_equity_investment_links_to_existing_entity(self) -> None:
        text = "关于参股长鑫存储项目进展的公告"
        items = self.extractor.extract_from_text(
            code="002208",
            text=text,
            source_ref="https://example.com/ann/1",
        )
        self.assertGreaterEqual(len(items), 1)
        self.assertEqual(items[0].target_entity_id, "changxin")
        saved = self.extractor.persist_extractions(items)
        self.assertGreaterEqual(saved, 1)
        edges = self.repo.get_exposures_by_code("002208")
        sources = {e.source for e in edges}
        self.assertIn("announcement", sources)

    def test_extract_supply_chain_creates_entity(self) -> None:
        text = "与某某科技签订重大采购合同"
        items = self.extractor.extract_from_text(
            code="600519",
            text=text,
            source_ref="ann-2",
        )
        self.assertGreaterEqual(len(items), 1)
        self.assertEqual(items[0].link_type, "supply_chain")
        self.extractor.persist_extractions(items)
        self.assertGreater(len(self.repo.list_entity_aliases()), 2)


if __name__ == "__main__":
    unittest.main()
