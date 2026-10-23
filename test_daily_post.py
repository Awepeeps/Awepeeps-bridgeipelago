import asyncio
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.daily_post import (
    DailyPostConfigError,
    DailyPostState,
    next_run_after,
    load_daily_post_settings,
    run_daily_posts,
)

NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parent


class FakeClock:
    def __init__(self, now):
        self.current = now
        self.sleeps = []

    def now(self):
        return self.current

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += dt.timedelta(seconds=seconds)


class SettingsTests(unittest.TestCase):
    def test_missing_or_disabled_config_returns_none(self):
        self.assertIsNone(load_daily_post_settings(None))
        self.assertIsNone(load_daily_post_settings({}))
        self.assertIsNone(load_daily_post_settings({
            "Enabled": False, "Time": "not a time", "Timezone": "not a zone",
        }))

    def test_enabled_config_uses_configured_values(self):
        settings = load_daily_post_settings({
            "Enabled": True, "Time": "23:45", "Timezone": "America/New_York",
        })
        self.assertEqual((settings.hour, settings.minute), (23, 45))
        self.assertEqual(settings.timezone_name, "America/New_York")
        self.assertEqual(settings.timezone, NY)

    def test_enabled_config_defaults_to_midnight_new_york(self):
        settings = load_daily_post_settings({"Enabled": True})
        self.assertEqual((settings.hour, settings.minute), (0, 0))
        self.assertEqual(settings.timezone_name, "America/New_York")

    def test_template_ships_the_feature_disabled(self):
        template = json.loads(
            (ROOT / "config.json.template").read_text(encoding="utf-8")
        )["DailyPostConfig"]
        self.assertIs(template["Enabled"], False)
        self.assertEqual(template["Time"], "00:00")
        self.assertEqual(template["Timezone"], "America/New_York")
        self.assertIsNone(load_daily_post_settings(template))

    def test_malformed_time_reports_a_clear_error(self):
        for value in ("24:00", "7:30", "07:60", "midnight", 730):
            with self.subTest(value=value):
                with self.assertRaises(DailyPostConfigError) as raised:
                    load_daily_post_settings({
                        "Enabled": True, "Time": value,
                        "Timezone": "America/New_York",
                    })
                self.assertIn("DailyPostConfig.Time", str(raised.exception))
                self.assertIn("HH:MM", str(raised.exception))

    def test_malformed_timezone_reports_a_clear_error(self):
        with self.assertRaises(DailyPostConfigError) as raised:
            load_daily_post_settings({
                "Enabled": True, "Time": "00:00", "Timezone": "Mars/Olympus",
            })
        self.assertIn("DailyPostConfig.Timezone", str(raised.exception))
        self.assertIn("IANA", str(raised.exception))

    def test_non_boolean_enabled_and_non_object_config_report_errors(self):
        with self.assertRaises(DailyPostConfigError):
            load_daily_post_settings({"Enabled": "true"})
        with self.assertRaises(DailyPostConfigError):
            load_daily_post_settings(["Enabled"])


class NextRunTests(unittest.TestCase):
    def settings(self, hour=0, minute=0, timezone="America/New_York"):
        return load_daily_post_settings({
            "Enabled": True,
            "Time": "%02d:%02d" % (hour, minute),
            "Timezone": timezone,
        })

    def test_uses_new_york_wall_time_in_winter_and_summer(self):
        winter = next_run_after(
            dt.datetime(2026, 1, 15, 4, 30, tzinfo=UTC), self.settings()
        )
        self.assertEqual(winter, dt.datetime(2026, 1, 15, 5, 0, tzinfo=UTC))

        summer = next_run_after(
            dt.datetime(2026, 7, 15, 4, 30, tzinfo=UTC), self.settings()
        )
        self.assertEqual(summer, dt.datetime(2026, 7, 16, 4, 0, tzinfo=UTC))

    def test_exact_scheduled_time_moves_to_the_next_day(self):
        result = next_run_after(
            dt.datetime(2026, 1, 15, 5, 0, tzinfo=UTC), self.settings()
        )
        self.assertEqual(result, dt.datetime(2026, 1, 16, 5, 0, tzinfo=UTC))

    def test_missing_spring_forward_time_is_skipped(self):
        result = next_run_after(
            dt.datetime(2026, 3, 7, 12, 0, tzinfo=UTC),
            self.settings(hour=2, minute=30),
        )
        # 02:30 does not exist on 2026-03-08 in New York.
        self.assertEqual(result, dt.datetime(2026, 3, 9, 6, 30, tzinfo=UTC))

    def test_repeated_fall_back_time_posts_on_first_occurrence(self):
        result = next_run_after(
            dt.datetime(2026, 10, 31, 12, 0, tzinfo=UTC),
            self.settings(hour=1, minute=30),
        )
        # 01:30 happens twice on 2026-11-01; fold=0 is still EDT.
        self.assertEqual(result, dt.datetime(2026, 11, 1, 5, 30, tzinfo=UTC))

    def test_utc_configuration(self):
        result = next_run_after(
            dt.datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            self.settings(hour=6, minute=15, timezone="UTC"),
        )
        self.assertEqual(result, dt.datetime(2026, 1, 16, 6, 15, tzinfo=UTC))


class StateTests(unittest.TestCase):
    def test_state_round_trips_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "nested" / "DailyPostState.json")
            state = DailyPostState(path)
            self.assertIsNone(state.last_post_date)
            state.mark_posted(dt.date(2026, 10, 20))

            restored = DailyPostState(path)
            self.assertEqual(restored.last_post_date, dt.date(2026, 10, 20))
            self.assertTrue(restored.already_posted(dt.date(2026, 10, 20)))
            self.assertFalse(restored.already_posted(dt.date(2026, 10, 21)))

    def test_corrupt_state_is_ignored_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "DailyPostState.json"
            messages = []
            path.write_text("{not json", encoding="utf-8")
            state = DailyPostState(str(path), log=messages.append)
            self.assertIsNone(state.last_post_date)
            self.assertTrue(messages)

            path.write_text(json.dumps({"LastPostDate": "yesterday"}), encoding="utf-8")
            state = DailyPostState(str(path), log=messages.append)
            self.assertIsNone(state.last_post_date)


class LoopTests(unittest.TestCase):
    def settings(self, hour=0, minute=0):
        return load_daily_post_settings({
            "Enabled": True,
            "Time": "%02d:%02d" % (hour, minute),
            "Timezone": "America/New_York",
        })

    def test_posts_on_each_local_day_and_skips_spring_forward(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(dt.datetime(2026, 3, 7, 5, 0, tzinfo=UTC))
            state = DailyPostState(str(Path(directory) / "state.json"))
            posted = []

            async def post():
                posted.append(clock.now().astimezone(NY).date())
                if len(posted) == 2:
                    raise asyncio.CancelledError

            async def scenario():
                with self.assertRaises(asyncio.CancelledError):
                    await run_daily_posts(
                        self.settings(hour=2, minute=30), state, post,
                        now=clock.now, sleep=clock.sleep,
                    )

            asyncio.run(scenario())
            self.assertEqual(posted, [dt.date(2026, 3, 7), dt.date(2026, 3, 9)])
            self.assertEqual(clock.sleeps, [2.5 * 3600, 47 * 3600])

    def test_startup_does_not_backfill_a_missed_post(self):
        with tempfile.TemporaryDirectory() as directory:
            # One second after today's midnight post time.
            clock = FakeClock(dt.datetime(2026, 1, 15, 5, 0, 1, tzinfo=UTC))
            state = DailyPostState(str(Path(directory) / "state.json"))
            posted = []

            async def post():
                posted.append((clock.now(), clock.now().astimezone(NY).date()))
                raise asyncio.CancelledError

            async def scenario():
                with self.assertRaises(asyncio.CancelledError):
                    await run_daily_posts(
                        self.settings(), state, post,
                        now=clock.now, sleep=clock.sleep,
                    )

            asyncio.run(scenario())
            self.assertEqual(posted[0][1], dt.date(2026, 1, 16))
            self.assertGreater(clock.sleeps[0], 86000)

    def test_resume_after_suspension_delivers_the_missed_post_late(self):
        with tempfile.TemporaryDirectory() as directory:
            # One minute before midnight in New York.
            clock = FakeClock(dt.datetime(2026, 1, 15, 4, 59, tzinfo=UTC))
            state = DailyPostState(str(Path(directory) / "state.json"))
            posted = []

            async def post():
                posted.append(clock.now().astimezone(NY).date())
                raise asyncio.CancelledError

            async def suspended_sleep(seconds):
                clock.sleeps.append(seconds)
                # The process is suspended for three hours across the post time.
                clock.current += dt.timedelta(seconds=seconds + 3 * 3600)

            async def scenario():
                with self.assertRaises(asyncio.CancelledError):
                    await run_daily_posts(
                        self.settings(), state, post,
                        now=clock.now, sleep=suspended_sleep,
                    )

            asyncio.run(scenario())
            self.assertEqual(clock.sleeps, [60])
            # Unlike startup, the missed local day is still delivered late.
            self.assertEqual(posted, [dt.date(2026, 1, 15)])
            self.assertEqual(
                clock.now(), dt.datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
            )

    def test_restart_same_day_does_not_post_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.json")
            settings = self.settings()

            clock = FakeClock(dt.datetime(2026, 1, 15, 4, 59, tzinfo=UTC))
            state = DailyPostState(path)
            first_posts = []

            async def first_post():
                first_posts.append(clock.now().astimezone(NY).date())

            async def first_sleep(seconds):
                # Stop after the first post, while waiting for the next day.
                if clock.sleeps:
                    raise asyncio.CancelledError
                await clock.sleep(seconds)

            async def first_run():
                with self.assertRaises(asyncio.CancelledError):
                    await run_daily_posts(
                        settings, state, first_post,
                        now=clock.now, sleep=first_sleep,
                    )

            asyncio.run(first_run())
            self.assertEqual(first_posts, [dt.date(2026, 1, 15)])
            self.assertEqual(DailyPostState(path).last_post_date, dt.date(2026, 1, 15))

            # Restart one second after the post; the next post is tomorrow.
            clock = FakeClock(dt.datetime(2026, 1, 15, 5, 0, 1, tzinfo=UTC))
            restored = DailyPostState(path)
            second_posts = []

            async def second_post():
                second_posts.append(clock.now().astimezone(NY).date())
                raise asyncio.CancelledError

            async def second_run():
                with self.assertRaises(asyncio.CancelledError):
                    await run_daily_posts(
                        settings, restored, second_post,
                        now=clock.now, sleep=clock.sleep,
                    )

            asyncio.run(second_run())
            self.assertEqual(second_posts, [dt.date(2026, 1, 16)])

    def test_failed_post_is_reported_and_does_not_kill_the_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(dt.datetime(2026, 1, 15, 4, 59, tzinfo=UTC))
            state = DailyPostState(str(Path(directory) / "state.json"))
            errors = []
            attempts = []

            async def post():
                attempts.append(clock.now().astimezone(NY).date())
                if len(attempts) == 1:
                    raise RuntimeError("tracker unavailable")
                raise asyncio.CancelledError

            async def scenario():
                with self.assertRaises(asyncio.CancelledError):
                    await run_daily_posts(
                        self.settings(), state, post,
                        now=clock.now, sleep=clock.sleep,
                        on_error=errors.append,
                    )

            asyncio.run(scenario())
            self.assertEqual([str(error) for error in errors], ["tracker unavailable"])
            self.assertEqual(
                attempts, [dt.date(2026, 1, 15), dt.date(2026, 1, 16)]
            )
            self.assertFalse(state.already_posted(dt.date(2026, 1, 15)))


if __name__ == "__main__":
    unittest.main()
