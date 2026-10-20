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
        if (isinstance(node, ast.FunctionDef) and node.name in {"QueueRoomSleepNotice", "QueueRoomWakeNotice"})
        or (isinstance(node, ast.ClassDef) and node.name == "TrackerClient")
    ]
    namespace = {
        "CoreConfig": {"ArchipelagoConfig": {
            "ArchipelagoServer": "localhost", "ArchipelagoPort": 38281,
            "ArchipelagoBotSlot": "bot", "ArchipelagoServerURL": "https://archipelago.gg/room/abc",
        }, "AdvancedConfig": {"DebugMode": False, "QueueOverclock": 1}},
        "RoomAsleep": False, "chat_queue": FakeQueue(), "websocket_queue": FakeQueue(),
        "discordbridge_queue": SimpleNamespace(empty=lambda: True),
        "GetArchPassword": lambda: "", "typing": SimpleNamespace(Any=object), "Enum": Enum,
        "uuid": SimpleNamespace(getnode=lambda: 1), "ClientConnection": object,
        "Thread": threading.Thread, "Event": threading.Event, "json": json,
        "time": SimpleNamespace(monotonic=lambda: 0), "ConnectionRefusedError": ConnectionRefusedError,
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

    def test_received_1001_close_queues_one_sleep_notice_and_sets_flag(self):
        namespace = self.run_disconnect(1001)
        self.assertTrue(namespace["RoomAsleep"])
        self.assertEqual(len(namespace["chat_queue"].messages), 1)
        message = namespace["chat_queue"].messages[0]
        self.assertEqual(message["type"], "CORE")
        self.assertIn("<https://archipelago.gg/room/abc>", message["data"]["text"])

    def test_non_1001_close_does_not_queue_notice(self):
        namespace = self.run_disconnect(1000)
        self.assertFalse(namespace["RoomAsleep"])
        self.assertEqual(namespace["chat_queue"].messages, [])

    def test_connected_with_sleep_flag_queues_wake_notice_and_clears_flag(self):
        client, namespace = self.make_client()
        namespace["RoomAsleep"] = True
        client.is_closed.clear()
        client.ap_connection = FakeConnection(json.dumps([{"cmd": "Connected"}]), client)
        client.run()
        self.assertFalse(namespace["RoomAsleep"])
        self.assertEqual(len(namespace["chat_queue"].messages), 1)
        self.assertIn("archipelago.gg:38281", namespace["chat_queue"].messages[0]["data"]["text"])

    def test_connected_without_sleep_flag_does_not_queue_notice(self):
        client, namespace = self.make_client()
        client.is_closed.clear()
        client.ap_connection = FakeConnection(json.dumps([{"cmd": "Connected"}]), client)
        client.run()
        self.assertFalse(namespace["RoomAsleep"])
        self.assertEqual(namespace["chat_queue"].messages, [])

    def test_connection_refused_sets_flag_without_notice(self):
        client, namespace = self.make_client()
        def refuse(*args, **kwargs):
            raise ConnectionRefusedError(111, "connection refused")
        namespace["connect"] = refuse
        client.start()
        self.assertTrue(namespace["RoomAsleep"])
        self.assertEqual(namespace["chat_queue"].messages, [])


if __name__ == "__main__":
    unittest.main()
