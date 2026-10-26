import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from modules.daily_post import (
    DailyPostConfigError,
    load_daily_post_settings,
)

ROOT = Path(__file__).resolve().parent


def load_start_daily_post_scheduler():
    tree = ast.parse((ROOT / "bridgeipelago.py").read_text(encoding="utf-8"))
    names = {"StartDailyPostScheduler", "PostDailyStats", "ReportDailyPostError"}
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    namespace = {"AllGamesReleased": AsyncMock(return_value=False)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace["StartDailyPostScheduler"], namespace


class FakeTask:
    def __init__(self):
        self._done = False

    def done(self):
        return self._done


class FakeLoop:
    def __init__(self):
        self.created = []

    def create_task(self, coroutine):
        coroutine.close()
        task = FakeTask()
        self.created.append(task)
        return task


class DailyPostStartupTests(unittest.TestCase):
    def setUp(self):
        start, self.ns = load_start_daily_post_scheduler()
        self.start = start
        self.loop = FakeLoop()

        async def fake_run_daily_posts(*args, **kwargs):
            return None

        self.ns.update({
            "CoreConfig": {},
            "DebugChannel": SimpleNamespace(send=AsyncMock()),
            "DailyPostTask": None,
            "GetCoreDirectory": lambda folder: "logs/session/",
            "discord_client": SimpleNamespace(loop=self.loop),
            "load_daily_post_settings": load_daily_post_settings,
            "DailyPostConfigError": DailyPostConfigError,
            "DailyPostState": Mock(),
            "run_daily_posts": fake_run_daily_posts,
            "WriteToErrorLog": Mock(),
        })

    def run_start(self):
        with patch("builtins.print"):
            asyncio.run(self.start())

    def test_enabled_config_starts_only_one_task_across_reconnects(self):
        self.ns["CoreConfig"] = {"DailyPostConfig": {
            "Enabled": True, "Time": "00:00", "Timezone": "America/New_York",
        }}
        self.run_start()
        first = self.ns["DailyPostTask"]
        self.assertIsNotNone(first)
        self.assertEqual(len(self.loop.created), 1)

        # on_ready fires again after a gateway reconnect.
        self.run_start()
        self.assertIs(self.ns["DailyPostTask"], first)
        self.assertEqual(len(self.loop.created), 1)

    def test_finished_task_can_be_replaced(self):
        self.ns["CoreConfig"] = {"DailyPostConfig": {"Enabled": True}}
        self.run_start()
        self.ns["DailyPostTask"]._done = True
        self.run_start()
        self.assertEqual(len(self.loop.created), 2)

    def test_disabled_or_missing_config_starts_nothing(self):
        for config in ({}, {"DailyPostConfig": {"Enabled": False}}):
            with self.subTest(config=config):
                self.ns["CoreConfig"] = config
                self.run_start()
                self.assertIsNone(self.ns["DailyPostTask"])
        self.assertEqual(self.loop.created, [])
        self.ns["DebugChannel"].send.assert_not_awaited()

    def test_malformed_config_reports_and_does_not_start_or_raise(self):
        self.ns["CoreConfig"] = {"DailyPostConfig": {
            "Enabled": True, "Time": "midnight", "Timezone": "America/New_York",
        }}
        self.run_start()
        self.assertIsNone(self.ns["DailyPostTask"])
        self.assertEqual(self.loop.created, [])
        message = self.ns["DebugChannel"].send.await_args.args[0]
        self.assertIn("Daily post configuration error", message)
        self.assertIn("DailyPostConfig.Time", message)
        self.ns["WriteToErrorLog"].assert_called_once()

    def test_daily_post_reuses_existing_broadcast_commands(self):
        calls = []

        async def checkcount():
            calls.append("checkcount")
            return True

        async def checkgraph():
            calls.append("checkgraph")
            return True

        async def deathcount():
            calls.append("deathcount")
            return True

        self.ns["Command_CheckCount"] = checkcount
        self.ns["Command_DeathCount"] = deathcount
        self.ns["Command_CheckGraph"] = checkgraph
        asyncio.run(self.ns["PostDailyStats"]())
        self.assertEqual(calls, ["checkcount", "checkgraph", "deathcount"])


if __name__ == "__main__":
    unittest.main()
