# -*- coding: utf-8 -*-
"""Tests for exposure event ingest and worker (Phase 2a)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from src.config import Config
from src.repositories.event_signal_repo import EventSignalRepository
from src.repositories.exposure_repo import ExposureRepository
from src.search_service import SearchResponse, SearchResult
from src.services.exposure_event_ingest import ExposureEventIngestService
from src.services.exposure_event_worker import ExposureEventWorker
from src.services.theme_pack_importer import import_theme_pack, resolve_theme_pack_path
from src.storage import DatabaseManager


class ExposureEventIngestTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "event_ingest_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.exposure_repo = ExposureRepository(self.db)
        self.signal_repo = EventSignalRepository(self.db)
        import_theme_pack(
            path=resolve_theme_pack_path(pack_id="changxin_chain"),
            repo=self.exposure_repo,
        )

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def test_process_item_hits_changxin_chain(self) -> None:
        service = ExposureEventIngestService(
            exposure_repo=self.exposure_repo,
            signal_repo=self.signal_repo,
        )
        signal_id = service.process_item(
            source_type="news",
            title="长鑫扩产带动国产存储景气提升",
            source_url="https://example.com/news/changxin-1",
            snippet="存储产业链关注升温",
            force=True,
        )
        self.assertIsNotNone(signal_id)
        rows, total = self.signal_repo.list_signals()
        self.assertEqual(total, 1)
        payload = EventSignalRepository.to_dict(rows[0])
        self.assertIn("changxin", payload["entities"])
        codes = [item["code"] for item in payload["matched_codes"]]
        self.assertIn("002208", codes)

    def test_url_dedup(self) -> None:
        service = ExposureEventIngestService(
            exposure_repo=self.exposure_repo,
            signal_repo=self.signal_repo,
        )
        first = service.process_item(
            source_type="news",
            title="长鑫扩产",
            source_url="https://example.com/news/dup",
            force=True,
        )
        second = service.process_item(
            source_type="news",
            title="长鑫扩产",
            source_url="https://example.com/news/dup",
            force=True,
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_worker_skips_outside_session(self) -> None:
        config = MagicMock()
        config.exposure_event_worker_enabled = True
        config.theme_news_ingest_enabled = True
        config.announcement_monitor_enabled = False
        config.exposure_event_ingest_outside_session = False
        config.exposure_ingest_query_mode = "graph"
        config.exposure_ingest_max_queries = 20
        config.theme_news_keywords = ""

        worker = ExposureEventWorker(
            config_provider=lambda: config,
            ingest_service=ExposureEventIngestService(
                exposure_repo=self.exposure_repo,
                signal_repo=self.signal_repo,
            ),
        )

        class _Phase:
            is_market_open_now = False

        with unittest.mock.patch(
            "src.services.exposure_event_worker.build_market_phase_context",
            return_value=_Phase(),
        ):
            stats = worker.run_once()

        self.assertEqual(stats["session_skipped"], 1)
        self.assertEqual(stats["inserted"], 0)

    def test_worker_runs_with_force(self) -> None:
        def _fake_search(keyword: str, *, max_results: int = 5) -> SearchResponse:
            return SearchResponse(
                query=keyword,
                provider="Fake",
                success=True,
                results=[
                    SearchResult(
                        title="长鑫存储扩产计划公布",
                        snippet="产业链受益",
                        url="https://example.com/news/changxin-force",
                        source="Fake",
                        published_date=datetime.now(timezone.utc),
                    )
                ],
            )

        config = MagicMock()
        config.exposure_event_worker_enabled = True
        config.theme_news_ingest_enabled = True
        config.announcement_monitor_enabled = False
        config.exposure_event_ingest_outside_session = False
        config.theme_news_keywords = "长鑫"
        config.exposure_ingest_query_mode = "graph"
        config.exposure_ingest_max_queries = 20
        config.stock_list = []
        config.event_delta_analysis_enabled = False

        service = ExposureEventIngestService(
            exposure_repo=self.exposure_repo,
            signal_repo=self.signal_repo,
            search_fn=_fake_search,
        )
        worker = ExposureEventWorker(
            config_provider=lambda: config,
            ingest_service=service,
        )
        stats = worker.run_once(force=True)
        self.assertEqual(stats["inserted"], 1)
        rows, _ = self.signal_repo.list_signals()
        self.assertEqual(rows[0].status, "pending")


if __name__ == "__main__":
    unittest.main()
