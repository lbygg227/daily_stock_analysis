# -*- coding: utf-8 -*-
"""Tests for sector resonance detection (Phase 4)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.config import Config
from src.repositories.event_push_cooldown_repo import EventPushCooldownRepository
from src.repositories.event_signal_repo import EventSignalRepository
from src.repositories.exposure_repo import ExposureRepository
from src.services.event_delta_analysis import EventDeltaAnalysisService
from src.services.event_delta_processor import EventDeltaProcessor
from src.services.sector_resonance_service import SectorResonanceService
from src.services.theme_pack_importer import import_theme_pack, resolve_theme_pack_path
from src.storage import DatabaseManager


class SectorResonanceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "sector_resonance_test.db"
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

    def _config(self) -> MagicMock:
        config = MagicMock()
        config.exposure_graph_enabled = True
        config.event_delta_analysis_enabled = True
        config.sector_resonance_enabled = True
        config.sector_resonance_min_members = 3
        config.sector_resonance_min_up_ratio = 0.6
        config.event_push_scope = "watchlist"
        config.stock_list = ["002208", "600584", "603986"]
        config.event_analysis_max_stocks = 5
        config.event_push_min_confidence = "low"
        config.event_push_cooldown_minutes = 45
        config.event_baseline_stale_days = 3
        config.event_baseline_max_age_days = 7
        config.exposure_event_ingest_outside_session = True
        config.event_llm_daily_budget = 0
        config.event_delta_analysis_enabled = False
        return config

    def test_evaluate_detects_hot_sector(self) -> None:
        service = SectorResonanceService(
            exposure_repo=self.exposure_repo,
            rankings_provider=lambda _n: [
                {"name": "存储芯片", "change_pct": 3.5, "kind": "concept"},
            ],
            quote_provider=lambda code: {
                "stock_code": code,
                "stock_name": code,
                "change_percent": 2.0,
            },
            member_codes_provider=lambda _sector, watchlist: list(watchlist),
        )
        result = service.evaluate(
            entity_ids=["storage_shortage"],
            event_title="存储紧缺预期升温",
            config=self._config(),
            watchlist_codes={"002208", "600584", "603986"},
            stock_name_resolver=lambda c: c,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sector_name, "存储芯片")
        self.assertTrue(result.should_push)

    def test_processor_pushes_sector_digest_instead_of_per_stock(self) -> None:
        signal_id = self.signal_repo.insert_signal(
            {
                "source_type": "news",
                "source_url": "https://example.com/news/sector-1",
                "title": "存储产业链景气上行",
                "entities": ["storage_shortage"],
                "matched_codes": [{"code": "002208", "score": 2.0}],
                "status": "pending",
            }
        )
        self.assertIsNotNone(signal_id)

        sector_service = SectorResonanceService(
            exposure_repo=self.exposure_repo,
            rankings_provider=lambda _n: [
                {"name": "存储芯片", "change_pct": 4.0, "kind": "concept"},
            ],
            quote_provider=lambda code: {
                "stock_code": code,
                "stock_name": "样例",
                "change_percent": 2.5,
            },
            member_codes_provider=lambda _sector, watchlist: list(watchlist),
        )
        notifier = MagicMock()
        notifier.send_with_results.return_value = MagicMock(success=True, dispatched=True)

        processor = EventDeltaProcessor(
            signal_repo=self.signal_repo,
            cooldown_repo=EventPushCooldownRepository(self.db),
            exposure_repo=self.exposure_repo,
            analysis_service=EventDeltaAnalysisService(exposure_repo=self.exposure_repo),
            sector_service=sector_service,
            notifier=notifier,
            stock_name_resolver=lambda _c: "合肥城建",
        )
        stats = processor.process_pending(self._config(), force=True)
        self.assertEqual(stats["pushed"], 1)
        rows, _ = self.signal_repo.list_signals(status="pushed")
        self.assertEqual(rows[0].resonance_sector, "存储芯片")
        notifier.send_with_results.assert_called_once()
        body = notifier.send_with_results.call_args[0][0]
        self.assertIn("板块共振", body)


if __name__ == "__main__":
    unittest.main()
