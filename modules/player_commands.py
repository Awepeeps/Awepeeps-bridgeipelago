"""Player-facing Discord slash commands, independent from bot startup/config."""

from discord import app_commands
import discord

HINT_FIELD_MAX_LENGTH = 100
HINT_NOT_REGISTERED_MESSAGE = (
    "You can only request hints for a slot you're registered for. "
    "Use `$register <slot>` first."
)


def register_player_commands(tree, callbacks, get_broadcast_channel_id):
    """Register commands, using existing callbacks and a live broadcast channel ID."""
    names = []

    async def check_channel(interaction):
        if callbacks.get("slash_enabled", lambda: True)() is not True:
            await interaction.response.send_message("Slash commands are disabled.", ephemeral=True)
            return False
        if interaction.channel_id != int(get_broadcast_channel_id()):
            await interaction.response.send_message(
                "Use this command in the configured bot channel.", ephemeral=True
            )
            return False
        return True

    def command(name, description):
        def decorate(function):
            tree.command(name=name, description=description)(function)
            names.append(name)
            return function
        return decorate

    async def run_deferred(interaction, work, message):
        if not await check_channel(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await work()
            if result is False:
                await interaction.followup.send(
                    "The command could not be completed. Please check your DM settings or try again later.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                result if message is None else message, ephemeral=True
            )
        except Exception:
            await interaction.followup.send(
                "The command could not be completed. Please try again later.",
                ephemeral=True,
            )

    @command("help", "Show player commands")
    async def help_command(interaction):
        if await check_channel(interaction):
            await interaction.response.send_message(callbacks["help_text"], ephemeral=True)

    @command("register", "Register your Discord account for an AP slot")
    async def register_command(interaction, slot: str):
        if not await check_channel(interaction):
            return
        if not slot.strip():
            await interaction.response.send_message(
                "Use `/register` with the exact slot name from Archipelago.", ephemeral=True
            )
            return
        async def register_and_bind():
            result = await callbacks["register"](str(interaction.user), slot)
            if isinstance(result, str) and (result.startswith("You've been registered") or result.startswith("You're already registered")):
                callbacks.get("bind_registration", lambda _user: None)(interaction.user)
            return result
        await run_deferred(interaction, register_and_bind, None)

    @command("listreg", "DM you the slots you are registered for")
    async def listreg_command(interaction):
        await run_deferred(
            interaction,
            lambda: callbacks["listreg"](interaction.user),
            "Check your DMs for your registrations.",
        )

    @command("clearreg", "Remove all of your slot registrations")
    async def clearreg_command(interaction):
        async def clear_and_forget():
            result = await callbacks["clearreg"](str(interaction.user))
            forgotten = callbacks.get("clear_preferences", lambda _user: True)(interaction.user)
            if forgotten is False:
                return "Registration status: {} Notification preferences could not be cleared; try again.".format(result)
            if isinstance(result, str) and result.startswith("You're not registered"):
                return "You're not registered for slots; item preferences were cleared."
            return result
        await run_deferred(
            interaction,
            clear_and_forget,
            None,
        )

    @command("ketchmeup", "DM you missed items for your registered slots")
    @app_commands.choices(filter=[
        app_commands.Choice(name="Everything", value=0),
        app_commands.Choice(name="Useful and progression", value=1),
        app_commands.Choice(name="Progression only", value=2),
    ])
    async def ketchmeup_command(interaction, filter: int = 0):
        await run_deferred(
            interaction,
            lambda: callbacks["ketchmeup"](interaction.user, str(filter)),
            "Check your DMs for your missed items.",
        )

    @command("groupcheck", "DM queued items for a slot")
    async def groupcheck_command(interaction, slot: str):
        await run_deferred(
            interaction,
            lambda: callbacks["groupcheck"](interaction.user, slot),
            "Sending the queued items by DM.",
        )

    @command("hints", "DM hints assigned to your registered slots")
    async def hints_command(interaction):
        await run_deferred(
            interaction,
            lambda: callbacks["hints"](interaction.user),
            "Check your DMs for your hints.",
        )

    @command("hint", "Request a hint for a slot and item")
    async def hint_command(interaction, slot: str, item: str):
        if not await check_channel(interaction):
            return
        slot = slot.strip()
        item = item.strip()
        if not slot or not item:
            await interaction.response.send_message(
                "Both slot and item are required.", ephemeral=True
            )
            return
        if len(slot) > HINT_FIELD_MAX_LENGTH or len(item) > HINT_FIELD_MAX_LENGTH:
            await interaction.response.send_message(
                "Slot and item must each be {} characters or fewer.".format(HINT_FIELD_MAX_LENGTH),
                ephemeral=True,
            )
            return
        if not callbacks["can_hint"](interaction.user, slot):
            await interaction.response.send_message(HINT_NOT_REGISTERED_MESSAGE, ephemeral=True)
            return
        registered_at_request = bool(callbacks.get(
            "is_registered_for_slot", lambda _user, _slot: False
        )(interaction.user, slot))
        if registered_at_request:
            callbacks.get("bind_registration", lambda _user: None)(interaction.user)
        callbacks["hint_queue"]().put({
            "slot": slot, "item": item,
            "request_context": {
                "requester_id": getattr(interaction.user, "id", None),
                "requester_name": str(interaction.user),
                "registered_at_request": registered_at_request,
            },
        })
        await interaction.response.send_message(
            str(interaction.user) + " requested a hint for " + slot + ": " + item,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @command("notify", "Choose which item notifications to receive")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="All registered slots", value="all"),
        app_commands.Choice(name="Watched items", value="watched"),
    ])
    async def notify_command(interaction, mode: str):
        if not await check_channel(interaction):
            return
        if mode != "off" and not callbacks["is_registered_user"](interaction.user):
            await interaction.response.send_message("Register for a slot before changing notification settings.", ephemeral=True)
            return
        if mode != "off" and callbacks["global_notifications_enabled"]() is not True:
            await interaction.response.send_message("Item notification pings are disabled by the bot administrator.", ephemeral=True)
            return
        result = callbacks["set_notify_mode"](interaction.user, mode)
        await interaction.response.send_message(result, ephemeral=True)

    async def watch_command(interaction, slot: str, item: str, remove=False):
        if not await check_channel(interaction):
            return
        if not callbacks["is_registered_for_slot"](interaction.user, slot):
            await interaction.response.send_message(HINT_NOT_REGISTERED_MESSAGE, ephemeral=True)
            return
        if not callbacks["is_valid_item"](slot, item):
            await interaction.response.send_message("Use the exact item name for that slot.", ephemeral=True)
            return
        callback = callbacks["remove_watch"] if remove else callbacks["add_watch"]
        await interaction.response.send_message(callback(interaction.user, slot, item), ephemeral=True)

    @command("watch", "Watch for an item in one of your registered slots")
    async def watch_item_command(interaction, slot: str, item: str):
        await watch_command(interaction, slot.strip(), item.strip())

    @command("unwatch", "Stop watching for an item")
    async def unwatch_item_command(interaction, slot: str, item: str):
        await watch_command(interaction, slot.strip(), item.strip(), remove=True)

    @command("watches", "List your watched items")
    async def watches_command(interaction):
        if await check_channel(interaction):
            await interaction.response.send_message(callbacks["list_watches"](interaction.user), ephemeral=True)

    @command("checkcount", "Post the check count")
    async def checkcount_command(interaction):
        await run_deferred(
            interaction, callbacks["checkcount"], "Check count posted."
        )

    @command("checkgraph", "Post the progress graph")
    async def checkgraph_command(interaction):
        await run_deferred(
            interaction, callbacks["checkgraph"], "Progress graph posted."
        )

    @command("deathcount", "Post the DeathLink tally")
    async def deathcount_command(interaction):
        await run_deferred(
            interaction, callbacks["deathcount"], "DeathLink tally posted."
        )

    return tuple(names)
