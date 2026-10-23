import ast
import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from modules.item_notifications import ItemNotificationState

ROOT = Path(__file__).resolve().parent
SOURCE = ast.parse((ROOT / "bridgeipelago.py").read_text(encoding="utf-8"))


def load_functions(*names):
    nodes = [node for node in SOURCE.body if getattr(node, "name", None) in names]
    namespace = {"json": json, "hashlib": hashlib}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace


class ItemNotificationWiringTests(unittest.TestCase):
    def test_preferences_singleton_reloads_when_room_path_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = [os.path.join(directory, "room-a.json")]
            ns = load_functions("GetItemNotificationState")
            ns.update({"ItemNotificationStateInstance": None,
                       "GetCoreFiles": lambda key: path[0],
                       "ItemNotificationState": ItemNotificationState})
            first = ns["GetItemNotificationState"]()
            first.set_mode(100, "all")
            path[0] = os.path.join(directory, "room-b.json")
            second = ns["GetItemNotificationState"]()
            self.assertIsNot(first, second)
            self.assertEqual(second.recipients("Slot A", "Map", [100]), [])

    def test_item_validation_uses_real_write_data_package_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = {"slot_info": {"7": {"name": "Slot A", "game": "Test Game"}}}
            games = {"Test Game": {"item_name_to_id": {"Progressive Sword": 123, "Map": 456}}}
            for filename, data in (("connection.json", connection), ("games.json", games)):
                with open(os.path.join(directory, filename), "w", encoding="utf-8") as output:
                    json.dump(data, output)
            ns = load_functions("IsKnownItemForSlot")
            ns["GetCoreFiles"] = lambda key: os.path.join(directory, "connection.json" if key == "archconnectiondump" else "games.json")
            self.assertTrue(ns["IsKnownItemForSlot"]("Slot A", "Progressive Sword"))
            self.assertTrue(ns["IsKnownItemForSlot"]("Slot A", "progressive sword"))
            self.assertFalse(ns["IsKnownItemForSlot"]("Other", "Map"))

    def test_global_off_sends_no_ping_and_success_completes_watch(self):
        with tempfile.TemporaryDirectory() as directory:
            registrations = os.path.join(directory, "registrations")
            os.makedirs(registrations)
            with open(os.path.join(registrations, "Alice.json"), "w", encoding="utf-8") as output:
                json.dump(["Slot A"], output)
            state = ItemNotificationState(os.path.join(directory, "state.json"))
            state.bind_registration(100, "Alice", ["Slot A"])
            state.set_mode(100, "watched")
            state.add_watch(100, "Slot A", "Map")
            sent = AsyncMock()
            mentions = Mock(side_effect=lambda **kwargs: kwargs)
            ns = load_functions("SendItemBroadcast")
            ns.update({
                "MainChannel": SimpleNamespace(send=sent),
                "RegisteredUserIDsForSlot": lambda slot: state.registered_user_ids(slot, registrations),
                "GetItemNotificationState": lambda: state,
                "ItemNotificationsEnabled": lambda: False,
                "discord": SimpleNamespace(Object=lambda id: SimpleNamespace(id=id), AllowedMentions=type("AllowedMentions", (), {"none": staticmethod(Mock(return_value={"users": [], "roles": False, "everyone": False, "replied_user": False})), "__new__": lambda cls, **kwargs: kwargs})),
            })
            event = {"data": [{"text": "event"}]}
            asyncio.run(ns["SendItemBroadcast"]("item", "Slot A", "Map", event))
            sent.assert_awaited_once()
            self.assertEqual(sent.await_args.args[0], "item")
            allowed = sent.await_args.kwargs["allowed_mentions"]
            self.assertEqual(allowed["users"], [])
            self.assertFalse(allowed["roles"] or allowed["everyone"] or allowed["replied_user"])
            self.assertTrue(state.was_delivered(__import__("hashlib").sha256(json.dumps(event, sort_keys=True, default=str).encode()).hexdigest()))
            self.assertEqual(state.watches(100), [])

    def test_large_registered_ping_list_splits_safely_and_duplicate_event_sends_no_second_ping(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ItemNotificationState(os.path.join(directory, "state.json"))
            ids = list(range(100, 230))
            for user_id in ids:
                state.bind_registration(user_id, "Alice", ["Slot A"])
                state.set_mode(user_id, "all")
            sent = AsyncMock()
            allowed_none = Mock(return_value={"none": True})
            mention_allowed = Mock(side_effect=lambda **kwargs: kwargs)
            ns = load_functions("SendItemBroadcast")
            ns.update({
                "MainChannel": SimpleNamespace(send=sent),
                "RegisteredUserIDsForSlot": lambda slot: ids,
                "GetItemNotificationState": lambda: state,
                "ItemNotificationsEnabled": lambda: True,
                "discord": SimpleNamespace(Object=lambda id: SimpleNamespace(id=id), AllowedMentions=SimpleNamespace(none=allowed_none)),
            })
            ns["discord"].AllowedMentions = type("AllowedMentions", (), {"none": staticmethod(allowed_none), "__new__": lambda cls, **kwargs: mention_allowed(**kwargs)})
            event = {"data": [{"text": "many recipients"}]}
            asyncio.run(ns["SendItemBroadcast"]("item post", "Slot A", "Map", event))
            count = sent.await_count
            self.assertGreater(count, 1)
            for call in sent.await_args_list:
                self.assertLessEqual(len(call.args[0]), 2000)
                mention_ids = call.kwargs["allowed_mentions"].get("users", [])
                self.assertLessEqual(len(mention_ids), 100)
            self.assertTrue(sent.await_args_list[0].args[0].startswith("item post "))
            self.assertEqual(len(sent.await_args_list[0].kwargs["allowed_mentions"]["users"]), 100)
            pinged = {user.id for call in sent.await_args_list for user in call.kwargs["allowed_mentions"].get("users", [])}
            self.assertEqual(pinged, set(ids))
            asyncio.run(ns["SendItemBroadcast"]("item post", "Slot A", "Map", event))
            self.assertEqual(sent.await_count, count + 1)
            self.assertEqual(sent.await_args_list[-1].args[0], "item post")

    def test_normal_opted_in_ping_uses_one_post_with_item_and_user_mention(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ItemNotificationState(os.path.join(directory, "state.json"))
            state.bind_registration(100, "Alice", ["Slot A"])
            state.set_mode(100, "all")
            sent = AsyncMock()
            allowed = type("AllowedMentions", (), {"none": staticmethod(lambda: {}), "__new__": lambda cls, **kwargs: kwargs})
            ns = load_functions("SendItemBroadcast")
            ns.update({
                "MainChannel": SimpleNamespace(send=sent),
                "RegisteredUserIDsForSlot": lambda slot: [100],
                "GetItemNotificationState": lambda: state,
                "ItemNotificationsEnabled": lambda: True,
                "discord": SimpleNamespace(Object=lambda id: SimpleNamespace(id=id), AllowedMentions=allowed),
            })
            asyncio.run(ns["SendItemBroadcast"]("Alice found Map", "Slot A", "Map", {"event": "normal"}))
            sent.assert_awaited_once()
            self.assertEqual(sent.await_args.args[0], "Alice found Map <@100>")
            self.assertEqual([user.id for user in sent.await_args.kwargs["allowed_mentions"]["users"]], [100])

    def test_overflow_send_failure_preserves_base_post_but_does_not_mark_event(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ItemNotificationState(os.path.join(directory, "state.json"))
            ids = list(range(100, 230))
            for user_id in ids:
                state.bind_registration(user_id, "Alice", ["Slot A"])
                state.set_mode(user_id, "all")
            sent = AsyncMock(side_effect=[None, RuntimeError("overflow failed")])
            allowed = type("AllowedMentions", (), {"none": staticmethod(lambda: {}), "__new__": lambda cls, **kwargs: kwargs})
            ns = load_functions("SendItemBroadcast")
            ns.update({
                "MainChannel": SimpleNamespace(send=sent),
                "RegisteredUserIDsForSlot": lambda slot: ids,
                "GetItemNotificationState": lambda: state,
                "ItemNotificationsEnabled": lambda: True,
                "discord": SimpleNamespace(Object=lambda id: SimpleNamespace(id=id), AllowedMentions=allowed),
            })
            with self.assertRaises(RuntimeError):
                asyncio.run(ns["SendItemBroadcast"]("item post", "Slot A", "Map", {"event": "overflow"}))
            self.assertTrue(sent.await_args_list[0].args[0].startswith("item post "))
            self.assertEqual(len(sent.await_args_list[0].kwargs["allowed_mentions"]["users"]), 100)
            self.assertEqual(state.data["delivered"], [])

    def test_failed_discord_send_does_not_complete_watch_or_mark_event(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ItemNotificationState(os.path.join(directory, "state.json"))
            state.bind_registration(100, "Alice", ["Slot A"])
            state.set_mode(100, "watched")
            state.add_watch(100, "Slot A", "Map")
            ns = load_functions("SendItemBroadcast")
            ns.update({
                "MainChannel": SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("send failed"))),
                "RegisteredUserIDsForSlot": lambda slot: [100], "GetItemNotificationState": lambda: state,
                "ItemNotificationsEnabled": lambda: True,
                "discord": SimpleNamespace(Object=lambda id: SimpleNamespace(id=id), AllowedMentions=type("AllowedMentions", (), {"none": staticmethod(lambda: {"none": True}), "__new__": lambda cls, **kwargs: kwargs})),
            })
            with self.assertRaises(RuntimeError):
                asyncio.run(ns["SendItemBroadcast"]("item", "Slot A", "Map", {"event": 1}))
            self.assertEqual(state.watches(100), [{"slot": "Slot A", "item": "Map"}])
            self.assertEqual(state.data["delivered"], [])


if __name__ == "__main__":
    unittest.main()
