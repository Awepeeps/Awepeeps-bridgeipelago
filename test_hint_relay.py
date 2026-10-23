import tempfile
import unittest
import ast
import json
import threading
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from modules.hint_relay import (
    HintRelayState, format_hint_status, hint_identity, hint_subscription_keys,
    normalize_requested_hint,
)


def hint(**updates):
    result = {
        "receiving_player": 2, "finding_player": 1, "location": 42,
        "item": 99, "found": False, "entrance": "East Gate",
        "item_flags": 1, "status": 30, "team": 0,
    }
    result.update(updates)
    return result


class HintRelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "relay.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_subscribes_to_same_team_players_and_group_slots_only(self):
        team, keys = hint_subscription_keys({
            "team": 0,
            "players": [
                {"team": 0, "slot": 1}, {"team": 0, "slot": 2},
                {"team": 1, "slot": 3},
            ],
            "slot_info": {"4": {"group_members": [1, "2"]}, "5": {"group_members": [3]}},
        })
        self.assertEqual(team, 0)
        self.assertEqual(keys, ["_read_hints_0_1", "_read_hints_0_2", "_read_hints_0_4"])

    def test_explicit_printjson_conversion_keeps_protocol_status_and_entrance(self):
        result = normalize_requested_hint({
            "type": "Hint", "receiving": 2, "found": False,
            "item": {"item": 99, "location": 42, "player": 1, "flags": 3},
            "data": [
                {"type": "hint_status", "hint_status": 30, "text": "(priority)"},
                {"type": "entrance_name", "text": "East Gate"},
            ],
        }, team=0)
        self.assertEqual(result["status"], 30)
        self.assertEqual(result["entrance"], "East Gate")
        self.assertEqual(result["item_flags"], 3)
        self.assertEqual(result["team"], 0)

    def test_baselines_old_hints_silently_then_detects_setreply_update(self):
        state = HintRelayState(self.path)
        old = hint()
        self.assertEqual(state.observe_snapshot("store", [old]), 0)
        new = hint(location=43, item=100)
        self.assertEqual(state.observe_snapshot("store", [old, new]), 1)
        self.assertEqual(state.pending_items(), [(hint_identity(new), new)])

    def test_receiver_and_finder_snapshots_and_mutable_status_dedupe(self):
        state = HintRelayState(self.path)
        item = hint()
        self.assertEqual(state.observe_snapshot("receiver", [item]), 0)
        self.assertEqual(state.observe_snapshot("finder", [item]), 0)
        state.observe_snapshot("receiver", [dict(item, status=40, found=True)])
        self.assertEqual(state.pending_items(), [])

    def test_reconnect_and_process_restart_catch_new_hints_without_repeating_old(self):
        first = HintRelayState(self.path)
        old = hint()
        first.observe_snapshot("store", [old])
        first.observe_snapshot("store", [old, hint(location=43)])
        identity, _ = first.pending_items()[0]
        first.mark_delivered(identity)

        restarted = HintRelayState(self.path)
        self.assertEqual(restarted.observe_snapshot("store", [old, hint(location=43)]), 0)
        self.assertEqual(restarted.observe_snapshot("store", [old, hint(location=43), hint(location=44)]), 1)
        self.assertEqual(len(restarted.pending_items()), 1)

    def test_explicit_result_can_announce_a_previously_baselined_hint_once(self):
        state = HintRelayState(self.path)
        item = hint()
        state.observe_snapshot("store", [item])
        self.assertTrue(state.enqueue_requested(item))
        self.assertFalse(state.enqueue_requested(dict(item, status=40, found=True)))
        state.deliver_pending(lambda delivered: None)
        self.assertFalse(state.enqueue_requested(item))

    def test_delivery_failure_remains_pending_and_retry_acknowledges(self):
        state = HintRelayState(self.path)
        item = hint()
        state.observe_snapshot("store", [item])
        state.observe_snapshot("store", [item, hint(location=43)])
        def fail(_):
            raise RuntimeError("discord unavailable")
        with self.assertRaises(RuntimeError):
            state.deliver_pending(fail)
        self.assertEqual(len(state.pending_items()), 1)
        state.deliver_pending(lambda _: None)
        self.assertEqual(state.pending_items(), [])
        reloaded = HintRelayState(self.path)
        self.assertEqual(reloaded.data["announced"], [hint_identity(hint(location=43))])

    def test_corrupt_state_safely_silent_baselines(self):
        Path(self.path).write_text("{broken", encoding="utf-8")
        state = HintRelayState(self.path)
        self.assertTrue(state.corrupt)
        self.assertEqual(state.observe_snapshot("store", [hint()]), 0)
        self.assertEqual(state.pending_items(), [])

    def test_status_mapping_keeps_unknown_status_readable(self):
        self.assertEqual(format_hint_status(30), "Priority")
        self.assertEqual(format_hint_status(997), "Status 997")
        self.assertEqual(format_hint_status(None, True), "Found")

    def test_template_uses_literal_placeholders_and_sanitizes_mentions(self):
        from modules.hint_relay import render_hint_message
        text = render_hint_message(
            "✨ {receiver}: {item} at {location} {entrance}", "plain",
            {"receiver": "@everyone", "item": "A {location}", "location": "Cave", "entrance": ""},
        )
        self.assertEqual(text, "✨ @\u200beveryone: A {location} at Cave ")
        self.assertEqual(render_hint_message("  ", "plain {receiver}", {"receiver": "A"}), "plain A")
        self.assertEqual(render_hint_message(7, "plain {receiver}", {"receiver": "A"}), "plain A")

    def test_real_shaped_hint_identity_ignores_found_and_status(self):
        record = {
            "receiving_player": 2, "finding_player": 1, "location": 42,
            "item": 99, "found": False, "entrance": "East Gate",
            "item_flags": 1, "status": 30, "team": 0,
        }
        self.assertEqual(hint_identity(record), hint_identity(dict(record, found=True, status=40)))


def load_tracker_client():
    tree = ast.parse((Path(__file__).resolve().parent / "bridgeipelago.py").read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "TrackerClient")
    class FakeQueue:
        def __init__(self):
            self.messages = []
        def put(self, message):
            self.messages.append(message)
    namespace = {
        "CoreConfig": {"RelayConfig": {"HintMessages": True}, "ArchipelagoConfig": {
            "ArchipelagoServer": "localhost", "ArchipelagoPort": 38281, "ArchipelagoBotSlot": "bot",
        }, "AdvancedConfig": {"DebugMode": False, "QueueOverclock": 1}},
        "HintRelayTeam": None, "chat_queue": FakeQueue(), "websocket_queue": Mock(),
        "seppuku_queue": Mock(), "item_queue": Mock(), "death_queue": Mock(),
        "discordbridge_queue": SimpleNamespace(empty=lambda: True),
        "GetArchPassword": lambda: "", "typing": SimpleNamespace(Any=object), "Enum": Enum,
        "uuid": SimpleNamespace(getnode=lambda: 1), "ClientConnection": object,
        "Thread": threading.Thread, "Event": threading.Event, "json": json,
        "time": SimpleNamespace(monotonic=lambda: 0), "QueueRoomWakeNotice": Mock(),
        "WriteArchConnectionJSON": Mock(), "WriteToErrorLog": Mock(),
        "hint_subscription_keys": hint_subscription_keys,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace["TrackerClient"], namespace


def load_hint_client(relay_config):
    tree = ast.parse((Path(__file__).resolve().parent / "bridgeipelago.py").read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "HintClient")
    class FakeQueue:
        def __init__(self):
            self.messages = []
        def put(self, message):
            self.messages.append(message)
        def get(self):
            return "slot"
    namespace = {
        "CoreConfig": {"RelayConfig": relay_config, "ArchipelagoConfig": {
            "ArchipelagoServer": "localhost", "ArchipelagoPort": 38281,
        }, "AdvancedConfig": {"DebugMode": False}},
        "HintRelayTeam": 0, "chat_queue": FakeQueue(), "hintprocessing_queue": FakeQueue(),
        "websocket_queue": FakeQueue(), "GetArchPassword": lambda: "",
        "typing": SimpleNamespace(Any=object), "Enum": Enum,
        "uuid": SimpleNamespace(getnode=lambda: 1), "ClientConnection": object,
        "Thread": threading.Thread, "Event": threading.Event, "json": json,
        "time": SimpleNamespace(sleep=lambda _: None), "QueueRoomUnavailableNotice": Mock(),
        "WriteToErrorLog": Mock(),
        "websockets": SimpleNamespace(exceptions=SimpleNamespace(
            ConnectionClosedError=type("ConnectionClosedError", (Exception,), {}),
            ConnectionClosed=type("ConnectionClosed", (Exception,), {}),
            InvalidState=type("InvalidState", (Exception,), {}),
        )),
        "normalize_requested_hint": normalize_requested_hint,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace["HintClient"], namespace


class TrackerHintProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "relay.json")

    def tearDown(self):
        self.temp.cleanup()

    def run_protocol(self, packets, relay_config=None):
        client_type, ns = load_tracker_client()
        if relay_config is not None:
            ns["CoreConfig"]["RelayConfig"] = relay_config
        client = client_type.__new__(client_type)
        client.is_closed = threading.Event()
        client.is_closed.clear()
        client.ap_connection = Mock()
        client.ap_connection.recv = Mock(side_effect=lambda timeout=None: (client.is_closed.set(), json.dumps(packets))[1])
        client.hint_baseline_keys = set()
        client.hint_store_keys = set()
        client.hint_prebaseline_replies = {}
        client.run()
        return ns, client

    @staticmethod
    def hint_dict(location=42):
        return {"receiving_player": 2, "finding_player": 1, "location": location,
                "item": 99, "found": False, "entrance": "East Gate", "item_flags": 1,
                "status": 30, "class": "Hint"}

    @staticmethod
    def connected():
        return {"cmd": "Connected", "team": 0, "slot": 1,
                "players": [{"team": 0, "slot": 1}, {"team": 0, "slot": 2}, {"team": 1, "slot": 1}],
                "slot_info": {"1": {"group_members": []}, "2": {"group_members": []},
                              "3": {"group_members": [1, "2"]}}}

    def test_connected_retrieved_then_setreply_uses_real_protocol_envelopes(self):
        key = "_read_hints_0_1"
        old = self.hint_dict()
        new = self.hint_dict(43)
        ns, client = self.run_protocol([
            self.connected(),
            {"cmd": "Retrieved", "keys": {key: [old]}},
            {"cmd": "SetReply", "key": key, "value": [old, new]},
        ])
        self.assertEqual(json.loads(client.ap_connection.send.call_args.args[0]), [
            {"cmd": "SetNotify", "keys": ["_read_hints_0_1", "_read_hints_0_2", "_read_hints_0_3"]},
            {"cmd": "Get", "keys": ["_read_hints_0_1", "_read_hints_0_2", "_read_hints_0_3"]},
        ])
        self.assertEqual([message["type"] for message in ns["chat_queue"].messages], ["HINT_SNAPSHOT", "HINT_SNAPSHOT"])
        state = HintRelayState(self.path)
        state.observe_snapshot(key, ns["chat_queue"].messages[0]["data"]["hints"])
        self.assertEqual(state.observe_snapshot(key, ns["chat_queue"].messages[1]["data"]["hints"]), 1)

    def test_setreply_before_retrieved_is_buffered_and_replayed_after_baseline(self):
        key = "_read_hints_0_1"
        old = self.hint_dict()
        new = self.hint_dict(43)
        ns, _ = self.run_protocol([
            self.connected(),
            {"cmd": "SetReply", "key": key, "value": [old, new]},
            {"cmd": "Retrieved", "keys": {key: [old]}},
        ])
        messages = ns["chat_queue"].messages
        self.assertEqual([message["data"]["hints"] for message in messages], [
            [dict(old, team=0)], [dict(old, team=0), dict(new, team=0)],
        ])
        state = HintRelayState(self.path)
        state.observe_snapshot(key, messages[0]["data"]["hints"])
        self.assertEqual(state.observe_snapshot(key, messages[1]["data"]["hints"]), 1)

    def test_missing_hint_flag_keeps_tracker_subscription_disabled(self):
        ns, client = self.run_protocol([self.connected()], relay_config={})
        client.ap_connection.send.assert_not_called()
        self.assertEqual(ns["chat_queue"].messages, [])

    def test_hintclient_uses_shared_dedupe_only_when_enabled_and_keeps_command_result(self):
        connected = {"cmd": "Connected", "team": 1, "slot": 3}
        hint_message = {
            "cmd": "PrintJSON", "type": "Hint", "receiving": 2, "found": False,
            "item": {"item": 99, "location": 42, "player": 1, "flags": 3},
            "data": [{"type": "hint_status", "hint_status": 30, "text": "(priority)"},
                     {"type": "entrance_name", "text": "East Gate"}],
        }
        command_result = {"cmd": "PrintJSON", "type": "CommandResult", "data": [{"text": "Hint costs 5 points."}]}
        client_type, ns = load_hint_client({"HintMessages": True})
        client = client_type.__new__(client_type)
        client.is_closed = threading.Event()
        client.team = None
        client.hintqueuepayload = "slot | item"
        client.ap_connection = Mock()
        client.ap_connection.recv = Mock(side_effect=lambda timeout=None: (client.is_closed.set(), json.dumps([connected, hint_message, command_result]))[1])
        client.run()
        self.assertEqual([message["type"] for message in ns["chat_queue"].messages], ["HINT_REQUESTED", "HTMessage"])
        self.assertEqual(ns["chat_queue"].messages[0]["data"]["team"], 1)
        self.assertEqual(ns["chat_queue"].messages[0]["data"]["status"], 30)
        self.assertEqual(ns["chat_queue"].messages[0]["data"]["entrance"], "East Gate")
        self.assertEqual(ns["chat_queue"].messages[1]["data"], command_result)

        client_type, ns = load_hint_client({})
        client = client_type.__new__(client_type)
        client.is_closed = threading.Event()
        client.team = None
        client.hintqueuepayload = "slot | item"
        client.ap_connection = Mock()
        client.ap_connection.recv = Mock(side_effect=lambda timeout=None: (client.is_closed.set(), json.dumps([connected, hint_message, command_result]))[1])
        client.run()
        self.assertEqual([message["type"] for message in ns["chat_queue"].messages], ["HTMessage", "HTMessage"])


if __name__ == "__main__":
    unittest.main()
