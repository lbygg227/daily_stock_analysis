# -*- coding: utf-8 -*-
"""Tests for event delta analysis and processor (Phase 3)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from src.config import Config
from src.repositories.event_push_cooldown_repo import EventPushCooldownRepository
from src.repositories.event_signal_repo import EventSignalRepository
from src.repositories.exposure_repo import ExposureRepository
from src.services.event_delta_analysis import EventDeltaAnalysisService
from src.services.event_delta_processor import EventDeltaProcessor
from src.services.theme_pack_importer import import_theme_pack, resolve_theme_pack_path
from src.storage import DatabaseManager


class EventDeltaProcessorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "event_delta_test.db"
        os.environ["DATABASE_PATH"] = str(self._db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.exposure_repo = ExposureRepository(self.db)
        self.signal_repo = EventSignalRepository(self.db)
        self.cooldown_repo = EventPushCooldownRepository(self.db)
        import_theme_pack(
            path=resolve_theme_pack_path(pack_id="changxin_chain"),
            repo=self.exposure_repo,
        )
        self.exposure_repo.upsert_baseline_cache(
            {
                "code": "002208",
                "baseline_history_id": 1,
                "operation_advice": "观望",
                "core_thesis": "主题联动，地产主业偏弱",
                "risks": "地产下行",
                "key_levels": {},
                "price_at_analysis": 10.5,
                "tech_summary": "震荡",
                "exposure_digest": "changxin(equity_investment)",
            }
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
        config.event_push_scope = "watchlist"
        config.stock_list = ["002208"]
        config.event_analysis_max_stocks = 5
        config.event_push_min_confidence = "low"
        config.event_llm_daily_budget = 0
        config.event_delta_analysis_enabled = False
        return config

    def test_heuristic_skips_stale_baseline(self) -> None:
        from src.storage import AnalysisBaselineCache

        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
        with self.db.get_session() as session:
            row = session.get(AnalysisBaselineCache, "002208")
            self.assertIsNotNone(row)
            row.created_at = old_time
            session.commit()

        service = EventDeltaAnalysisService(
            exposure_repo=self.exposure_repo,
            quote_provider=lambda _c: {"current_price": 11.0, "change_percent": 1.2},
        )
        result = service.analyze_candidate(
            code="002208",
            event_title="长鑫扩产带动产业链",
            source_type="news",
            entities=["changxin"],
            edge_payload={"code": "002208", "target_entity_id": "changxin", "link_type": "equity_investment"},
            config=self._config(),
            stock_name="合肥城建",
        )
        self.assertEqual(result.vs_baseline, "基线过期")
        self.assertFalse(result.should_push)

    def test_processor_pushes_with_mock_notifier(self) -> None:
        edges = self.exposure_repo.get_exposures_by_code("002208")
        self.assertTrue(edges)
        edge_id = edges[0].id

        signal_id = self.signal_repo.insert_signal(
            {
                "source_type": "announcement",
                "source_url": "https://example.com/news/delta-1",
                "title": "长鑫扩产计划公布",
                "snippet": "产业链受益",
                "entities": ["changxin"],
                "matched_codes": [
                    {
                        "code": "002208",
                        "edge_id": edge_id,
                        "target_entity_id": "changxin",
                        "link_type": "equity_investment",
                        "score": 2.4,
                    }
                ],
                "status": "pending",
            }
        )
        self.assertIsNotNone(signal_id)

        notifier = MagicMock()
        dispatch = MagicMock(success=True, dispatched=True)
        notifier.send_with_results.return_value = dispatch

        processor = EventDeltaProcessor(
            signal_repo=self.signal_repo,
            cooldown_repo=self.cooldown_repo,
            exposure_repo=self.exposure_repo,
            analysis_service=EventDeltaAnalysisService(
                exposure_repo=self.exposure_repo,
                quote_provider=lambda _c: {"current_price": 11.0, "change_percent": 2.0},
            ),
            notifier=notifier,
            stock_name_resolver=lambda _c: "合肥城建",
        )
        stats = processor.process_pending(self._config(), force=True)
        self.assertGreaterEqual(stats["pushed"], 1)
        rows, _ = self.signal_repo.list_signals(status="pushed")
        self.assertEqual(len(rows), 1)
        self.assertTrue(self.cooldown_repo.is_in_cooldown("002208"))
        notifier.send_with_results.assert_called_once()

    def test_cooldown_blocks_second_push(self) -> None:
        self.cooldown_repo.set_cooldown("002208", cooldown_minutes=60, reason="test")
        self.signal_repo.insert_signal(
            {
                "source_type": "news",
                "source_url": "https://example.com/news/delta-2",
                "title": "长鑫再扩产",
                "entities": ["changxin"],
                "matched_codes": [{"code": "002208", "score": 2.0}],
                "status": "pending",
            }
        )
        notifier = MagicMock()
        processor = EventDeltaProcessor(
            signal_repo=self.signal_repo,
            cooldown_repo=self.cooldown_repo,
            exposure_repo=self.exposure_repo,
            analysis_service=EventDeltaAnalysisService(
                exposure_repo=self.exposure_repo,
                quote_provider=lambda _c: {"current_price": 11.0, "change_percent": 1.0},
            ),
            notifier=notifier,
            stock_name_resolver=lambda _c: "合肥城建",
        )
        stats = processor.process_pending(self._config(), force=True)
        self.assertEqual(stats["pushed"], 0)
        notifier.send_with_results.assert_not_called()


if __name__ == "__main__":
    unittest.main()
