# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from tests.test_scheduler_background import _FakeScheduleModule


class TestSchedulerExtraDailyTasks(unittest.TestCase):
    def test_add_daily_task_registers_named_job(self) -> None:
        fake_schedule = _FakeScheduleModule()
        with patch.dict(sys.modules, {"schedule": fake_schedule}):
            from src.scheduler import Scheduler

            calls = []
            scheduler = Scheduler(schedule_time="18:00")
            scheduler.add_daily_task(
                lambda: calls.append("sync"),
                "02:00",
                name="fundamental_sync",
                run_immediately=True,
            )

            self.assertEqual(len(fake_schedule.jobs), 1)
            self.assertEqual(calls, ["sync"])

    def test_run_with_schedule_can_skip_primary_daily_task(self) -> None:
        from src import scheduler as scheduler_module

        created = {}

        class FakeScheduler:
            def __init__(self, schedule_time="18:00", schedule_time_provider=None):
                created["instance"] = self
                self.schedule_time = schedule_time

            def add_daily_task(self, **kwargs):
                created["extra"] = kwargs
                kwargs["task"]()

            def set_daily_task(self, task, run_immediately=True):
                created["primary"] = (task, run_immediately)

            def run(self):
                created["ran"] = True

        with patch.object(scheduler_module, "Scheduler", FakeScheduler):
            scheduler_module.run_with_schedule(
                task=None,
                enable_primary_daily_task=False,
                extra_daily_tasks=[
                    {
                        "name": "fundamental_sync",
                        "task": lambda: created.setdefault("extra_ran", True),
                        "schedule_time": "02:00",
                    }
                ],
            )

        self.assertNotIn("primary", created)
        self.assertTrue(created.get("extra_ran"))
        self.assertTrue(created.get("ran"))

    def test_add_weekly_task_registers_named_job(self) -> None:
        fake_schedule = _FakeScheduleModule()
        with patch.dict(sys.modules, {"schedule": fake_schedule}):
            from src.scheduler import Scheduler

            calls = []
            scheduler = Scheduler(schedule_time="18:00")
            scheduler.add_weekly_task(
                lambda: calls.append("industry"),
                "03:00",
                6,
                name="fundamental_industry_sync",
                run_immediately=True,
            )

            self.assertEqual(len(fake_schedule.jobs), 1)
            self.assertEqual(calls, ["industry"])


if __name__ == "__main__":
    unittest.main()
