import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

ROOT = Path(__file__).resolve().parent
NAMES = {
    "DEFAULT_SLEEP_WAKE_EDIT_WINDOW_MINUTES",
    "RoomSleepNoticeMessage",
    "RoomSleepNoticeSentAt",
    "GetSleepWakeEditWindowSeconds",
    "SendRoomConnectionNotice",
}


def top_level_name(node):
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return getattr(node, "name", None)


def load(notification_config=None):
    tree = ast.parse((ROOT / "bridgeipelago.py").read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if top_level_name(node) in NAMES]
    clock = SimpleNamespace(now=0.0)
    sleep_message = SimpleNamespace(edit=AsyncMock())
    config = {} if notification_config is None else {"NotificationConfig": notification_config}
    namespace = {
        "CoreConfig": config,
        "MainChannel": SimpleNamespace(send=AsyncMock(return_value=sleep_message)),
        "SendMainChannelMessage": AsyncMock(),
        "WriteToErrorLog": Mock(),
        "discord": discord,
        "time": SimpleNamespace(monotonic=lambda: clock.now),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bridgeipelago.py", "exec"), namespace)
    namespace["clock"] = clock
    namespace["sleep_message"] = sleep_message
    return namespace


def notice(flag, text):
    return {"type": "CORE", "data": {"text": text}, "flag": flag}


class RoomNoticeEditTests(unittest.TestCase):
    def send(self, ns, flag, text, at):
        ns["clock"].now = at
        asyncio.run(ns["SendRoomConnectionNotice"](notice(flag, text)))

    def test_wake_inside_the_window_edits_the_sleep_notice(self):
        ns = load()
        self.send(ns, "ROOM_SLEEP", "asleep", 100)
        ns["MainChannel"].send.assert_awaited_once()
        self.send(ns, "ROOM_WAKE", "awake", 100 + 14 * 60)
        ns["sleep_message"].edit.assert_awaited_once()
        self.assertEqual(ns["sleep_message"].edit.await_args.kwargs["content"], "awake")
        ns["SendMainChannelMessage"].assert_not_awaited()

    def test_wake_after_the_window_posts_a_new_message(self):
        ns = load()
        self.send(ns, "ROOM_SLEEP", "asleep", 100)
        self.send(ns, "ROOM_WAKE", "awake", 100 + 15 * 60)
        ns["sleep_message"].edit.assert_not_awaited()
        ns["SendMainChannelMessage"].assert_awaited_once_with("awake")

    def test_configured_window_is_used(self):
        ns = load({"SleepWakeEditWindowMinutes": 2})
        self.send(ns, "ROOM_SLEEP", "asleep", 0)
        self.send(ns, "ROOM_WAKE", "awake", 3 * 60)
        ns["sleep_message"].edit.assert_not_awaited()
        ns["SendMainChannelMessage"].assert_awaited_once_with("awake")

    def test_zero_window_always_posts(self):
        ns = load({"SleepWakeEditWindowMinutes": 0})
        self.send(ns, "ROOM_SLEEP", "asleep", 0)
        self.send(ns, "ROOM_WAKE", "awake", 0)
        ns["sleep_message"].edit.assert_not_awaited()
        ns["SendMainChannelMessage"].assert_awaited_once_with("awake")

    def test_failed_edit_falls_back_to_a_new_message(self):
        ns = load()
        response = SimpleNamespace(status=404, reason="Not Found")
        ns["sleep_message"].edit.side_effect = discord.NotFound(response, "deleted")
        self.send(ns, "ROOM_SLEEP", "asleep", 0)
        self.send(ns, "ROOM_WAKE", "awake", 60)
        ns["SendMainChannelMessage"].assert_awaited_once_with("awake")
        ns["WriteToErrorLog"].assert_called_once()

    def test_wake_without_a_sleep_notice_posts(self):
        ns = load()
        self.send(ns, "ROOM_WAKE", "awake", 0)
        ns["SendMainChannelMessage"].assert_awaited_once_with("awake")

    def test_a_sleep_notice_is_only_edited_once(self):
        ns = load()
        self.send(ns, "ROOM_SLEEP", "asleep", 0)
        self.send(ns, "ROOM_WAKE", "awake", 60)
        self.send(ns, "ROOM_WAKE", "awake again", 120)
        ns["sleep_message"].edit.assert_awaited_once()
        ns["SendMainChannelMessage"].assert_awaited_once_with("awake again")

    def test_notices_never_ping(self):
        ns = load()
        self.send(ns, "ROOM_SLEEP", "asleep", 0)
        self.send(ns, "ROOM_WAKE", "awake", 60)
        for call in (ns["MainChannel"].send.await_args, ns["sleep_message"].edit.await_args):
            mentions = call.kwargs["allowed_mentions"]
            self.assertFalse(mentions.everyone or mentions.users or mentions.roles)

    def test_window_setting_falls_back_to_15_minutes_when_invalid(self):
        for value in ("abc", -1, True, None, float("nan"), float("inf"), [5]):
            with self.subTest(value=value):
                self.assertEqual(load({"SleepWakeEditWindowMinutes": value})["GetSleepWakeEditWindowSeconds"](), 900)
        self.assertEqual(load()["GetSleepWakeEditWindowSeconds"](), 900)
        self.assertEqual(load({"SleepWakeEditWindowMinutes": "5"})["GetSleepWakeEditWindowSeconds"](), 300)
        self.assertEqual(load({"SleepWakeEditWindowMinutes": 0})["GetSleepWakeEditWindowSeconds"](), 0)


if __name__ == "__main__":
    unittest.main()
