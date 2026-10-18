import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parent

def load_on_ready():
    tree = ast.parse((ROOT / "bridgeipelago.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_ready")
    function.decorator_list = []
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace["on_ready"]

class FakeTask:
    def __init__(self):
        self.running = False
        self.start = Mock(side_effect=self._start)
    def _start(self):
        self.running = True
    def is_running(self):
        return self.running

class ConnectionAnnouncementTests(unittest.TestCase):
    def run_ready_twice(self, announce_setting):
        on_ready = load_on_ready()
        debug_channel = SimpleNamespace(send=AsyncMock())
        main_channel = SimpleNamespace(guild=SimpleNamespace(id=123))
        client = SimpleNamespace(get_channel=lambda channel_id: {1: main_channel, 2: debug_channel}[channel_id], user="mock bot")
        tree = SimpleNamespace(copy_global_to=Mock(), sync=AsyncMock())
        tasks = [FakeTask() for _ in range(5)]
        discord = SimpleNamespace(Object=lambda id: SimpleNamespace(id=id), HTTPException=Exception)
        discord_config = {"DiscordBroadcastChannel": "1", "DiscordDebugChannel": "2"}
        if announce_setting is not ...:
            discord_config["AnnounceConnection"] = announce_setting
        namespace = on_ready.__globals__
        namespace.update({
            "CoreConfig": {"DiscordConfig": discord_config, "AdvancedConfig": {"JoinMessage": "ready"}},
            "discord_client": client, "discord": discord, "MainChannel": None, "DebugChannel": None,
            "PlayerCommandsSyncedGuild": None, "tree": tree,
            "CheckArchHost": tasks[0], "ProcessItemQueue": tasks[1], "ProcessDeathQueue": tasks[2],
            "ProcessChatQueue": tasks[3], "CheckCommandQueue": tasks[4],
        })
        with unittest.mock.patch("builtins.print"):
            asyncio.run(on_ready())
            asyncio.run(on_ready())
        return debug_channel, tree, tasks

    def test_missing_or_false_setting_suppresses_only_startup_announcement(self):
        for setting in (..., False):
            with self.subTest(setting=setting):
                debug, tree, tasks = self.run_ready_twice(setting)
                debug.send.assert_not_awaited()
                self.assertEqual(tree.sync.await_count, 1)
                self.assertEqual(tree.copy_global_to.call_count, 1)
                for task in tasks:
                    task.start.assert_called_once_with()

    def test_true_setting_keeps_announcement_on_each_ready(self):
        debug, tree, tasks = self.run_ready_twice(True)
        self.assertEqual(debug.send.await_count, 2)
        debug.send.assert_awaited_with("Bot connected. Debug control - Online.")
        self.assertEqual(tree.sync.await_count, 1)
        for task in tasks:
            task.start.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
