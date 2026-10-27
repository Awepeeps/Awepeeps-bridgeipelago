"""Offline failure injection for persistence and HTTP entry points."""

import asyncio
import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import requests

from modules.config_store import save_config_atomic
from test_admin_queue_safety import load, load_set_config


class ConfigTransactionTests(unittest.TestCase):
    def test_failed_writes_preserve_disk_memory_and_toggle(self):
        for stage in ("serialize", "flush", "replace"):
            for key, value in (("ArchipelagoPort", "38282"), ("ArchipelagoPassword", "new-secret")):
                with self.subTest(stage=stage, key=key), tempfile.TemporaryDirectory() as directory:
                    ns = load_set_config()
                    path = Path(directory) / "custom-config.json"
                    ns["config"] = path
                    original = copy.deepcopy(ns["CoreConfig"])
                    path.write_text(json.dumps(original), encoding="utf-8")
                    original_bytes = path.read_bytes()

                    def partial_write(data, stream, **kwargs):
                        stream.write('{"partial":')
                        raise OSError("sensitive-error-text")

                    target = {"serialize": "json.dump", "flush": "os.fsync", "replace": "os.replace"}[stage]
                    effect = partial_write if stage == "serialize" else OSError("sensitive-error-text")
                    with patch("modules.config_store." + target, side_effect=effect):
                        result = ns["SetConfigVariable"](key, value)
                    self.assertEqual(result, "Unable to save configuration. No settings were changed.")
                    self.assertEqual(path.read_bytes(), original_bytes)
                    self.assertEqual(ns["CoreConfig"], original)
                    self.assertTrue(ns["ToggleConfig"]["CrippleTracker"])
                    ns["ConfirmSpecialFiles"].assert_not_called()
                    self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_replacement_is_beside_custom_path_and_precedes_memory_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            ns = load_set_config()
            path = Path(directory) / "custom-config.json"
            ns["config"] = path
            path.write_text(json.dumps(ns["CoreConfig"]), encoding="utf-8")
            replace = os.replace

            def inspect_replace(source, destination):
                self.assertEqual(Path(source).parent, path.parent)
                self.assertEqual(Path(destination), path)
                self.assertEqual(ns["CoreConfig"]["ArchipelagoConfig"]["ArchipelagoPort"], 38281)
                self.assertTrue(ns["ToggleConfig"]["CrippleTracker"])
                self.assertEqual(json.loads(Path(source).read_text())["ArchipelagoConfig"]["ArchipelagoPort"], 38282)
                return replace(source, destination)

            with patch("modules.config_store.os.replace", side_effect=inspect_replace):
                ns["SetConfigVariable"]("ArchipelagoPort", "38282")
            self.assertEqual(json.loads(path.read_text())["ArchipelagoConfig"]["ArchipelagoPort"], 38282)
            self.assertEqual(ns["CoreConfig"]["ArchipelagoConfig"]["ArchipelagoPort"], 38282)
            self.assertFalse(ns["ToggleConfig"]["CrippleTracker"])
            ns["ConfirmSpecialFiles"].assert_called_once()

    def test_serialization_error_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(TypeError):
                save_config_atomic(path, {"invalid": object()})
            self.assertEqual(path.read_text(), "{}")
            self.assertEqual(list(Path(directory).iterdir()), [path])


class HintHttpTests(unittest.IsolatedAsyncioTestCase):
    def namespace(self, get):
        soup = Mock()
        soup.find.return_value.find_all.return_value = []
        ns = load(["Command_Hints"], {
            "CoreConfig": {"AdvancedConfig": {"SelfHostNoWeb": False},
                           "ArchipelagoConfig": {"ArchipelagoTrackerURL": "https://tracker.test"}},
            "asyncio": asyncio, "requests": SimpleNamespace(get=get), "discord": discord,
            "BeautifulSoup": Mock(return_value=soup), "os": SimpleNamespace(path=SimpleNamespace(isfile=lambda _: False)),
            "MainChannel": SimpleNamespace(send=AsyncMock()), "DebugChannel": SimpleNamespace(send=AsyncMock()),
            "GetCoreDirectory": lambda _: "unused/",
        })
        player = SimpleNamespace(name="Test", create_dm=AsyncMock(), dm_channel=SimpleNamespace(send=AsyncMock()))
        return ns, player

    async def test_http_runs_off_event_loop_with_timeout_and_status_check(self):
        response = Mock(content=b"<html></html>")
        caller_thread = threading.get_ident()

        def get(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), caller_thread)
            return response

        request = Mock(side_effect=get)
        ns, player = self.namespace(request)
        await ns["Command_Hints"](player)
        request.assert_called_once_with("https://tracker.test", timeout=15)
        response.raise_for_status.assert_called_once_with()
        ns["BeautifulSoup"].assert_called_once_with(response.content, "html.parser")

    async def test_timeout_and_http_error_use_existing_error_handling(self):
        for error in (requests.Timeout("offline fixture"), requests.HTTPError("offline fixture")):
            with self.subTest(error=type(error).__name__):
                response = Mock()
                response.raise_for_status.side_effect = error
                request = Mock(side_effect=error) if isinstance(error, requests.Timeout) else Mock(return_value=response)
                ns, player = self.namespace(request)
                self.assertFalse(await ns["Command_Hints"](player))
                ns["BeautifulSoup"].assert_not_called()
                ns["DebugChannel"].send.assert_awaited_once()


class StartupVersionTests(unittest.TestCase):
    def test_startup_version_timeout_and_status_validation(self):
        class StopStartup(Exception):
            pass

        for fail in (False, True):
            with self.subTest(http_error=fail):
                response = Mock(content=b'{"tag_name": "live-v3.0.1"}')
                if fail:
                    response.raise_for_status.side_effect = requests.HTTPError("offline fixture")
                get = Mock(return_value=response)
                ns = load(["main"], {
                    "requests": SimpleNamespace(get=get), "json": json,
                    "GenerateConfigManagers": Mock(side_effect=StopStartup),
                })
                with self.assertRaises(StopStartup):
                    ns["main"]()
                get.assert_called_once_with("https://api.github.com/repos/Quasky/bridgeipelago/releases/latest", timeout=15)
                response.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
