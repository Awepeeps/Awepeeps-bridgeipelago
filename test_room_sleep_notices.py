import ast
import json
import threading
import unittest
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
        if (isinstance(node, ast.FunctionDef) and node.name in {"QueueRoomSleepNotice", "QueueRoomWakeNotice", "QueueRoomUnavailableNotice"})
        or (isinstance(node, ast.ClassDef) and node.name == "TrackerClient")
    ]
    namespace = {
        "CoreConfig": {"ArchipelagoConfig": {
            "ArchipelagoServer": "localhost", "ArchipelagoPort": 38281,
            "ArchipelagoBotSlot": "bot", "ArchipelagoServerURL": "https://archipelago.gg/room/abc",
        }, "AdvancedConfig": {"DebugMode": False, "QueueOverclock": 1}},
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
        self.assertTrue(text.startswith("\U0001f4a4 The Archipelago room"))
        self.assertIn("room may be asleep", text)
        self.assertIn("<https://archipelago.gg/room/abc>", text)
        client.is_closed.clear()
        client.ap_connection = FakeConnection(json.dumps([{"cmd": "Connected"}]), client)
        client.run()
        self.assertFalse(namespace["RoomAsleep"])
        self.assertEqual(len(namespace["chat_queue"].messages), 2)
        self.assertIn("\u2600\ufe0f The Archipelago room is reachable again", namespace["chat_queue"].messages[1]["data"]["text"])
        self.assertNotIn("awake", namespace["chat_queue"].messages[1]["data"]["text"])

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
