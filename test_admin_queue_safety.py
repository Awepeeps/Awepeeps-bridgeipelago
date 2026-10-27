"""Regression tests for admin gating, secret-safe output, queue paths, and mentions."""

import ast
import asyncio
import contextlib
import io
import json
import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from modules.config_store import save_config_atomic
from modules.hint_relay import unused_corrupt_path
from modules.player_commands import HINT_FIELD_MAX_LENGTH, HINT_NOT_REGISTERED_MESSAGE, register_player_commands

ROOT = Path(__file__).resolve().parent
SOURCE = ast.parse((ROOT / "bridgeipelago.py").read_text(encoding="utf-8"))
COMMAND_NAMES = sorted(
    node.name for node in SOURCE.body
    if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("Command_")
)


def top_level_name(node):
    """Name defined by a top-level def, class, or simple assignment, else None."""
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return getattr(node, "name", None)


def load(names, namespace, optional=()):
    """Compile the named top-level functions and constants from bridgeipelago.py into namespace."""
    wanted = set(names) | set(optional)
    nodes = []
    for node in SOURCE.body:
        if top_level_name(node) in wanted:
            if hasattr(node, "decorator_list"):
                node.decorator_list = []
            nodes.append(node)
    missing = set(names) - {top_level_name(node) for node in nodes}
    if missing:
        raise AssertionError("not defined in bridgeipelago.py: " + ", ".join(sorted(missing)))
    namespace.setdefault("WriteToErrorLog", Mock())
    namespace.setdefault("AlertMention", lambda: "")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    return namespace


def fake_member(administrator=False, manage_guild=False):
    permissions = SimpleNamespace(administrator=administrator, manage_guild=manage_guild)
    return SimpleNamespace(name="Player", guild_permissions=permissions, send=AsyncMock())


def fake_message(content, author, channel_id=42):
    channel = SimpleNamespace(id=channel_id, send=AsyncMock())
    return SimpleNamespace(content=content, author=author, channel=channel)


class FakeUser:
    def __init__(self, name="Player#1234", user_id=None):
        self.name = name
        self.send = AsyncMock()
        if user_id is not None:
            self.id = user_id

    def __str__(self):
        return self.name


def load_on_message():
    namespace = {
        "discord_client": SimpleNamespace(user=object()),
        "MainChannel": SimpleNamespace(id=42),
        "CoreConfig": {"DrawbridgeConfig": {"DiscordBridgeEnabled": False}},
        "SendMainChannelMessage": AsyncMock(),
        "SetConfigVariable": Mock(return_value="ok"),
        "ReloadBot": Mock(),
        "discordseppuku_queue": Mock(),
        "discordbridge_queue": Mock(),
        "hint_queue": Mock(),
        "HelpText": "help",
        "HINT_FIELD_MAX_LENGTH": HINT_FIELD_MAX_LENGTH,
        "HINT_NOT_REGISTERED_MESSAGE": HINT_NOT_REGISTERED_MESSAGE,
        "CanRequestHint": Mock(return_value=True),
        "IsKnownItemForSlot": Mock(return_value=True),
        "UpdateItemWatch": Mock(),
        "IsRegisteredForSlot": Mock(return_value=False),
        "BindItemNotificationRegistration": Mock(),
    }
    namespace.update({name: AsyncMock() for name in COMMAND_NAMES})
    namespace["Command_Register"].return_value = "You've been registered for Slot A!"
    namespace["PrefixCommandsEnabled"] = lambda: True
    return load(["on_message"], namespace, optional=["IsBotAdmin"])


class AdminGateTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_on_message()

    def run_as(self, content, author):
        asyncio.run(self.ns["on_message"](fake_message(content, author)))

    def test_manage_guild_admin_can_reload_tracker(self):
        self.run_as("$reloadtracker", fake_member(manage_guild=True))
        self.ns["ReloadBot"].assert_called_once_with()

    def test_admin_setconfig_passes_key_and_value(self):
        self.run_as("$setconfig ArchipelagoPort 38282", fake_member(administrator=True))
        self.ns["SetConfigVariable"].assert_called_once_with("ArchipelagoPort", "38282")

    def test_plain_member_cannot_reload_tracker(self):
        self.run_as("$reloadtracker", fake_member())
        self.ns["ReloadBot"].assert_not_called()

    def test_plain_member_cannot_reload_discord(self):
        self.run_as("$reloaddiscord", fake_member())
        self.ns["discordseppuku_queue"].put.assert_not_called()

    def test_plain_member_cannot_set_config(self):
        self.run_as("$setconfig ArchipelagoPort 38282", fake_member())
        self.ns["SetConfigVariable"].assert_not_called()

    def test_plain_member_cannot_run_archinfo(self):
        self.run_as("$archinfo", fake_member())
        self.ns["Command_ArchInfo"].assert_not_awaited()

    def test_disabled_prefix_toggle_blocks_leading_whitespace_commands(self):
        self.ns["PrefixCommandsEnabled"] = lambda: False
        self.run_as("   $register Slot A", fake_member())
        self.ns["SendMainChannelMessage"].assert_not_awaited()
        self.ns["Command_Register"].assert_not_awaited()

    def test_admin_setconfig_keeps_spaces_in_value(self):
        self.run_as("$setconfig ArchipelagoPassword two words", fake_member(administrator=True))
        self.ns["SetConfigVariable"].assert_called_once_with("ArchipelagoPassword", "two words")


class TextHintTests(unittest.TestCase):
    def run_as(self, content):
        ns = load_on_message()
        asyncio.run(ns["on_message"](fake_message(content, fake_member())))
        return ns

    def test_text_hint_with_empty_slot_or_item_gets_usage_reply(self):
        for content in ("$hint |x", "$hint Slot A |", "$hint |", "$hint ", "$hint", "$hint Slot A"):
            with self.subTest(content=content):
                ns = self.run_as(content)
                ns["hint_queue"].put.assert_not_called()
                ns["SendMainChannelMessage"].assert_awaited_once_with(
                    "Invalid $hint format. Use `$hint <slot>|<item>`")

    def test_text_hint_for_an_unregistered_slot_is_refused(self):
        ns = load_on_message()
        ns["CanRequestHint"].return_value = False
        author = fake_member()
        asyncio.run(ns["on_message"](fake_message("$hint Slot A | Item B", author)))
        ns["CanRequestHint"].assert_called_once_with(author, "Slot A")
        ns["hint_queue"].put.assert_not_called()
        ns["SendMainChannelMessage"].assert_awaited_once_with(HINT_NOT_REGISTERED_MESSAGE)

    def test_hints_list_command_is_not_parsed_as_a_hint_request(self):
        ns = self.run_as("$hints")
        ns["hint_queue"].put.assert_not_called()
        ns["Command_Hints"].assert_awaited_once()
        ns["SendMainChannelMessage"].assert_not_awaited()

    def test_text_hint_with_slot_and_item_is_queued(self):
        # Positive control for the empty-field checks above.
        ns = self.run_as("$hint Slot A | Item B")
        queued = ns["hint_queue"].put.call_args.args[0]
        self.assertEqual((queued["slot"], queued["item"]), ("Slot A", "Item B"))

    def test_text_hint_captures_registered_requester_before_queueing_without_arming_watch(self):
        ns = load_on_message()
        author = fake_member()
        author.id = 100
        ns["IsRegisteredForSlot"].return_value = True
        asyncio.run(ns["on_message"](fake_message("$hint Slot A | Mask Fragement: Left", author)))
        queued = ns["hint_queue"].put.call_args.args[0]
        self.assertEqual(queued["request_context"], {
            "requester_id": 100, "requester_name": str(author), "registered_at_request": True,
        })
        ns["BindItemNotificationRegistration"].assert_called_once_with(author)
        ns["UpdateItemWatch"].assert_not_called()

    def test_text_hint_over_100_characters_is_refused(self):
        for content in ("$hint " + "S" * 101 + " | Item B", "$hint Slot A | " + "I" * 101):
            with self.subTest(length=len(content)):
                ns = self.run_as(content)
                ns["hint_queue"].put.assert_not_called()
                ns["SendMainChannelMessage"].assert_awaited_once_with(
                    "Slot and item must each be 100 characters or fewer.")

    def test_text_hint_accepts_exactly_100_characters(self):
        ns = self.run_as("$hint " + "S" * 100 + " | " + "I" * 100)
        queued = ns["hint_queue"].put.call_args.args[0]
        self.assertEqual((queued["slot"], queued["item"]), ("S" * 100, "I" * 100))


class CanRequestHintTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ns = load(["CanRequestHint", "IsBotAdmin"], {
            "GetCoreDirectory": lambda folder: self.directory.name + "/",
            "json": json,
        })

    def write_registrations(self, contents):
        # Registration files are named after str(author), as Command_Register stores them.
        with open(os.path.join(self.directory.name, str(self.member) + ".json"), "w") as output:
            output.write(contents)

    @property
    def member(self):
        if not hasattr(self, "_member"):
            self._member = FakeUser("Player")
            self._member.guild_permissions = SimpleNamespace(administrator=False, manage_guild=False)
        return self._member

    def test_registered_slot_matches_ignoring_case_and_spaces(self):
        self.write_registrations(json.dumps([" Slot A "]))
        self.assertTrue(self.ns["CanRequestHint"](self.member, "slot a"))

    def test_other_slots_are_refused(self):
        self.write_registrations(json.dumps(["Slot A"]))
        self.assertFalse(self.ns["CanRequestHint"](self.member, "Slot B"))

    def test_unregistered_or_unreadable_users_are_refused(self):
        self.assertFalse(self.ns["CanRequestHint"](self.member, "Slot A"))
        for contents in ("not json", json.dumps({"Slot A": 1})):
            with self.subTest(contents=contents):
                self.write_registrations(contents)
                self.assertFalse(self.ns["CanRequestHint"](self.member, "Slot A"))

    def test_admins_can_request_any_slot(self):
        self.assertTrue(self.ns["CanRequestHint"](fake_member(manage_guild=True), "Slot B"))


class RegisterCommandInputTests(unittest.TestCase):
    def run_as(self, content):
        ns = load_on_message()
        author = fake_member()
        asyncio.run(ns["on_message"](fake_message(content, author)))
        return ns, str(author)

    def test_bare_register_gets_usage_and_registers_nothing(self):
        for content in ("$register", "$register   ", "$register\n"):
            with self.subTest(content=content):
                ns, _ = self.run_as(content)
                ns["Command_Register"].assert_not_awaited()
                ns["SendMainChannelMessage"].assert_awaited_once_with(
                    "Use `$register <slot>` with the exact slot name from Archipelago.")

    def test_register_with_slot_passes_only_the_slot(self):
        # Positive control for the bare-command check above.
        ns, author = self.run_as("$register Slot A")
        ns["Command_Register"].assert_awaited_once_with(author, "Slot A")

    def test_register_prefix_of_another_word_is_not_a_register_command(self):
        ns, _ = self.run_as("$registerSlot A")
        ns["Command_Register"].assert_not_awaited()


class IsBotAdminTests(unittest.TestCase):
    def test_accepts_administrator_or_manage_guild_only(self):
        is_admin = load(["IsBotAdmin"], {})["IsBotAdmin"]
        self.assertTrue(is_admin(fake_member(administrator=True)))
        self.assertTrue(is_admin(fake_member(manage_guild=True)))
        self.assertFalse(is_admin(fake_member()))
        self.assertFalse(is_admin(SimpleNamespace()))


def load_archinfo(debug_mode):
    config = {
        "AdvancedConfig": {"DebugMode": debug_mode},
        "DiscordConfig": {"DiscordToken": "token-SECRET", "DiscordBroadcastChannel": "1"},
        "ArchipelagoConfig": {"ArchipelagoPassword": "room-SECRET", "ArchipelagoPort": 38281, "UniqueID": "1"},
        "ItemFilterConfig": {}, "RelayConfig": {}, "DrawbridgeConfig": {}, "MetaConfig": {},
    }
    return load(["Command_ArchInfo", "RedactConfigValue"], {"CoreConfig": config})


class ArchInfoTests(unittest.TestCase):
    def test_debug_disabled_replies_without_dump(self):
        ns = load_archinfo(False)
        message = fake_message("$archinfo", fake_member())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            asyncio.run(ns["Command_ArchInfo"](message))
        message.channel.send.assert_awaited_once_with("Debug Mode is disabled.")
        self.assertEqual(out.getvalue(), "")

    def test_debug_dump_redacts_token_and_password(self):
        ns = load_archinfo(True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            asyncio.run(ns["Command_ArchInfo"](fake_message("$archinfo", fake_member(administrator=True))))
        self.assertNotIn("token-SECRET", out.getvalue())
        self.assertNotIn("room-SECRET", out.getvalue())
        self.assertIn("DiscordToken", out.getvalue())


def load_set_config():
    namespace = {
        "CoreConfig": {"ArchipelagoConfig": {
            "ArchipelagoPort": 38281, "ArchipelagoPassword": "old", "UniqueID": "1234567890",
            "ArchipelagoTrackerURL": "https://old.test", "ArchipelagoServerURL": "https://old.test/room",
        }},
        "ToggleConfig": {"CrippleTracker": True},
        "ConfigLock": threading.RLock(),
        "ToggleLock": threading.RLock(),
        "ConfirmSpecialFiles": Mock(),
        "save_config_atomic": save_config_atomic,
        "json": json,
    }
    return load(["SetConfigVariable"], namespace)


def run_set_config(ns, key, value, directory):
    config_path = Path(directory) / "config.json"
    ns["config"] = config_path
    return ns["SetConfigVariable"](key, value)


class SetConfigTests(unittest.TestCase):
    def test_non_secret_value_is_still_echoed(self):
        ns = load_set_config()
        with tempfile.TemporaryDirectory() as directory:
            result = run_set_config(ns, "ArchipelagoTrackerURL", "https://example.test/t", directory)
        self.assertEqual(result, "Key 'ArchipelagoTrackerURL' set to 'https://example.test/t'!")

    def test_password_value_is_not_echoed(self):
        ns = load_set_config()
        with tempfile.TemporaryDirectory() as directory:
            result = run_set_config(ns, "ArchipelagoPassword", "pw-SECRET", directory)
        self.assertNotIn("pw-SECRET", result)
        self.assertIn("ArchipelagoPassword", result)

    def test_unique_id_rejects_path_separators(self):
        ns = load_set_config()
        with tempfile.TemporaryDirectory() as directory:
            run_set_config(ns, "UniqueID", "../escape", directory)
            self.assertFalse((Path(directory) / "config.json").exists())
        self.assertEqual(ns["CoreConfig"]["ArchipelagoConfig"]["UniqueID"], "1234567890")

    def test_port_numeric_string_is_saved_as_integer(self):
        ns = load_set_config()
        with tempfile.TemporaryDirectory() as directory:
            result = run_set_config(ns, "ArchipelagoPort", "38282", directory)
            saved = json.loads((Path(directory) / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(result, "Key 'ArchipelagoPort' set to '38282'!")
        self.assertIs(type(ns["CoreConfig"]["ArchipelagoConfig"]["ArchipelagoPort"]), int)
        self.assertEqual(saved["ArchipelagoConfig"]["ArchipelagoPort"], 38282)

    def test_port_rejects_non_numbers_and_out_of_range_values(self):
        for bad in ("abc", "1.5", "", "0", "-1", "65536"):
            with self.subTest(port=bad):
                ns = load_set_config()
                with tempfile.TemporaryDirectory() as directory:
                    result = run_set_config(ns, "ArchipelagoPort", bad, directory)
                    self.assertEqual(result, "Invalid ArchipelagoPort. Use a whole number from 1 to 65535.")
                    self.assertFalse((Path(directory) / "config.json").exists())
                self.assertEqual(ns["CoreConfig"]["ArchipelagoConfig"]["ArchipelagoPort"], 38281)
                self.assertTrue(ns["ToggleConfig"]["CrippleTracker"])


def load_send_main():
    return load(["SendMainChannelMessage"], {
        "MainChannel": SimpleNamespace(send=AsyncMock()),
        "discord": discord,
    })


def load_alert_mention(alert_id):
    ns = load(["AlertMention"], {"CoreConfig": {"DiscordConfig": {"DiscordAlertUserID": alert_id}}})
    return ns["AlertMention"]()


class MentionTests(unittest.TestCase):
    def test_alert_mention_renders_user_and_role_ids(self):
        self.assertEqual(load_alert_mention("123456"), " <@123456>")
        self.assertEqual(load_alert_mention("&555"), " <@&555>")
        self.assertEqual(load_alert_mention(""), "")

    def test_alert_mention_rejects_non_id_values(self):
        self.assertEqual(load_alert_mention("Admins"), "")

    def test_main_channel_relay_suppresses_everyone_and_role_pings(self):
        ns = load_send_main()
        asyncio.run(ns["SendMainChannelMessage"]("@everyone server maintenance"))
        mentions = ns["MainChannel"].send.await_args.kwargs.get("allowed_mentions")
        self.assertIsNotNone(mentions)
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.roles)


def queue_line(item, location="Loc", sender="Sender", flags=0):
    return "Slot||%s||%s||%s||%d\n" % (item, sender, location, flags)


class QueueSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "reg").mkdir()
        (self.root / "item").mkdir()
        self.ns = load(
            ["Command_Register", "Command_ListRegistrations", "Command_KetchMeUp", "Command_GroupCheck", "ItemFilter", "ItemQueueFileFor",
             "CatchupStartFileFor", "LoadCatchupStart", "MoveCatchupStartAside", "RecordCatchupStart",
             "ItemQueueSafeChars", "ItemQueueReservedNames", "CatchupUserKey", "CatchupLocks", "KetchMeUpLocked"],
            {
                "discord": discord,
                "asyncio": asyncio,
                "json": json,
                "os": os,
                "unused_corrupt_path": unused_corrupt_path,
                "GetCoreDirectory": lambda folder: str(self.root / folder) + "/",
                "DebugChannel": SimpleNamespace(send=AsyncMock()),
                "MainChannel": SimpleNamespace(send=AsyncMock()),
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_command(self, name, *args):
        return asyncio.run(self.ns[name](*args))

    def write_registration(self, user, slots):
        (self.root / "reg" / (user + ".json")).write_text(json.dumps(slots), encoding="utf-8")

    def queue_path(self, slot):
        return self.root / "item" / (slot.lower() + ".csv")

    def write_queue(self, slot, text):
        self.queue_path(slot).write_text(text, encoding="utf-8")

    @staticmethod
    def sent_text(user):
        return "\n".join(str(call.args[0]) for call in user.send.await_args_list)

    def test_register_stores_plain_slot(self):
        result = self.run_command("Command_Register", "Player#1234", "Slot A")
        self.assertEqual(result, "You've been registered for Slot A!")
        stored = json.loads((self.root / "reg" / "Player#1234.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, ["Slot A"])

    def test_register_path_like_slot_queue_stays_in_item_directory(self):
        # Path-like names are stored, but their queue file is escaped into the item directory.
        self.run_command("Command_Register", "Player#1234", "Slot B")
        self.run_command("Command_Register", "Player#1234", "../secret")
        stored = json.loads((self.root / "reg" / "Player#1234.json").read_text(encoding="utf-8"))
        self.assertIn("Slot B", stored)
        self.assertIn("../secret", stored)
        self.assertEqual(self.item_file_path("../secret").name, "..%2Fsecret.csv")

    def test_ketchmeup_delivers_queued_items(self):
        self.write_registration("Player#1234", ["Slot A"])
        self.write_queue("Slot A", queue_line("Map", flags=1))
        user = FakeUser()
        self.run_command("Command_KetchMeUp", user, "$ketchmeup")
        self.assertIn("Map", self.sent_text(user))

    def test_ketchmeup_repeat_sends_only_items_since_last_catchup(self):
        self.write_registration("Alice", ["Slot A"])
        self.write_queue("Slot A", queue_line("Map"))
        self.run_command("Command_KetchMeUp", FakeUser("Alice"), "$ketchmeup")
        with self.queue_path("Slot A").open("a", encoding="utf-8") as queue_file:
            queue_file.write(queue_line("Second"))
        again = FakeUser("Alice")
        self.run_command("Command_KetchMeUp", again, "$ketchmeup")
        self.assertIn("Second", self.sent_text(again))
        self.assertNotIn("Map", self.sent_text(again))

    def test_new_registrant_starts_at_current_queue_end(self):
        self.write_queue("Slot A", queue_line("Old"))
        self.run_command("Command_Register", "Carol", "Slot A")
        with self.queue_path("Slot A").open("a", encoding="utf-8") as queue_file:
            queue_file.write(queue_line("New"))
        carol = FakeUser("Carol")
        self.run_command("Command_KetchMeUp", carol, "$ketchmeup")
        self.assertIn("New", self.sent_text(carol))
        self.assertNotIn("Old", self.sent_text(carol))

    def test_ketchmeup_keeps_queue_for_other_registered_players(self):
        self.write_registration("Alice", ["Slot A"])
        self.write_registration("Bob", ["Slot A"])
        self.write_queue("Slot A", queue_line("Map"))
        self.run_command("Command_KetchMeUp", FakeUser("Alice"), "$ketchmeup")
        bob = FakeUser("Bob")
        self.run_command("Command_KetchMeUp", bob, "$ketchmeup")
        self.assertIn("Map", self.sent_text(bob))

    def test_ketchmeup_malformed_line_does_not_drop_queue(self):
        self.write_registration("Player#1234", ["Slot A"])
        self.write_queue("Slot A", "not-a-queue-line\n" + queue_line("Map"))
        user = FakeUser()
        self.run_command("Command_KetchMeUp", user, "$ketchmeup")
        self.assertTrue(self.queue_path("Slot A").exists())
        self.assertIn("Map", self.sent_text(user))

    def test_ketchmeup_skips_traversal_registration_without_deleting(self):
        # Positive control: a normal registration in the same run still delivers.
        self.write_registration("Player#1234", ["Slot A", "../secret"])
        self.write_queue("Slot A", queue_line("Map"))
        (self.root / "secret.csv").write_text(queue_line("SECRET-ITEM"), encoding="utf-8")
        user = FakeUser()
        self.run_command("Command_KetchMeUp", user, "$ketchmeup")
        self.assertIn("Map", self.sent_text(user))
        self.assertNotIn("SECRET-ITEM", self.sent_text(user))
        self.assertTrue((self.root / "secret.csv").exists())

    def test_register_stores_trimmed_slot(self):
        result = self.run_command("Command_Register", "Player#1234", "  Slot A  ")
        self.assertEqual(result, "You've been registered for Slot A!")
        stored = json.loads((self.root / "reg" / "Player#1234.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, ["Slot A"])

    def test_catchup_start_mark_matches_registration_with_spaces(self):
        self.write_queue("Slot A", queue_line("Old"))
        self.run_command("Command_Register", "Carol", " Slot A ")
        with self.queue_path("Slot A").open("a", encoding="utf-8") as queue_file:
            queue_file.write(queue_line("New"))
        carol = FakeUser("Carol")
        self.run_command("Command_KetchMeUp", carol, "$ketchmeup")
        self.assertIn("New", self.sent_text(carol))
        self.assertNotIn("Old", self.sent_text(carol))

    def test_cursor_with_stray_spaces_in_slot_key_still_resumes(self):
        self.write_registration("Alice", ["Slot A"])
        self.write_queue("Slot A", queue_line("Map") + queue_line("Second"))
        cursor_dir = self.root / "reg" / "catchup"
        cursor_dir.mkdir(parents=True)
        (cursor_dir / "Alice.json").write_text(json.dumps({" Slot A ": 1}), encoding="utf-8")
        alice = FakeUser("Alice")
        self.run_command("Command_KetchMeUp", alice, "$ketchmeup")
        self.assertIn("Second", self.sent_text(alice))
        self.assertNotIn("Map", self.sent_text(alice))

    def test_ketchmeup_cursor_is_keyed_by_user_id_and_repeat_sends_only_new(self):
        self.write_registration("Player#1234", ["Slot A"])
        self.write_queue("Slot A", queue_line("Map"))
        self.run_command("Command_KetchMeUp", FakeUser("Player#1234", user_id=1234567890), "$ketchmeup")
        cursor_dir = self.root / "reg" / "catchup"
        self.assertTrue((cursor_dir / "1234567890.json").exists())
        self.assertFalse((cursor_dir / "Player#1234.json").exists())
        with self.queue_path("Slot A").open("a", encoding="utf-8") as queue_file:
            queue_file.write(queue_line("Second"))
        again = FakeUser("Player#1234", user_id=1234567890)
        self.run_command("Command_KetchMeUp", again, "$ketchmeup")
        self.assertIn("Second", self.sent_text(again))
        self.assertNotIn("Map", self.sent_text(again))

    def test_unreadable_start_marks_are_moved_aside_not_overwritten(self):
        start_dir = self.root / "reg" / "catchup-start"
        start_dir.mkdir(parents=True)
        (start_dir / "Carol.json").write_text('{"slot b": 5', encoding="utf-8")
        self.run_command("Command_Register", "Carol", "Slot A")
        corrupt = start_dir / "Carol.json.corrupt"
        self.assertEqual(corrupt.read_text(encoding="utf-8"), '{"slot b": 5')
        marks = json.loads((start_dir / "Carol.json").read_text(encoding="utf-8"))
        self.assertEqual(marks, {"slot a": 0})

    def test_unreadable_start_marks_that_cannot_move_are_left_in_place(self):
        start_dir = self.root / "reg" / "catchup-start"
        start_dir.mkdir(parents=True)
        (start_dir / "Carol.json").write_text('{"slot b": 5', encoding="utf-8")
        with patch("os.replace", side_effect=OSError("file in use")):
            self.assertIn("registered", self.run_command("Command_Register", "Carol", "Slot A"))
        self.assertEqual((start_dir / "Carol.json").read_text(encoding="utf-8"), '{"slot b": 5')

    def test_unreadable_start_marks_do_not_overwrite_an_earlier_corrupt_copy(self):
        start_dir = self.root / "reg" / "catchup-start"
        start_dir.mkdir(parents=True)
        (start_dir / "Carol.json").write_text('{"slot b": 5', encoding="utf-8")
        (start_dir / "Carol.json.corrupt").write_text("earlier copy", encoding="utf-8")
        self.run_command("Command_Register", "Carol", "Slot A")
        self.assertEqual((start_dir / "Carol.json.corrupt").read_text(encoding="utf-8"), "earlier copy")
        self.assertEqual((start_dir / "Carol.json.corrupt.1").read_text(encoding="utf-8"), '{"slot b": 5')
        marks = json.loads((start_dir / "Carol.json").read_text(encoding="utf-8"))
        self.assertEqual(marks, {"slot a": 0})

    def item_file_path(self, slot):
        path = self.ns["ItemQueueFileFor"](slot)
        self.assertIsNotNone(path)
        self.assertEqual(Path(path).parent, self.root / "item")
        return Path(path)

    def test_ordinary_slot_names_keep_their_historical_file_name(self):
        cases = {"Slot A": "slot a.csv", "Slot.A-1_x": "slot.a-1_x.csv", ".hidden": ".hidden.csv", "Café": "café.csv"}
        for slot, name in cases.items():
            with self.subTest(slot=slot):
                self.assertEqual(self.item_file_path(slot).name, name)

    def test_reserved_device_names_get_safe_file_names(self):
        cases = {
            "Con": "%63on.csv", "nul.txt": "%6Eul.txt.csv", "AUX": "%61ux.csv",
            "COM1": "%63om1.csv", "lpt9.log": "%6Cpt9.log.csv", "con.": "%63on..csv",
            "COM¹": "com%C2%B9.csv", "_con": "_con.csv",
        }
        for slot, name in cases.items():
            with self.subTest(slot=slot):
                path = self.item_file_path(slot)
                self.assertEqual(path.name, name)
                path.write_text(queue_line("Map"), encoding="utf-8")
                self.assertTrue(path.is_file())

    def test_separators_colons_and_control_characters_are_percent_encoded(self):
        cases = {
            "A:B": "a%3Ab.csv", "a/b": "a%2Fb.csv", "a\\b": "a%5Cb.csv",
            'a"b*?<>|': "a%22b%2A%3F%3C%3E%7C.csv", "tab\there": "tab%09here.csv",
            "..": "%2E%2E.csv", ".": "%2E.csv", "½": "%C2%BD.csv",
        }
        for slot, name in cases.items():
            with self.subTest(slot=slot):
                self.assertEqual(self.item_file_path(slot).name, name)

    def test_only_empty_slot_names_are_refused(self):
        for slot in (None, "", "   "):
            with self.subTest(slot=slot):
                self.assertIsNone(self.ns["ItemQueueFileFor"](slot))
        self.assertIsNotNone(self.ns["ItemQueueFileFor"]("Con"))

    def test_reserved_and_special_slots_register_and_catch_up(self):
        for number, slot in enumerate(("Con", "A:B", "a/b")):
            with self.subTest(slot=slot):
                name = "Player%d" % number
                self.assertIn("registered for " + slot, self.run_command("Command_Register", name, slot))
                self.item_file_path(slot).write_text(queue_line("Map"), encoding="utf-8")
                user = FakeUser(name)
                self.run_command("Command_KetchMeUp", user, "$ketchmeup")
                self.assertIn("Map", self.sent_text(user))

    def test_concurrent_ketchmeup_delivers_each_item_once(self):
        self.write_registration("Alice", ["Slot A"])
        self.write_queue("Slot A", queue_line("UniqueItemXYZ"))
        alice = FakeUser("Alice", user_id=111)

        async def slow_send(*args, **kwargs):
            # Yield between reading the cursor and saving it, so an unlocked second call can interleave.
            await asyncio.sleep(0)

        alice.send = AsyncMock(side_effect=slow_send)

        async def both_calls():
            await asyncio.gather(
                self.ns["Command_KetchMeUp"](alice, "$ketchmeup"),
                self.ns["Command_KetchMeUp"](alice, "$ketchmeup"),
            )

        asyncio.run(both_calls())
        self.assertEqual(self.sent_text(alice).count("UniqueItemXYZ"), 1)


    def test_groupcheck_cannot_read_outside_item_directory(self):
        # Positive control: a normal slot lookup in the same run still delivers.
        self.write_queue("slot a", queue_line("Map"))
        (self.root / "secret.csv").write_text(queue_line("SECRET-ITEM"), encoding="utf-8")
        user = FakeUser()
        self.run_command("Command_GroupCheck", user, "slot a")
        self.assertIn("Map", self.sent_text(user))
        traversal = FakeUser()
        self.run_command("Command_GroupCheck", traversal, "../secret")
        self.assertNotIn("SECRET-ITEM", self.sent_text(traversal))

    def test_dm_failure_reports_failure(self):
        self.write_registration("Player#1234", ["Slot A"])
        self.write_queue("Slot A", queue_line("Map"))
        blocked = FakeUser()
        blocked.send = AsyncMock(side_effect=RuntimeError("DMs blocked"))
        self.assertIs(self.run_command("Command_ListRegistrations", blocked), False)
        self.assertIs(self.run_command("Command_GroupCheck", blocked, "slot a"), False)
        self.assertIs(self.run_command("Command_KetchMeUp", blocked, "$ketchmeup"), False)

    def test_forbidden_dm_reports_failure(self):
        self.write_registration("Player#1234", ["Slot A"])
        self.write_queue("Slot A", queue_line("Map"))
        denied = FakeUser()
        response = SimpleNamespace(status=403, reason="Forbidden")
        denied.send = AsyncMock(side_effect=discord.Forbidden(response, "blocked"))
        self.assertIs(self.run_command("Command_KetchMeUp", denied, "$ketchmeup"), False)


def load_hint_tree(can_hint=True):
    class FakeTree:
        def __init__(self):
            self.commands = {}

        def command(self, name, description):
            def decorator(callback):
                self.commands[name] = callback
                return callback
            return decorator

    async def noop(*args):
        return None

    callbacks = {name: noop for name in (
        "help_text", "register", "listreg", "clearreg", "ketchmeup", "groupcheck",
        "hints", "checkcount", "checkgraph", "deathcount",
    )}
    hint_queue = queue.Queue()
    callbacks["hint_queue"] = lambda: hint_queue
    callbacks["can_hint"] = lambda user, slot: can_hint
    tree = FakeTree()
    register_player_commands(tree, callbacks, lambda: 42)
    return tree, hint_queue


class FakeHintResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, content, ephemeral=False, **kwargs):
        self.messages.append((content, ephemeral))


class SlashHintTests(unittest.TestCase):
    def test_hint_request_is_queued(self):
        tree, hint_queue = load_hint_tree()
        interaction = SimpleNamespace(channel_id=42, user=FakeUser(), response=FakeHintResponse())
        asyncio.run(tree.commands["hint"](interaction, " Slot A ", " Item B "))
        queued = hint_queue.get_nowait()
        self.assertEqual((queued["slot"], queued["item"]), ("Slot A", "Item B"))

    def test_hint_request_confirmation_is_public_and_names_requester(self):
        tree, _ = load_hint_tree()
        interaction = SimpleNamespace(channel_id=42, user=FakeUser(), response=FakeHintResponse())
        asyncio.run(tree.commands["hint"](interaction, "Slot A", "Item B"))
        content, ephemeral = interaction.response.messages[0]
        self.assertFalse(ephemeral)
        self.assertIn("Player#1234", content)
        self.assertIn("Slot A", content)
        self.assertIn("Item B", content)

    def test_hint_request_rejects_slot_or_item_over_100_characters(self):
        tree, hint_queue = load_hint_tree()
        for slot, item in (("S" * 101, "Item B"), ("Slot A", "I" * 101)):
            with self.subTest(slot=len(slot), item=len(item)):
                interaction = SimpleNamespace(channel_id=42, user=FakeUser(), response=FakeHintResponse())
                asyncio.run(tree.commands["hint"](interaction, slot, item))
                content, ephemeral = interaction.response.messages[0]
                self.assertTrue(ephemeral)
                self.assertIn("100 characters", content)
        self.assertTrue(hint_queue.empty())

    def test_hint_request_for_an_unregistered_slot_is_refused_privately(self):
        tree, hint_queue = load_hint_tree(can_hint=False)
        interaction = SimpleNamespace(channel_id=42, user=FakeUser(), response=FakeHintResponse())
        asyncio.run(tree.commands["hint"](interaction, "Slot A", "Item B"))
        self.assertEqual(interaction.response.messages, [(HINT_NOT_REGISTERED_MESSAGE, True)])
        self.assertTrue(hint_queue.empty())

    def test_hint_request_accepts_exactly_100_characters(self):
        tree, hint_queue = load_hint_tree()
        interaction = SimpleNamespace(channel_id=42, user=FakeUser(), response=FakeHintResponse())
        asyncio.run(tree.commands["hint"](interaction, "S" * 100, "I" * 100))
        queued = hint_queue.get_nowait()
        self.assertEqual((queued["slot"], queued["item"]), ("S" * 100, "I" * 100))


if __name__ == "__main__":
    unittest.main()
