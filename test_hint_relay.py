import asyncio
import tempfile
import unittest
import ast
import json
import os
import threading
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from modules.hint_relay import (
    HintRelayState, MAX_DISCORD_TEXT_LENGTH, deliver_pending_hints,
    format_hint_status, hint_identity, hint_subscription_keys,
    normalize_requested_hint, render_hint_message, unused_corrupt_path,
)
from modules.item_notifications import ItemNotificationState, resolved_hint_matches


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeHTTPError(Exception):
    def __init__(self, status):
        super().__init__("rejected")
        self.status = status


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

        async def deliver(_):
            return None

        asyncio.run(deliver_pending_hints(state, deliver))
        self.assertFalse(state.enqueue_requested(item))

    def test_transient_delivery_failure_stays_pending_until_backoff_then_retries(self):
        clock = FakeClock()
        state = HintRelayState(self.path, clock=clock)
        item = hint()
        state.observe_snapshot("store", [item])
        state.observe_snapshot("store", [item, hint(location=43)])

        async def fail(_):
            raise RuntimeError("discord unavailable")

        self.assertEqual(asyncio.run(deliver_pending_hints(state, fail)), 0)
        self.assertEqual(len(state.pending_items()), 1)
        self.assertEqual(len(state.due_pending_items()), 0)
        clock.advance(3600)

        async def succeed(_):
            return None

        self.assertEqual(asyncio.run(deliver_pending_hints(state, succeed)), 1)
        self.assertEqual(state.pending_items(), [])
        reloaded = HintRelayState(self.path)
        self.assertEqual(reloaded.data["announced"], [hint_identity(hint(location=43))])

    def test_corrupt_state_safely_silent_baselines(self):
        Path(self.path).write_text("{broken", encoding="utf-8")
        state = HintRelayState(self.path)
        self.assertTrue(state.corrupt)
        self.assertEqual(state.observe_snapshot("store", [hint()]), 0)
        self.assertEqual(state.pending_items(), [])

    def test_corrupt_state_is_moved_aside_before_the_first_write(self):
        Path(self.path).write_text("{broken", encoding="utf-8")
        state = HintRelayState(self.path)
        self.assertEqual(state.corrupt_backup, self.path + ".corrupt")
        self.assertEqual(state.observe_snapshot("store", [hint()]), 0)
        self.assertEqual(Path(self.path + ".corrupt").read_text(encoding="utf-8"), "{broken")
        self.assertIn("store", json.loads(Path(self.path).read_text(encoding="utf-8"))["snapshots"])

    def test_earlier_corrupt_copies_are_not_overwritten(self):
        Path(self.path + ".corrupt").write_text("earlier copy", encoding="utf-8")
        Path(self.path).write_text("{broken", encoding="utf-8")
        state = HintRelayState(self.path)
        self.assertEqual(state.corrupt_backup, self.path + ".corrupt.1")
        self.assertEqual(Path(self.path + ".corrupt").read_text(encoding="utf-8"), "earlier copy")
        self.assertEqual(Path(self.path + ".corrupt.1").read_text(encoding="utf-8"), "{broken")

    def test_unmovable_corrupt_state_blocks_writes_instead_of_overwriting(self):
        Path(self.path).write_text("{broken", encoding="utf-8")
        with patch("modules.hint_relay.os.replace", side_effect=OSError("file in use")):
            state = HintRelayState(self.path)
            with self.assertRaises(OSError):
                state.observe_snapshot("store", [hint()])
        self.assertEqual(Path(self.path).read_text(encoding="utf-8"), "{broken")
        self.assertIsNone(state.corrupt_backup)
        self.assertIn("file in use", state.corrupt_error)
        # The move is retried before the next write, which then succeeds.
        self.assertEqual(state.observe_snapshot("store", [hint()]), 0)
        self.assertEqual(Path(self.path + ".corrupt").read_text(encoding="utf-8"), "{broken")

    def test_unused_corrupt_path_numbers_past_existing_copies(self):
        base = str(Path(self.temp.name) / "relay.json")
        self.assertEqual(unused_corrupt_path(base), base + ".corrupt")
        Path(base + ".corrupt").write_text("a", encoding="utf-8")
        Path(base + ".corrupt.1").write_text("b", encoding="utf-8")
        self.assertEqual(unused_corrupt_path(base), base + ".corrupt.2")

    def test_status_mapping_keeps_unknown_status_readable(self):
        self.assertEqual(format_hint_status(30), "Priority")
        self.assertEqual(format_hint_status(997), "Status 997")
        self.assertEqual(format_hint_status(None, True), "Found")

    def test_template_uses_literal_placeholders_and_sanitizes_mentions(self):
        text = render_hint_message(
            "✨ {receiver}: {item} at {location} {entrance}", "plain",
            {"receiver": "@everyone", "item": "A {location}", "location": "Cave", "entrance": ""},
        )
        self.assertEqual(text, "✨ @\u200beveryone: A {location} at Cave ")
        self.assertEqual(render_hint_message("  ", "plain {receiver}", {"receiver": "A"}), "plain A")
        self.assertEqual(render_hint_message(7, "plain {receiver}", {"receiver": "A"}), "plain A")

    def test_rendered_discord_text_is_capped_at_2000_characters(self):
        text = render_hint_message("x" * 5000, "fallback", {})
        self.assertEqual(len(text), MAX_DISCORD_TEXT_LENGTH)
        self.assertEqual(len(render_hint_message("y" * 2000, "fallback", {})), 2000)

    def test_real_shaped_hint_identity_ignores_found_and_status(self):
        record = {
            "receiving_player": 2, "finding_player": 1, "location": 42,
            "item": 99, "found": False, "entrance": "East Gate",
            "item_flags": 1, "status": 30, "team": 0,
        }
        self.assertEqual(hint_identity(record), hint_identity(dict(record, found=True, status=40)))

    def test_permanent_http_400_and_403_deadletter_without_blocking_good_items(self):
        for status in (400, 403):
            with self.subTest(status=status):
                path = str(Path(self.temp.name) / "relay-{}.json".format(status))
                state = HintRelayState(path)
                bad = hint(location=1)
                good = hint(location=2)
                state.enqueue_requested(bad)
                state.enqueue_requested(good)
                sent = []
                logs = []

                async def send(item):
                    if item["location"] == 1:
                        raise FakeHTTPError(status)
                    sent.append(item["location"])

                delivered = asyncio.run(deliver_pending_hints(state, send, log=logs.append))
                self.assertEqual(delivered, 1)
                self.assertEqual(sent, [2])
                dead = dict(state.deadletter_items())
                self.assertIn(hint_identity(bad), dead)
                self.assertEqual(dead[hint_identity(bad)]["reason"], "http_{}".format(status))
                self.assertEqual(dead[hint_identity(bad)]["attempts"], 1)
                self.assertNotIn("entrance", dead[hint_identity(bad)])
                self.assertNotIn("item", dead[hint_identity(bad)])
                self.assertEqual(state.pending_items(), [])
                self.assertEqual(len(logs), 1)
                self.assertIn("HTTP {}".format(status), logs[0])
                # Logs must not leak the rendered hint or its payload.
                self.assertNotIn("Entrance", logs[0])
                self.assertNotIn("Hint result", logs[0])

    def test_transient_backoff_persists_and_recovers_after_restart(self):
        clock = FakeClock(1000.0)
        options = dict(
            clock=clock, max_attempts=3,
            retry_base_seconds=5.0, retry_max_seconds=60.0,
        )
        state = HintRelayState(self.path, **options)
        item = hint()
        state.enqueue_requested(item)
        identity = hint_identity(item)
        calls = []

        async def fail(_):
            calls.append("fail")
            raise RuntimeError("temporary")

        self.assertEqual(asyncio.run(deliver_pending_hints(state, fail)), 0)
        self.assertEqual(calls, ["fail"])
        retry = state.data["retries"][identity]
        self.assertEqual(retry["attempts"], 1)
        self.assertEqual(retry["next_attempt"], 1005.0)

        restarted = HintRelayState(self.path, **options)
        self.assertEqual(restarted.due_pending_items(), [])
        self.assertEqual(asyncio.run(deliver_pending_hints(restarted, fail)), 0)
        self.assertEqual(calls, ["fail"])

        clock.advance(5.0)
        sent = []

        async def succeed(delivered):
            sent.append(delivered)

        self.assertEqual(asyncio.run(deliver_pending_hints(restarted, succeed)), 1)
        self.assertEqual(sent, [item])
        self.assertEqual(restarted.pending_items(), [])
        self.assertEqual(restarted.data["retries"], {})

        reloaded = HintRelayState(self.path, **options)
        self.assertIn(identity, reloaded.data["announced"])

    def test_repeated_transient_failures_deadletter_after_bounded_attempts(self):
        clock = FakeClock(0.0)
        state = HintRelayState(
            self.path, clock=clock, max_attempts=3,
            retry_base_seconds=1.0, retry_max_seconds=2.0,
        )
        item = hint()
        state.enqueue_requested(item)
        identity = hint_identity(item)

        async def fail(_):
            raise RuntimeError("still unavailable")

        for expected in (1, 2):
            asyncio.run(deliver_pending_hints(state, fail))
            self.assertEqual(state.data["retries"][identity]["attempts"], expected)
            clock.advance(10.0)
        self.assertEqual(asyncio.run(deliver_pending_hints(state, fail)), 0)
        self.assertEqual(state.pending_items(), [])
        dead = dict(state.deadletter_items())[identity]
        self.assertEqual(dead["attempts"], 3)
        self.assertEqual(dead["reason"], "retry_limit")

    def test_default_retry_window_deadletters_after_eleven_attempts_over_eighteen_minutes(self):
        clock = FakeClock(0.0)
        state = HintRelayState(self.path, clock=clock)
        item = hint()
        state.enqueue_requested(item)
        identity = hint_identity(item)
        sends = []

        async def fail(_):
            sends.append(clock.now)
            raise RuntimeError("unavailable")

        self.assertEqual((state.max_attempts, state.retry_max_seconds), (11, 300.0))
        for _ in range(state.max_attempts + 2):
            asyncio.run(deliver_pending_hints(state, fail))
            if not state.pending_items():
                break
            clock.now = state.data["retries"][identity]["next_attempt"]
        self.assertEqual(len(sends), 11)
        gaps = [later - earlier for earlier, later in zip(sends, sends[1:])]
        self.assertEqual(gaps, [2, 4, 8, 16, 32, 64, 128, 256, 300, 300])
        self.assertEqual(sends[-1] - sends[0], 1110)
        dead = dict(state.deadletter_items())[identity]
        self.assertEqual((dead["attempts"], dead["reason"]), (11, "retry_limit"))

    def test_unpersisted_acknowledgment_is_retried_without_resending_in_this_process(self):
        state = HintRelayState(self.path)
        item = hint()
        state.enqueue_requested(item)
        identity = hint_identity(item)
        sends = []

        async def send(delivered):
            sends.append(delivered)

        with patch.object(state, "_save_data", side_effect=OSError("disk full")):
            self.assertEqual(asyncio.run(deliver_pending_hints(state, send)), 0)
            self.assertEqual(asyncio.run(deliver_pending_hints(state, send)), 0)
            # A restart cannot know the acknowledgment was lost, so the result stays pending on disk.
            self.assertEqual(HintRelayState(self.path).pending_items(), [(identity, item)])
        self.assertEqual(sends, [item])
        self.assertEqual(state.pending_items(), [(identity, item)])

        self.assertEqual(asyncio.run(deliver_pending_hints(state, send)), 1)
        self.assertEqual(sends, [item])
        self.assertEqual(state.pending_items(), [])
        self.assertIn(identity, HintRelayState(self.path).data["announced"])

    def test_failed_retry_write_still_backs_off_in_this_process(self):
        clock = FakeClock(1000.0)
        state = HintRelayState(self.path, clock=clock)
        item = hint()
        state.enqueue_requested(item)
        identity = hint_identity(item)
        calls = []

        async def fail(_):
            calls.append("fail")
            raise RuntimeError("temporary")

        with patch.object(state, "_save_data", side_effect=OSError("disk full")):
            self.assertEqual(asyncio.run(deliver_pending_hints(state, fail)), 0)
            self.assertEqual(asyncio.run(deliver_pending_hints(state, fail)), 0)
        self.assertEqual(calls, ["fail"])
        self.assertEqual(state.data["retries"][identity]["attempts"], 1)
        self.assertEqual(state.data["retries"][identity]["next_attempt"], 1002.0)

        clock.advance(2.0)
        self.assertEqual(asyncio.run(deliver_pending_hints(state, fail)), 0)
        self.assertEqual(calls, ["fail", "fail"])

    def test_failed_deadletter_write_is_not_retried_in_this_process(self):
        state = HintRelayState(self.path)
        item = hint()
        state.enqueue_requested(item)
        identity = hint_identity(item)
        calls = []

        async def reject(_):
            calls.append("reject")
            raise FakeHTTPError(400)

        with patch.object(state, "_save_data", side_effect=OSError("disk full")):
            asyncio.run(deliver_pending_hints(state, reject))
            asyncio.run(deliver_pending_hints(state, reject))
        self.assertEqual(calls, ["reject"])
        self.assertEqual(state.pending_items(), [])
        self.assertIn(identity, dict(state.deadletter_items()))

    def test_delivery_issue_logs_are_throttled_between_backoff_attempts(self):
        clock = FakeClock(0.0)
        state = HintRelayState(
            self.path, clock=clock, max_attempts=50,
            retry_base_seconds=0.0, retry_max_seconds=0.0,
            log_interval_seconds=60.0,
        )
        state.enqueue_requested(hint())
        logs = []

        async def fail(_):
            raise RuntimeError("temporary")

        asyncio.run(deliver_pending_hints(state, fail, log=logs.append))
        asyncio.run(deliver_pending_hints(state, fail, log=logs.append))
        self.assertEqual(len(logs), 1)
        clock.advance(61.0)
        asyncio.run(deliver_pending_hints(state, fail, log=logs.append))
        self.assertEqual(len(logs), 2)

    def test_failed_state_write_does_not_drop_pending_in_memory(self):
        state = HintRelayState(self.path)
        item = hint()
        state.enqueue_requested(item)
        identity = hint_identity(item)
        before = json.loads(json.dumps(state.data))

        with patch.object(state, "_save_data", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                state.mark_delivered(identity)
            with self.assertRaises(OSError):
                state.observe_snapshot("store", [hint(location=50)])

        self.assertEqual(state.pending_items(), [(identity, item)])
        self.assertEqual(state.data, before)
        self.assertEqual(HintRelayState(self.path).pending_items(), [(identity, item)])

    def test_legacy_v1_state_without_delivery_metadata_loads_and_retries(self):
        item = hint()
        legacy = {
            "version": 1,
            "snapshots": {},
            "announced": [],
            "pending": {hint_identity(item): item},
        }
        Path(self.path).write_text(json.dumps(legacy), encoding="utf-8")
        state = HintRelayState(self.path)
        self.assertFalse(state.corrupt)
        self.assertEqual(state.data["retries"], {})
        self.assertEqual(state.data["dead"], {})
        self.assertEqual(len(state.due_pending_items()), 1)


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
        "HintWatchMatchesRequest": lambda context, hint: resolved_hint_matches(
            context.get("item", ""), hint.get("item"),
            {"Mask Fragment: Left": 99, "Mask Fragment: Dragon": 100, "Map": 1},
        ),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace["HintClient"], namespace


def load_bridge_functions(names, namespace):
    tree = ast.parse((Path(__file__).resolve().parent / "bridgeipelago.py").read_text(encoding="utf-8"))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            body.append(node)
    exec(compile(ast.Module(body=body, type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace


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

    def test_null_retrieved_store_is_an_empty_baseline_then_setreply_announces(self):
        key = "_read_hints_0_1"
        new = self.hint_dict(43)
        ns, client = self.run_protocol([
            self.connected(),
            {"cmd": "Retrieved", "keys": {key: None}},
            {"cmd": "SetReply", "key": key, "value": [new]},
        ])
        messages = ns["chat_queue"].messages
        self.assertEqual([message["data"]["hints"] for message in messages], [
            [], [dict(new, team=0)],
        ])
        self.assertNotIn(key, client.hint_baseline_keys)
        state = HintRelayState(self.path)
        self.assertEqual(state.observe_snapshot(key, messages[0]["data"]["hints"]), 0)
        self.assertEqual(state.observe_snapshot(key, messages[1]["data"]["hints"]), 1)

    def test_retrieved_does_not_baseline_unsubscribed_keys(self):
        key = "_read_hints_0_1"
        ns, client = self.run_protocol([
            self.connected(),
            {"cmd": "Retrieved", "keys": {
                "_read_hints_9_9": None,
                key: None,
            }},
        ])
        messages = ns["chat_queue"].messages
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["data"]["key"], key)
        self.assertEqual(messages[0]["data"]["hints"], [])
        self.assertNotIn(key, client.hint_baseline_keys)
        self.assertNotIn("_read_hints_9_9", client.hint_baseline_keys)

    def test_prebaseline_setreply_buffer_is_bounded_and_keeps_newest(self):
        key = "_read_hints_0_1"
        old = self.hint_dict()
        packets = [self.connected()]
        for location in range(100, 120):
            packets.append({"cmd": "SetReply", "key": key, "value": [self.hint_dict(location)]})
        packets.append({"cmd": "Retrieved", "keys": {key: [old]}})
        ns, client = self.run_protocol(packets)
        messages = ns["chat_queue"].messages
        limit = client.HINT_PREBASELINE_REPLY_LIMIT
        self.assertEqual(len(messages), 1 + limit)
        self.assertEqual(messages[0]["data"]["hints"], [dict(old, team=0)])
        replayed = [message["data"]["hints"][0]["location"] for message in messages[1:]]
        self.assertEqual(replayed, list(range(120 - limit, 120)))

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

    def test_typoed_request_resolves_canonical_watch_when_hint_relay_is_disabled(self):
        client_type, ns = load_hint_client({})
        ns["LookupSlot"] = lambda slot_id: {"2": "Slot B"}.get(str(slot_id), "NULL")
        context = {"requester_id": 100, "requester_name": "Alice", "registered_at_request": True,
                   "slot": "Slot B", "item": "Mask Fragement: Left"}
        client = client_type.__new__(client_type)
        client.is_closed = threading.Event()
        client.team = None
        client.connected_slot = None
        client.request_context = context
        client.hintqueuepayload = "Mask Fragement: Left"
        client.ap_connection = Mock()
        response = {"cmd": "PrintJSON", "type": "Hint", "receiving": 2, "found": False,
                    "item": {"item": 99, "location": 42, "player": 1, "flags": 0}, "data": []}
        client.ap_connection.recv = Mock(side_effect=lambda timeout=None: (
            client.is_closed.set(), json.dumps([
                {"cmd": "Connected", "team": 0, "slot": 2}, response,
            ])
        )[1])
        client.run()
        watch_events = [message for message in ns["chat_queue"].messages
                        if message["type"] == "HINT_WATCH_RESOLVED"]
        self.assertEqual(len(watch_events), 1)
        event = watch_events[0]

        with tempfile.TemporaryDirectory() as directory:
            state = ItemNotificationState(str(Path(directory) / "items.json"))
            state.bind_registration(100, "Alice", ["Slot B"])
            state.set_mode(100, "watched")
            state_file = os.path.join(directory, "Alice.json")
            Path(state_file).write_text('["Slot B"]', encoding="utf-8")
            bridge = load_bridge_functions({"HandleHintRelayMessage"}, {
                "InitializeHintRelayState": Mock(side_effect=AssertionError("relay must stay disabled")),
                "RegisteredUserIDsForSlot": lambda slot: state.registered_user_ids(slot, directory),
                "LookupGame": lambda slot_id: "Test Game",
                "LookupItem": lambda game, item_id: {"99": "Mask Fragment: Left"}.get(str(item_id), "NULL"),
                "GetItemNotificationState": lambda: state,
                "DeliverPendingHintResults": AsyncMock(),
                "ReportHintRelayDeliveryIssue": Mock(),
            })
            asyncio.run(bridge["HandleHintRelayMessage"](event))
            self.assertEqual(state.watches(100), [{"slot": "Slot B", "item": "Mask Fragment: Left"}])

            item_state = state
            sent = AsyncMock()
            class AllowedMentions:
                @staticmethod
                def none():
                    return {"users": []}
                def __new__(cls, **kwargs):
                    return kwargs
            filter_ns = load_bridge_functions({"ItemEventKey", "HasActiveItemWatch", "ItemPassesChannelFilter", "SendItemBroadcast"}, {
                "hashlib": __import__("hashlib"), "json": json,
                "GetItemNotificationState": lambda: item_state,
                "RegisteredUserIDsForSlot": lambda slot: item_state.registered_user_ids(slot, directory),
                "CoreConfig": {"ItemFilterConfig": {"BotItemSpoilTraps": False, "BotItemFilterLevel": 2}},
                "ItemFilter": lambda itemclass, level: bool(itemclass & 1),
                "MainChannel": SimpleNamespace(send=sent),
                "ItemNotificationsEnabled": lambda: True,
                "discord": SimpleNamespace(Object=lambda id: SimpleNamespace(id=id), AllowedMentions=AllowedMentions),
            })
            self.assertTrue(filter_ns["ItemPassesChannelFilter"](0, "Slot B", "Mask Fragment: Left", {"event": "new filler"}))
            self.assertFalse(filter_ns["ItemPassesChannelFilter"](0, "Slot B", "Unwatched filler"))
            self.assertFalse(filter_ns["ItemPassesChannelFilter"](4, "Slot B", "Mask Fragment: Left"))
            asyncio.run(filter_ns["SendItemBroadcast"](
                "Slot B found Mask Fragment: Left", "Slot B", "Mask Fragment: Left", {"event": "new filler"}
            ))
            sent.assert_awaited_once()
            self.assertEqual(sent.await_args.args[0], "Slot B found Mask Fragment: Left <@100>")
            self.assertEqual([user.id for user in sent.await_args.kwargs["allowed_mentions"]["users"]], [100])

    def test_concurrent_same_slot_hints_only_watch_each_requesters_item(self):
        for user_id, query, expected_id in (
                (100, "Mask Fragement: Left", 99),
                (200, "Mask Fragment: Dragon", 100)):
            with self.subTest(query=query):
                client_type, ns = load_hint_client({})
                ns["LookupSlot"] = lambda slot_id: "Slot B"
                client = client_type.__new__(client_type)
                client.is_closed = threading.Event()
                client.request_context = {
                    "requester_id": user_id, "registered_at_request": True,
                    "slot": "Slot B", "item": query,
                }
                client.hintqueuepayload = query
                packets = [{"cmd": "Connected", "team": 0, "slot": 2}] + [
                    {"cmd": "PrintJSON", "type": "Hint", "receiving": 2, "found": False,
                     "item": {"item": item_id, "location": item_id, "player": 1, "flags": 0}}
                    for item_id in (99, 100, 1)
                ]
                client.ap_connection = Mock()
                client.ap_connection.recv.side_effect = lambda timeout=None: (
                    client.is_closed.set(), json.dumps(packets)
                )[1]
                client.run()
                events = [message["data"] for message in ns["chat_queue"].messages
                          if message["type"] == "HINT_WATCH_RESOLVED"]
                self.assertEqual([event["hint"]["item"] for event in events], [expected_id])
                self.assertEqual(events[0]["request_context"]["requester_id"], user_id)

    def test_watch_resolution_rejects_wrong_receiver_and_found_hints(self):
        client_type, ns = load_hint_client({})
        ns["LookupSlot"] = lambda slot_id: "Slot B" if str(slot_id) == "2" else "NULL"
        context = {"requester_id": 100, "registered_at_request": True, "slot": "Slot B", "item": "Map"}
        for receiver, found in ((3, False), (2, True), (2, None)):
            client = client_type.__new__(client_type)
            client.is_closed = threading.Event()
            client.team = None
            client.connected_slot = None
            client.request_context = context
            client.hintqueuepayload = "Map"
            client.ap_connection = Mock()
            response = {"cmd": "PrintJSON", "type": "Hint", "receiving": receiver, "found": found,
                        "item": {"item": 1, "location": 42, "player": 1, "flags": 0}, "data": []}
            packets = [{"cmd": "Connected", "team": 0, "slot": 2}]
            if found is not None:
                packets.append(response)
            else:
                packets.append({"cmd": "PrintJSON", "type": "CommandResult", "data": [{"text": "No hint found."}]})
            client.ap_connection.recv = Mock(side_effect=lambda timeout=None, packets=packets: (
                client.is_closed.set(), json.dumps(packets)
            )[1])
            client.run()
        self.assertFalse(any(message["type"] == "HINT_WATCH_RESOLVED" for message in ns["chat_queue"].messages))


class HintRelayStartupAndQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "relay.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_initialize_helper_loads_durable_pending_once(self):
        state = HintRelayState(self.path)
        state.enqueue_requested(hint())
        namespace = {
            "HintRelayState": HintRelayState,
            "GetCoreFiles": lambda _: self.path,
            "HintRelayStateInstance": None,
        }
        load_bridge_functions({"InitializeHintRelayState"}, namespace)
        instance = asyncio.run(namespace["InitializeHintRelayState"]())
        self.assertIsNotNone(instance)
        self.assertEqual(len(instance.pending_items()), 1)
        self.assertEqual(instance.pending_items()[0][0], hint_identity(hint()))
        self.assertIs(instance, namespace["HintRelayStateInstance"])
        self.assertIs(asyncio.run(namespace["InitializeHintRelayState"]()), instance)

    def test_bad_hint_does_not_block_normal_chat_or_spam_debug(self):
        state = HintRelayState(self.path)
        bad = hint(location=7)
        state.enqueue_requested(bad)
        sent = []
        debug = []
        logs = []

        class FakeQueue:
            def __init__(self, items):
                self.items = list(items)
            def empty(self):
                return not self.items
            def get(self):
                return self.items.pop(0)

        async def send_hint(_):
            raise FakeHTTPError(400)

        async def send_main(text):
            sent.append(text)

        async def send_debug(text):
            debug.append(text)

        namespace = {
            "CoreConfig": {"RelayConfig": {"HintMessages": True}},
            "HintRelayStateInstance": state,
            "chat_queue": FakeQueue([
                {"type": "CORE", "data": {"text": "normal chat"}, "flag": "None"},
            ]),
            "HandleHintRelayMessage": Mock(),
            "SendMainChannelMessage": send_main,
            "SendDebugChannelMessage": send_debug,
            "WriteToErrorLog": lambda module, message: None,
            "SendHintResult": send_hint,
            "deliver_pending_hints": deliver_pending_hints,
            "ReportHintRelayDeliveryIssue": logs.append,
            "HintRelayState": HintRelayState,
            "GetCoreFiles": lambda _: self.path,
        }
        load_bridge_functions(
            {"InitializeHintRelayState", "DeliverPendingHintResults", "ProcessChatQueue"},
            namespace,
        )
        asyncio.run(namespace["ProcessChatQueue"]())
        self.assertEqual(sent, ["normal chat"])
        self.assertEqual(debug, [])
        self.assertEqual(len(state.deadletter_items()), 1)
        self.assertEqual(len(logs), 1)
        self.assertIn("HTTP 400", logs[0])

    def test_watch_resolution_is_dispatched_with_hint_announcements_disabled(self):
        event = {"type": "HINT_WATCH_RESOLVED", "data": {"hint": {}, "request_context": {}, "slot": "Slot B"}}
        queue = type("FakeQueue", (), {
            "__init__": lambda self: setattr(self, "items", [event]),
            "empty": lambda self: not self.items,
            "get": lambda self: self.items.pop(0),
        })()
        handler = AsyncMock()
        namespace = {
            "CoreConfig": {"RelayConfig": {"HintMessages": False}},
            "chat_queue": queue,
            "HandleHintRelayMessage": handler,
            "WriteToErrorLog": Mock(),
            "SendDebugChannelMessage": AsyncMock(),
            "SendMainChannelMessage": AsyncMock(),
        }
        load_bridge_functions({"ProcessChatQueue"}, namespace)
        asyncio.run(namespace["ProcessChatQueue"]())
        handler.assert_awaited_once_with(event)

    def test_resolved_watch_requires_requester_to_remain_registered_for_requested_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ItemNotificationState(os.path.join(directory, "items.json"))
            state.bind_registration(100, "Alice", ["Slot B"])
            Path(directory, "Alice.json").write_text('["Slot B"]', encoding="utf-8")
            Path(directory, "Bob.json").write_text('["Slot B"]', encoding="utf-8")
            event = {"type": "HINT_WATCH_RESOLVED", "data": {
                "hint": {"receiving_player": 2, "item": 99}, "slot": "Slot B",
                "request_context": {"requester_id": 100, "registered_at_request": True},
            }}
            ns = load_bridge_functions({"HandleHintRelayMessage"}, {
                "RegisteredUserIDsForSlot": lambda slot: state.registered_user_ids(slot, directory),
                "LookupGame": lambda slot_id: "Test Game",
                "LookupItem": lambda game, item_id: "Map",
                "GetItemNotificationState": lambda: state,
                "ReportHintRelayDeliveryIssue": Mock(),
            })
            asyncio.run(ns["HandleHintRelayMessage"](event))
            self.assertEqual(state.watches(100), [{"slot": "Slot B", "item": "Map"}])

            # A live registration recheck blocks a result after revocation, and
            # a requester ID mismatch never transfers the watch to another registrant.
            state.remove_watch(100, "Slot B", "Map")
            Path(directory, "Alice.json").write_text("[]", encoding="utf-8")
            asyncio.run(ns["HandleHintRelayMessage"](event))
            self.assertEqual(state.watches(100), [])
            Path(directory, "Alice.json").write_text('["Slot B"]', encoding="utf-8")
            event["data"]["request_context"]["requester_id"] = 200
            asyncio.run(ns["HandleHintRelayMessage"](event))
            self.assertEqual(state.watches(100), [])

    def test_unknown_canonical_item_does_not_create_watch(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ItemNotificationState(os.path.join(directory, "items.json"))
            state.bind_registration(100, "Alice", ["Slot B"])
            Path(directory, "Alice.json").write_text('["Slot B"]', encoding="utf-8")
            namespace = {
                "RegisteredUserIDsForSlot": lambda slot: state.registered_user_ids(slot, directory),
                "LookupGame": lambda slot_id: "Test Game",
                "LookupItem": lambda game, item_id: "NULL",
                "GetItemNotificationState": lambda: state,
                "ReportHintRelayDeliveryIssue": Mock(),
            }
            load_bridge_functions({"HandleHintRelayMessage"}, namespace)
            event = {"type": "HINT_WATCH_RESOLVED", "data": {
                "hint": {"receiving_player": 2, "item": 999}, "slot": "Slot B",
                "request_context": {"requester_id": 100, "registered_at_request": True},
            }}
            asyncio.run(namespace["HandleHintRelayMessage"](event))
            self.assertEqual(state.watches(100), [])


if __name__ == "__main__":
    unittest.main()
