# -*- coding: utf-8 -*-
"""API tests for exposure graph endpoints."""

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
from src.services.theme_pack_importer import import_theme_pack, resolve_theme_pack_path
from src.storage import DatabaseManager


class ExposureApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "exposure_api_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        import_theme_pack(path=resolve_theme_pack_path(pack_id="changxin_chain"))
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def test_by_entity_returns_hefei_urban_construction(self) -> None:
        response = self.client.get("/api/v1/exposure/by-entity/changxin")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["entity_id"], "changxin")
        self.assertIn("002208", payload["codes"])

    def test_by_code_returns_profile_and_exposures(self) -> None:
        response = self.client.get("/api/v1/exposure/by-code/002208")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], "002208")
        self.assertIsNotNone(payload["profile"])
        self.assertGreaterEqual(len(payload["exposures"]), 1)


if __name__ == "__main__":
    unittest.main()
