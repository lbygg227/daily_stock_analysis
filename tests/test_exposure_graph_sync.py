# -*- coding: utf-8 -*-
"""Tests for graph-driven ingest query resolution."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.config import Config
from src.repositories.exposure_repo import ExposureRepository
from src.services.exposure_event_ingest import ExposureEventIngestService
from src.services.exposure_graph_sync import ExposureGraphSyncService
from src.services.theme_pack_importer import import_theme_pack, resolve_theme_pack_path
from src.storage import DatabaseManager


class ExposureGraphSyncTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "graph_sync_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = ExposureRepository(self.db)
        import_theme_pack(
            path=resolve_theme_pack_path(pack_id="changxin_chain"),
            repo=self.repo,
        )
        self.sync = ExposureGraphSyncService(self.repo)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def test_build_queries_from_graph_not_from_env(self) -> None:
        config = MagicMock()
        config.exposure_ingest_query_mode = "graph"
        config.theme_news_keywords = "完全无关的关键词,另一个无关词"
        config.exposure_ingest_max_queries = 20

        service = ExposureEventIngestService(exposure_repo=self.repo)
        queries = service.resolve_ingest_queries(config)

        self.assertTrue(any("长鑫" in q for q in queries))
        self.assertFalse(any("完全无关" in q for q in queries))

    def test_env_keywords_only_in_keywords_mode(self) -> None:
        config = MagicMock()
        config.exposure_ingest_query_mode = "keywords"
        config.theme_news_keywords = "唯一关键词"
        config.exposure_ingest_max_queries = 20

        graph_queries = self.sync.build_ingest_queries_from_graph(max_queries=5)
        merged = ExposureGraphSyncService.resolve_ingest_query_lists(
            config,
            graph_queries,
        )
        self.assertEqual(merged, ["唯一关键词"])

    def test_ensure_entity_aliases_from_exposures(self) -> None:
        # changxin_chain import already creates aliases; add edge-only entity
        self.repo.upsert_company_exposure(
            {
                "code": "002208",
                "target_entity_id": "orphan_theme",
                "link_type": "concept",
                "strength": "low",
                "direction": "positive",
                "pricing_driver": "theme_overlay",
                "summary": "test",
                "source": "manual",
            }
        )
        created = self.sync.ensure_entity_aliases_from_exposures()
        self.assertGreaterEqual(created, 0)
        self.assertIsNotNone(self.repo.get_entity_alias("orphan_theme"))


if __name__ == "__main__":
    unittest.main()
