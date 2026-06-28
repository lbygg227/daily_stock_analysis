# -*- coding: utf-8 -*-
"""API tests for exposure graph operations (Phase 5)."""

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
from src.repositories.exposure_repo import ExposureRepository
from src.services.theme_pack_importer import import_theme_pack, resolve_theme_pack_path
from src.storage import DatabaseManager


class ExposureOpsApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "exposure_ops_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = ExposureRepository(self.db)
        import_theme_pack(path=resolve_theme_pack_path(pack_id="changxin_chain"))
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def test_list_edges_and_disable_feedback(self) -> None:
        response = self.client.get("/api/v1/exposure/edges")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["total"], 1)
        edge = next(item for item in payload["items"] if item["code"] == "002208")
        edge_id = edge["id"]

        fb = self.client.post(
            f"/api/v1/exposure/edges/{edge_id}/feedback",
            json={"feedback_type": "inaccurate", "note": "关联不准"},
        )
        self.assertEqual(fb.status_code, 200)

        by_entity = self.client.get("/api/v1/exposure/by-entity/changxin")
        self.assertEqual(by_entity.status_code, 200)
        codes = by_entity.json()["codes"]
        self.assertNotIn("002208", codes)

        with_disabled = self.client.get(
            "/api/v1/exposure/edges",
            params={"include_disabled": True},
        )
        disabled_item = next(
            item for item in with_disabled.json()["items"] if item["id"] == edge_id
        )
        self.assertTrue(disabled_item["is_disabled"])

    def test_patch_edge_strength(self) -> None:
        edges = self.client.get("/api/v1/exposure/edges", params={"code": "002208"})
        edge_id = edges.json()["items"][0]["id"]
        patch = self.client.patch(
            f"/api/v1/exposure/edges/{edge_id}",
            json={"strength": "high", "summary": "人工校对"},
        )
        self.assertEqual(patch.status_code, 200)
        detail = self.client.get("/api/v1/exposure/edges", params={"code": "002208"})
        item = next(i for i in detail.json()["items"] if i["id"] == edge_id)
        self.assertEqual(item["strength"], "high")


if __name__ == "__main__":
    unittest.main()
