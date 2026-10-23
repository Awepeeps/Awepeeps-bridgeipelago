import ast
import json
import threading
import unittest
from multiprocessing import Manager
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent


class FakeQueue:
    def __init__(self):
        self.messages = []

    def put(self, message):
        self.messages.append(message)


def load_tracker_client():
    tree = ast.parse((ROOT / "bridgeipelago.py").read_text(encoding="utf-8"))
    selected = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in {"RenderConnectionNotification", "GetConnectionNotificationPlaceholders", "QueueRoomSleepNotice", "QueueRoomWakeNotice", "QueueRoomUnavailableNotice"})
        or (isinstance(node, ast.ClassDef) and node.name == "TrackerClient")
    ]
    namespace = {
        "CoreConfig": {"ArchipelagoConfig": {
            "ArchipelagoServer": "localhost", "ArchipelagoPort": 38281,
            "ArchipelagoBotSlot": "bot", "ArchipelagoServerURL": "https://archipelago.gg/room/abc",
        }, "AdvancedConfig": {"DebugMode": False, "QueueOverclock": 1}},
        "DEFAULT_CONNECTION_UNAVAILABLE_MESSAGE": "Archipelago connection unavailable. {room_url}",
        "DEFAULT_CONNECTION_RESTORED_MESSAGE": "Archipelago connection restored: {server_address}",
        "RoomAsleep": False, "ConnectionUnavailableSince": None, "chat_queue": FakeQueue(), "websocket_queue": FakeQueue(),
        "discordbridge_queue": SimpleNamespace(empty=lambda: True),
        "GetArchPassword": lambda: "", "typing": SimpleNamespace(Any=object), "Enum": Enum,
        "uuid": SimpleNamespace(getnode=lambda: 1), "ClientConnection": object,
        "Thread": threading.Thread, "Event": threading.Event, "json": json,
        "time": SimpleNamespace(now=0, monotonic=lambda: 0), "ConnectionRefusedError": ConnectionRefusedError,
        "websockets": SimpleNamespace(exceptions=SimpleNamespace(
            ConnectionClosedError=type("ConnectionClosedError", (Exception,), {}),
            ConnectionClosed=type("ConnectionClosed", (Exception,), {}),
            InvalidState=type("InvalidState", (Exception,), {}),
        )),
        "WriteToErrorLog": Mock(), "WriteArchConnectionJSON": Mock(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace["TrackerClient"], namespace


class FakeConnection:
    def __init__(self, first_message, client=None):
        self.first_message = first_message
        self.called = False
        self.client = client

    def recv(self, timeout=None):
        if not self.called:
            self.called = True
            if self.client:
                self.client.is_closed.set()
            return self.first_message
        raise TimeoutError()


class RoomSleepNoticeTests(unittest.TestCase):
    def make_client(self):
        tracker_class, namespace = load_tracker_client()
        client = tracker_class()
        client.is_closed.set()
        return client, namespace

    def run_disconnect(self, close_code):
        client, namespace = self.make_client()
        client.is_closed.clear()
        close_frame = SimpleNamespace(code=close_code) if close_code is not None else None
        exception = namespace["websockets"].exceptions.ConnectionClosed("closed")
        exception.rcvd = close_frame
        client.ap_connection = FakeConnection(None)
        client.ap_connection.recv = Mock(side_effect=exception)
        client.run()
        return namespace

    def test_quick_1001_close_and_reconnect_are_quiet(self):
        namespace = self.run_disconnect(1001)
        self.assertFalse(namespace["RoomAsleep"])
        self.assertEqual(namespace["chat_queue"].messages, [])
        namespace["QueueRoomWakeNotice"]()
        self.assertEqual(namespace["chat_queue"].messages, [])

    def test_non_1001_close_does_not_claim_room_sleep(self):
        namespace = self.run_disconnect(1000)
        self.assertFalse(namespace["RoomAsleep"])
        self.assertEqual(namespace["chat_queue"].messages, [])

    def test_prolonged_unavailability_announces_once_and_connection_recovery(self):
        client, namespace = self.make_client()
        namespace["QueueRoomSleepNotice"](SimpleNamespace(code=1001))
        namespace["time"].monotonic = lambda: 61
        namespace["QueueRoomUnavailableNotice"]()
        namespace["QueueRoomUnavailableNotice"]()
        self.assertEqual(len(namespace["chat_queue"].messages), 1)
        text = namespace["chat_queue"].messages[0]["data"]["text"]
        self.assertEqual(text, "Archipelago connection unavailable. <https://archipelago.gg/room/abc>")
        self.assertIn("<https://archipelago.gg/room/abc>", text)
        client.is_closed.clear()
        client.ap_connection = FakeConnection(json.dumps([{"cmd": "Connected"}]), client)
        client.run()
        self.assertFalse(namespace["RoomAsleep"])
        self.assertEqual(len(namespace["chat_queue"].messages), 2)
        self.assertEqual(namespace["chat_queue"].messages[1]["data"]["text"], "Archipelago connection restored: archipelago.gg:38281")
        # The Discord side edits a recent sleep notice based on these flags.
        self.assertEqual([m["flag"] for m in namespace["chat_queue"].messages], ["ROOM_SLEEP", "ROOM_WAKE"])
        self.assertEqual({m["type"] for m in namespace["chat_queue"].messages}, {"CORE"})

    def test_emoji_templates_and_custom_text_are_preserved_with_dynamic_values(self):
        _, namespace = load_tracker_client()
        namespace["CoreConfig"]["NotificationConfig"] = {
            "ConnectionUnavailableMessage": "💤 custom {room_url}",
            "ConnectionRestoredMessage": "☀️ again at **{server_address}**",
        }
        namespace["QueueRoomSleepNotice"](SimpleNamespace(code=1001))
        namespace["time"].monotonic = lambda: 61
        namespace["QueueRoomUnavailableNotice"]()
        self.assertEqual(namespace["chat_queue"].messages[0]["data"]["text"], "💤 custom <https://archipelago.gg/room/abc>")
        namespace["CoreConfig"]["ArchipelagoConfig"]["ArchipelagoServerURL"] = "https://my.host/room/id"
        namespace["CoreConfig"]["ArchipelagoConfig"]["ArchipelagoPort"] = 555
        namespace["QueueRoomWakeNotice"]()
        self.assertEqual(namespace["chat_queue"].messages[1]["data"]["text"], "☀️ again at **my.host:555**")

    def test_both_placeholders_are_supported_in_both_notice_templates(self):
        _, namespace = load_tracker_client()
        namespace["CoreConfig"]["NotificationConfig"] = {
            "ConnectionUnavailableMessage": "down {room_url} {server_address}",
            "ConnectionRestoredMessage": "up {room_url} {server_address}",
        }
        namespace["QueueRoomSleepNotice"](SimpleNamespace(code=1001))
        namespace["time"].monotonic = lambda: 61
        namespace["QueueRoomUnavailableNotice"]()
        expected = "down <https://archipelago.gg/room/abc> archipelago.gg:38281"
        self.assertEqual(namespace["chat_queue"].messages[0]["data"]["text"], expected)
        namespace["RoomAsleep"] = True
        namespace["QueueRoomWakeNotice"]()
        self.assertEqual(namespace["chat_queue"].messages[1]["data"]["text"], "up <https://archipelago.gg/room/abc> archipelago.gg:38281")

    def test_dictproxy_notification_config_works(self):
        _, namespace = load_tracker_client()
        with Manager() as manager:
            namespace["CoreConfig"]["NotificationConfig"] = manager.dict({
                "ConnectionUnavailableMessage": "down {room_url} {server_address}",
            })
            namespace["QueueRoomSleepNotice"](SimpleNamespace(code=1001))
            namespace["time"].monotonic = lambda: 61
            namespace["QueueRoomUnavailableNotice"]()
            self.assertEqual(namespace["chat_queue"].messages[0]["data"]["text"], "down <https://archipelago.gg/room/abc> archipelago.gg:38281")

    def test_template_contains_emoji_overrides_and_default_config_has_plain_fallbacks(self):
        template = json.loads((ROOT / "config.json.template").read_text(encoding="utf-8"))
        notifications = template["NotificationConfig"]
        self.assertIn("💤", notifications["ConnectionUnavailableMessage"])
        self.assertIn("{room_url}", notifications["ConnectionUnavailableMessage"])
        self.assertIn("☀️", notifications["ConnectionRestoredMessage"])
        self.assertIn("{server_address}", notifications["ConnectionRestoredMessage"])
        self.assertEqual(notifications["SleepWakeEditWindowMinutes"], 15)
        _, namespace = load_tracker_client()
        self.assertEqual(namespace["DEFAULT_CONNECTION_UNAVAILABLE_MESSAGE"], "Archipelago connection unavailable. {room_url}")
        self.assertEqual(namespace["DEFAULT_CONNECTION_RESTORED_MESSAGE"], "Archipelago connection restored: {server_address}")

    def test_missing_malformed_and_empty_notification_config_fall_back(self):
        for malformed in (None, [], "bad"):
            _, namespace = load_tracker_client()
            namespace["CoreConfig"]["NotificationConfig"] = malformed
            namespace["QueueRoomSleepNotice"](SimpleNamespace(code=1001))
            namespace["time"].monotonic = lambda: 61
            namespace["QueueRoomUnavailableNotice"]()
            self.assertEqual(namespace["chat_queue"].messages[0]["data"]["text"], "Archipelago connection unavailable. <https://archipelago.gg/room/abc>")
        _, namespace = load_tracker_client()
        namespace["CoreConfig"]["NotificationConfig"] = {"ConnectionUnavailableMessage": "", "ConnectionRestoredMessage": 7}
        namespace["QueueRoomSleepNotice"](SimpleNamespace(code=1001))
        namespace["time"].monotonic = lambda: 61
        namespace["QueueRoomUnavailableNotice"]()
        self.assertEqual(namespace["chat_queue"].messages[0]["data"]["text"], "Archipelago connection unavailable. <https://archipelago.gg/room/abc>")
        namespace["RoomAsleep"] = True
        namespace["QueueRoomWakeNotice"]()
        self.assertEqual(namespace["chat_queue"].messages[1]["data"]["text"], "Archipelago connection restored: archipelago.gg:38281")

        namespace["CoreConfig"]["NotificationConfig"] = {"ConnectionUnavailableMessage": " \t\n", "ConnectionRestoredMessage": " \t\n"}
        namespace["RoomAsleep"] = False
        namespace["ConnectionUnavailableSince"] = None
        namespace["time"].monotonic = lambda: 0
        namespace["QueueRoomSleepNotice"](SimpleNamespace(code=1001))
        namespace["time"].monotonic = lambda: 61
        namespace["QueueRoomUnavailableNotice"]()
        self.assertEqual(namespace["chat_queue"].messages[2]["data"]["text"], "Archipelago connection unavailable. <https://archipelago.gg/room/abc>")
        namespace["QueueRoomWakeNotice"]()
        self.assertEqual(namespace["chat_queue"].messages[3]["data"]["text"], "Archipelago connection restored: archipelago.gg:38281")

    def test_connected_without_sleep_flag_does_not_queue_notice(self):
        client, namespace = self.make_client()
        client.is_closed.clear()
        client.ap_connection = FakeConnection(json.dumps([{"cmd": "Connected"}]), client)
        client.run()
        self.assertFalse(namespace["RoomAsleep"])
        self.assertEqual(namespace["chat_queue"].messages, [])

    def test_connection_refused_does_not_instantly_emit_notice_or_recovery(self):
        client, namespace = self.make_client()
        def refuse(*args, **kwargs):
            raise ConnectionRefusedError(111, "connection refused")
        namespace["connect"] = refuse
        client.start()
        self.assertFalse(namespace["RoomAsleep"])
        self.assertEqual(namespace["chat_queue"].messages, [])

    def test_connection_refused_after_candidate_matures_emits_unavailable_notice(self):
        client, namespace = self.make_client()
        namespace["QueueRoomSleepNotice"](SimpleNamespace(code=1001))
        namespace["time"].monotonic = lambda: 61
        namespace["connect"] = lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionRefusedError(111, "connection refused"))
        client.start()
        self.assertTrue(namespace["RoomAsleep"])
        self.assertEqual(len(namespace["chat_queue"].messages), 1)
        self.assertIn("connection unavailable", namespace["chat_queue"].messages[0]["data"]["text"])


if __name__ == "__main__":
    unittest.main()
