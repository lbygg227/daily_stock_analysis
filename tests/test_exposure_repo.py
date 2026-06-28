# -*- coding: utf-8 -*-
"""Tests for exposure repository and theme pack import."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.config import Config
from src.repositories.exposure_repo import ExposureRepository
from src.services.baseline_cache_service import BaselineCacheService
from src.services.theme_pack_importer import import_theme_pack, resolve_theme_pack_path
from src.storage import AnalysisHistory, DatabaseManager


class ExposureRepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "exposure_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = ExposureRepository(self.db)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def test_import_changxin_chain_reverse_lookup(self) -> None:
        stats = import_theme_pack(
            path=resolve_theme_pack_path(pack_id="changxin_chain"),
            repo=self.repo,
        )
        self.assertGreater(stats["entity_aliases"], 0)
        self.assertGreater(stats["exposures"], 0)
        self.assertEqual(stats["errors"], 0)

        codes = self.repo.reverse_lookup_codes("changxin")
        self.assertIn("002208", codes)

        entities = self.repo.resolve_entity_ids_from_text("长鑫扩产带动国产存储景气")
        self.assertIn("changxin", entities)

    def test_baseline_cache_upsert(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.db.get_session() as session:
            session.add(
                AnalysisHistory(
                    query_id="q-test",
                    code="002208",
                    name="合肥城建",
                    report_type="detailed",
                    sentiment_score=55,
                    operation_advice="观望",
                    trend_prediction="震荡",
                    analysis_summary="主题联动为主",
                    created_at=now,
                )
            )
            session.commit()

        class _Result:
            code = "002208"
            name = "合肥城建"
            success = True
            operation_advice = "观望"
            analysis_summary = "长鑫主题外溢"
            risk_warning = "地产拖累"
            trend_prediction = "震荡"
            current_price = 9.5

        service = BaselineCacheService(self.db, self.repo)
        self.assertTrue(service.upsert_from_analysis_result(_Result(), "q-test"))
        cached = self.repo.get_baseline_cache("002208")
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached.operation_advice, "观望")
        self.assertIn("长鑫", cached.core_thesis or "")


if __name__ == "__main__":
    unittest.main()
