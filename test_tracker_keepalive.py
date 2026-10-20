import ast
import json
import threading
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class FakeConnection:
    def __init__(self, clock, ping_event=None, on_ping=None):
        self.clock = clock
        self.ping_event = ping_event or threading.Event()
        self.on_ping = on_ping
        self.ping_count = 0
        self.close = Mock()

    def recv(self, timeout=None):
        self.clock.now += 10
        raise TimeoutError()

    def ping(self):
        self.ping_count += 1
        if self.on_ping:
            self.on_ping()
        return self.ping_event


def load_tracker_client():
    tree = ast.parse((ROOT / "bridgeipelago.py").read_text())
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "TrackerClient")
    namespace = {
        "CoreConfig": {
            "ArchipelagoConfig": {"ArchipelagoServer": "localhost", "ArchipelagoPort": 38281, "ArchipelagoBotSlot": "bot"},
            "AdvancedConfig": {"DebugMode": False, "QueueOverclock": 1},
        },
        "GetArchPassword": lambda: "",
        "typing": SimpleNamespace(Any=object), "Enum": Enum, "uuid": SimpleNamespace(getnode=lambda: 1),
        "ClientConnection": object, "Thread": threading.Thread, "Event": threading.Event,
        "json": json, "time": SimpleNamespace(monotonic=lambda: 0),
        "websockets": SimpleNamespace(exceptions=SimpleNamespace(
            ConnectionClosedError=type("ConnectionClosedError", (Exception,), {}),
            ConnectionClosed=type("ConnectionClosed", (Exception,), {}),
            InvalidState=type("InvalidState", (Exception,), {}),
        )),
        "WriteToErrorLog": Mock(), "websocket_queue": Mock(),
        "discordbridge_queue": SimpleNamespace(empty=lambda: True),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace["TrackerClient"], namespace


class TrackerKeepaliveTests(unittest.TestCase):
    def make_client(self, auto_pong=False):
        client_class, namespace = load_tracker_client()
        clock = FakeClock()
        namespace["time"] = SimpleNamespace(monotonic=clock.monotonic)
        event = threading.Event()
        client = client_class()
        client.is_closed.clear()
        def on_ping():
            if auto_pong:
                event.set()
                # End the run loop on the next receive timeout.
                original_recv = connection.recv
                def recv_and_stop(timeout=None):
                    client.is_closed.set()
                    return original_recv(timeout)
                connection.recv = recv_and_stop
        connection = FakeConnection(clock, event, on_ping)
        client.ap_connection = connection
        return client, namespace, connection, event

    def test_idle_triggers_one_ping(self):
        client, _, connection, _ = self.make_client()
        def stop_after_ping():
            client.is_closed.set()
        connection.on_ping = stop_after_ping
        client.run()
        self.assertEqual(connection.ping_count, 1)

    def test_missing_pong_logs_queues_and_exits(self):
        client, namespace, connection, _ = self.make_client()
        client.run()
        self.assertEqual(connection.ping_count, 1)
        namespace["WriteToErrorLog"].assert_called_once_with("Websocket", "Tracker keepalive ping timed out after 20 seconds.")
        namespace["websocket_queue"].put.assert_called_once_with("!! Tracker keepalive timeout ...")

    def test_pong_resets_idle_clock_without_restart(self):
        client, namespace, connection, _ = self.make_client(auto_pong=True)
        client.run()
        self.assertEqual(connection.ping_count, 1)
        namespace["WriteToErrorLog"].assert_not_called()
        namespace["websocket_queue"].put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
