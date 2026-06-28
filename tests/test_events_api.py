# -*- coding: utf-8 -*-
"""API tests for event inbox."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from api.app import create_app
from src.config import Config
from src.repositories.event_signal_repo import EventSignalRepository
from src.repositories.exposure_repo import ExposureRepository
from src.services.exposure_event_ingest import ExposureEventIngestService
from src.services.theme_pack_importer import import_theme_pack, resolve_theme_pack_path
from src.storage import DatabaseManager


class EventsApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "events_api_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        exposure_repo = ExposureRepository(self.db)
        import_theme_pack(
            path=resolve_theme_pack_path(pack_id="changxin_chain"),
            repo=exposure_repo,
        )
        ExposureEventIngestService(
            exposure_repo=exposure_repo,
            signal_repo=EventSignalRepository(self.db),
        ).process_item(
            source_type="news",
            title="长鑫扩产",
            source_url="https://example.com/api-test",
            force=True,
        )
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def test_list_event_signals(self) -> None:
        response = self.client.get("/api/v1/events/signals")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["total"], 1)
        self.assertIn("002208", str(payload["items"][0]["matched_codes"]))


if __name__ == "__main__":
    unittest.main()
