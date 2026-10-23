import ast
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parent


def load_check_arch_host():
    tree = ast.parse((ROOT / "bridgeipelago.py").read_text())
    names = {"CheckArchHost", "AlertMention", "RecordCheckArchHostFailure", "ResetCheckArchHostFailures"}
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    for node in nodes:
        node.decorator_list = []
    namespace = {"asyncio": asyncio, "json": json, "CoreConfig": {"DiscordConfig": {"DiscordAlertUserID": ""}},
                 "requests": SimpleNamespace(get=Mock()), "WriteToErrorLog": Mock(),
                 "DebugChannel": SimpleNamespace(send=AsyncMock()), "MainChannel": SimpleNamespace(send=AsyncMock()),
                 "SetConfigVariable": Mock()}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    namespace["CheckArchHostFailureCount"] = 0
    namespace["CheckArchHostAlertSent"] = False
    return namespace


def load_set_config_variable():
    tree = ast.parse((ROOT / "bridgeipelago.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if getattr(node, "name", None) == "SetConfigVariable")
    namespace = {
        "CoreConfig": {"ArchipelagoConfig": {"ArchipelagoPort": 38281}},
        "ToggleConfig": {"CrippleTracker": True},
        "ConfigLock": __import__("threading").RLock(),
        "ToggleLock": __import__("threading").RLock(),
        "ConfirmSpecialFiles": Mock(),
        "json": json,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace


class SetConfigVariableTests(unittest.TestCase):
    def test_numeric_port_is_saved_as_number_and_returns_string(self):
        ns = load_set_config_variable()
        real_open = open
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"

            def open_temp_config(path, mode="r", *args, **kwargs):
                return real_open(config_path if path == "config.json" else path, mode, *args, **kwargs)

            with patch("builtins.open", side_effect=open_temp_config):
                result = ns["SetConfigVariable"]("ArchipelagoPort", 38282)

            self.assertEqual(result, "Key 'ArchipelagoPort' set to '38282'!")
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["ArchipelagoConfig"]["ArchipelagoPort"], 38282)
            self.assertIsInstance(saved["ArchipelagoConfig"]["ArchipelagoPort"], int)
            self.assertEqual(ns["CoreConfig"]["ArchipelagoConfig"]["ArchipelagoPort"], 38282)


def status_response(content=b'{"last_port": 38281}', error=None):
    response = SimpleNamespace(content=content, raise_for_status=Mock())
    if error is not None:
        response.raise_for_status.side_effect = error
    return response


class CheckArchHostAlertsTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_check_arch_host()
        self.ns["CoreConfig"].update({
            "AdvancedConfig": {"SelfHostNoWeb": False},
            "ArchipelagoConfig": {"ArchipelagoServerURL": "https://archipelago.gg/room/abc123", "ArchipelagoPort": 38281},
        })
        self.ns["requests"].get.return_value = status_response()

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
        self.ns["requests"].get.return_value.raise_for_status.assert_called_once_with()

    def test_http_status_errors_count_toward_alert(self):
        self.ns["requests"].get.return_value = status_response(error=RuntimeError("503 response"))
        for _ in range(5):
            asyncio.run(self.ns["CheckArchHost"]())
        self.assertEqual(self.ns["CheckArchHostFailureCount"], 5)
        self.assertTrue(self.ns["CheckArchHostAlertSent"])
        self.assertIn("503 response", self.ns["DebugChannel"].send.await_args.args[0])

    def test_malformed_or_missing_port_counts_as_failure_and_valid_payload_recovers(self):
        bad_payloads = (
            b"{", b"{}", b'{"last_port": null}', b'{"last_port": "bad"}',
            b'{"last_port": 38281.5}', b'{"last_port": 70000}',
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                self.ns["requests"].get.return_value = status_response(payload)
                asyncio.run(self.ns["CheckArchHost"]())
                self.assertEqual(self.ns["CheckArchHostFailureCount"], 1)
                self.assertFalse(self.ns["CheckArchHostAlertSent"])
                self.ns["CheckArchHostFailureCount"] = 0

        self.ns["requests"].get.return_value = status_response(b'{"last_port": 38281}')
        for _ in range(5):
            self.ns["requests"].get.return_value = status_response(b"{}")
            asyncio.run(self.ns["CheckArchHost"]())
        self.assertTrue(self.ns["CheckArchHostAlertSent"])
        self.ns["requests"].get.return_value = status_response()
        asyncio.run(self.ns["CheckArchHost"]())
        self.assertEqual(self.ns["CheckArchHostFailureCount"], 0)
        self.assertFalse(self.ns["CheckArchHostAlertSent"])
        self.assertIn("recovered", self.ns["DebugChannel"].send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
