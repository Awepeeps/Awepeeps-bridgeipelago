import ast
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parent


def load_check_arch_host():
    tree = ast.parse((ROOT / "bridgeipelago.py").read_text())
    names = {"CheckArchHost", "AlertMention", "RecordCheckArchHostFailure", "ResetCheckArchHostFailures"}
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    for node in nodes:
        node.decorator_list = []
    namespace = {"json": json, "CoreConfig": {"DiscordConfig": {"DiscordAlertUserID": ""}},
                 "requests": SimpleNamespace(get=Mock()), "WriteToErrorLog": Mock(),
                 "DebugChannel": SimpleNamespace(send=AsyncMock()), "MainChannel": SimpleNamespace(send=AsyncMock()),
                 "SetConfigVariable": Mock()}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    namespace["CheckArchHostFailureCount"] = 0
    namespace["CheckArchHostAlertSent"] = False
    return namespace


class CheckArchHostAlertsTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_check_arch_host()
        self.ns["CoreConfig"].update({
            "AdvancedConfig": {"SelfHostNoWeb": False},
            "ArchipelagoConfig": {"ArchipelagoServerURL": "https://archipelago.gg/room/abc123", "ArchipelagoPort": 38281},
        })
        self.ns["requests"].get.return_value = SimpleNamespace(content=b'{"last_port": 38281}')

    def fail_once(self, error=None):
        self.ns["requests"].get.side_effect = error or OSError("temporary DNS failure")
        asyncio.run(self.ns["CheckArchHost"]())
        self.ns["requests"].get.side_effect = None

    def test_alert_only_after_five_consecutive_failures_once(self):
        for _ in range(4):
            self.fail_once()
        self.ns["DebugChannel"].send.assert_not_awaited()
        self.fail_once()
        self.assertEqual(self.ns["DebugChannel"].send.await_count, 1)
        self.assertIn("5 checks in a row", self.ns["DebugChannel"].send.await_args.args[0])
        for _ in range(3):
            self.fail_once()
        self.assertEqual(self.ns["DebugChannel"].send.await_count, 1)

    def test_success_after_alert_sends_recovery_and_resets(self):
        for _ in range(5):
            self.fail_once()
        asyncio.run(self.ns["CheckArchHost"]())
        self.assertEqual(self.ns["DebugChannel"].send.await_count, 2)
        self.assertIn("recovered after 5 failed checks", self.ns["DebugChannel"].send.await_args.args[0])
        self.assertEqual(self.ns["CheckArchHostFailureCount"], 0)
        self.assertFalse(self.ns["CheckArchHostAlertSent"])

    def test_success_before_threshold_resets_without_notice(self):
        for _ in range(3):
            self.fail_once()
        asyncio.run(self.ns["CheckArchHost"]())
        self.assertEqual(self.ns["DebugChannel"].send.await_count, 0)
        self.assertEqual(self.ns["CheckArchHostFailureCount"], 0)

    def test_alert_mention_handles_blank_and_set_id(self):
        self.assertEqual(self.ns["AlertMention"](), "")
        self.ns["CoreConfig"]["DiscordConfig"]["DiscordAlertUserID"] = "123456"
        self.assertEqual(self.ns["AlertMention"](), " <@123456>")

    def test_room_status_request_has_timeout(self):
        asyncio.run(self.ns["CheckArchHost"]())
        self.ns["requests"].get.assert_called_once_with("https://archipelago.gg/api/room_status/abc123", timeout=10)


if __name__ == "__main__":
    unittest.main()
