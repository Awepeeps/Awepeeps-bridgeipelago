import asyncio
import inspect
import queue
import unittest
import discord
from discord import app_commands

from modules.player_commands import register_player_commands


class FakeTree:
    def __init__(self):
        self.commands = {}

    def command(self, name, description):
        def decorator(callback):
            self.commands[name] = callback
            return callback
        return decorator


class FakeResponse:
    def __init__(self):
        self.messages = []
        self.deferred = []
        self.responded = False

    async def send_message(self, content, ephemeral=False):
        if self.responded:
            raise RuntimeError("interaction already acknowledged")
        self.responded = True
        self.messages.append((content, ephemeral))

    async def defer(self, ephemeral=False, thinking=False):
        if self.responded:
            raise RuntimeError("interaction already acknowledged")
        self.responded = True
        self.deferred.append((ephemeral, thinking))


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))


class FakeUser:
    def __str__(self):
        return "Player#1234"


class FakeInteraction:
    def __init__(self, channel_id=42):
        self.channel_id = channel_id
        self.user = FakeUser()
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class PlayerCommandTests(unittest.TestCase):
    def setUp(self):
        self.tree = FakeTree()
        self.queue = queue.Queue()
        self.calls = []
        async def callback(name, *args):
            self.calls.append((name, args))
        self.callbacks = {
            "help_text": "All eleven player commands are listed here.",
            "register": lambda *args: callback("register", *args),
            "listreg": lambda *args: callback("listreg", *args),
            "clearreg": lambda *args: callback("clearreg", *args),
            "ketchmeup": lambda *args: callback("ketchmeup", *args),
            "groupcheck": lambda *args: callback("groupcheck", *args),
            "hints": lambda *args: callback("hints", *args),
            "hint_queue": lambda: self.queue,
            "checkcount": lambda *args: callback("checkcount", *args),
            "checkgraph": lambda *args: callback("checkgraph", *args),
            "deathcount": lambda *args: callback("deathcount", *args),
        }
        self.registered = register_player_commands(self.tree, self.callbacks, lambda: 42)

    def call(self, name, *args, **kwargs):
        interaction = kwargs.pop("interaction", FakeInteraction())
        asyncio.run(self.tree.commands[name](interaction, *args, **kwargs))
        return interaction

    def test_registers_all_eleven_player_commands(self):
        expected = {
            "help", "register", "listreg", "clearreg", "ketchmeup", "groupcheck",
            "hints", "hint", "checkcount", "checkgraph", "deathcount",
        }
        self.assertEqual(set(self.registered), expected)
        self.assertEqual(set(self.tree.commands), expected)

    def test_parameter_names_and_filter_choices(self):
        self.assertEqual(list(inspect.signature(self.tree.commands["register"]).parameters), ["interaction", "slot"])
        self.assertEqual(list(inspect.signature(self.tree.commands["groupcheck"]).parameters), ["interaction", "slot"])
        self.assertEqual(list(inspect.signature(self.tree.commands["hint"]).parameters), ["interaction", "slot", "item"])
        filter_parameter = inspect.signature(self.tree.commands["ketchmeup"]).parameters["filter"]
        self.assertEqual(filter_parameter.annotation, int)
        self.assertEqual(filter_parameter.default, 0)
        client = discord.Client(intents=discord.Intents.none())
        real_tree = app_commands.CommandTree(client)
        async def noop(*args):
            return None
        callbacks = {"help_text": "help", "hint_queue": lambda: self.queue}
        callbacks.update({
            name: noop for name in (
                "register", "listreg", "clearreg", "ketchmeup", "groupcheck",
                "hints", "checkcount", "checkgraph", "deathcount",
            )
        })
        register_player_commands(real_tree, callbacks, lambda: 42)
        choices = real_tree.get_command("ketchmeup").parameters[0].choices
        self.assertEqual(
            [(choice.name, choice.value) for choice in choices],
            [("Everything", 0), ("Useful and progression", 1), ("Progression only", 2)],
        )

    def test_help_is_private_and_channel_gate_blocks_other_channels(self):
        ok = self.call("help")
        self.assertTrue(ok.response.messages[0][1])
        self.assertIn("All eleven", ok.response.messages[0][0])
        blocked = self.call("help", interaction=FakeInteraction(channel_id=99))
        self.assertTrue(blocked.response.messages[0][1])
        self.assertFalse(self.calls)

    def test_registration_uses_existing_callback_and_private_confirmation(self):
        interaction = self.call("register", "The Hero")
        self.assertEqual(self.calls, [("register", ("Player#1234", "The Hero"))])
        self.assertEqual(interaction.response.deferred, [(True, True)])
        self.assertTrue(interaction.followup.messages[0][1])

    def test_dm_commands_reuse_the_existing_callbacks(self):
        listreg = self.call("listreg")
        self.assertEqual(self.calls[-1], ("listreg", (listreg.user,)))
        self.assertTrue(listreg.followup.messages[0][1])
        clearreg = self.call("clearreg")
        self.assertEqual(self.calls[-1], ("clearreg", ("Player#1234",)))
        self.assertTrue(clearreg.followup.messages[0][1])
        hints = self.call("hints")
        self.assertEqual(self.calls[-1], ("hints", (hints.user,)))
        self.assertTrue(hints.followup.messages[0][1])

    def test_ketchmeup_filter_is_passed_as_string_after_defer(self):
        interaction = self.call("ketchmeup", 2)
        self.assertEqual(self.calls, [("ketchmeup", (interaction.user, "2"))])
        self.assertEqual(interaction.response.deferred, [(True, True)])
        self.assertTrue(interaction.followup.messages[0][1])

    def test_hint_is_queued_and_groupcheck_reuses_callback(self):
        self.call("hint", " Slot A ", " Item B ")
        self.assertEqual(self.queue.get_nowait(), ["Slot A", "Item B"])
        empty = self.call("hint", " ", "Item B")
        self.assertEqual(empty.response.messages[0][0], "Both slot and item are required.")
        self.assertTrue(self.queue.empty())
        interaction = self.call("groupcheck", "Slot A")
        self.assertEqual(self.calls, [("groupcheck", (interaction.user, "Slot A"))])

    def test_chart_callbacks_are_public_helpers_with_private_status(self):
        for name in ("checkcount", "checkgraph", "deathcount"):
            interaction = self.call(name)
            self.assertEqual(self.calls[-1], (name, ()))
            self.assertTrue(interaction.response.deferred[0][0])
            self.assertTrue(interaction.followup.messages[0][1])

    def test_deferred_callback_errors_get_private_followup(self):
        async def fail(*args):
            raise RuntimeError("private details")
        self.callbacks["listreg"] = fail
        interaction = self.call("listreg")
        self.assertEqual(
            interaction.followup.messages,
            [("The command could not be completed. Please try again later.", True)],
        )


if __name__ == "__main__":
    unittest.main()
