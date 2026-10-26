# Setup
Setting the bot up has been as streamlined as possible.  
Following this guide should get you fairly close to complete.  
If you have any questions, feel free to ask in the [Discord](https://discord.gg/5v9P3qNPXp)

## Before you continue:
This bot requires `python` and `pip` at a minimum.  
General knowledge of python is encouraged but <u>not strictly necessary</u>.  
I've also written this guide with Linux in mind, but that can be easily accomplished with WSL on windows, or a VM to run the bot.  

**Bridgeipelago Windows OS support has been removed in v3.0. Some day I'll add it back.**  
**You can use WSL to run Bridgeipelago VERY easily. [WSL Setup](https://learn.microsoft.com/en-us/windows/wsl/install)**  
~~There is some minor help in Step 5 with running python commands in windows.~~

To be honest, you can figure out most errors by just googling.


## Step 1) Preparing the Archipelago game
When generating the Archipelago game, make sure the <ins>**bridgeipelago.yaml**</ins> is included in the game to ensure the bot has a slot to listen in on.  
This slot has no effect on the AP itself.  
If you'd like to add-in this bot to an existing game (or not have a dedicated slot for the bot) change ArchipelagoBotSlot in the `config.json` to a valid slot-name.

## Step 2) Discord Bot Setup (You only need to do this once)
You'll need a discord bot API token for the bot.

Log into the Discord Developer Portal: https://discord.com/developers/applications  
And create a new Application/bot

In the **Installation** tab:  
- Installation Contexts:
  - Check `Guild Install`  
- Default Install Settings:
  - Scopes: `application.commands` and `bot`  
  - Permissions: `Send Messages`

In the **Bot** tab:
- Username: Something fun!
- Copy your token down. You'll need it in a bit!
- Enable `Public Bot`
- Enable `Message Content Intent`

Back in the **Installation** tab:
- Under `Install Link` make sure `Discord Provided Link` is selected
- Copy the OAuth link into your browser and add the bot to the discord of your choice.

You're done with the bot portal for now, but keep that token handy!

## Step 3) Discord Channel Setup (You only need to do this once)
- In Discord, enable `Developer Mode` for your client  
- Create/have two channels for the bot.  
  - One will be where the bot posts all the AP information, and where commands are ran (for your users) 
  - The second will be for debug reasons (for you, I'd keep this private)
  - **Make sure the bot has access to both channels and can send messages in them.**
- Right-Click the channels and `Copy Channel ID`, and hold them for now!
- Next, right-click your name and `Copy User ID`, copy it down.

## Step 4) Bridgeipelago Setup
- Core
  1. Download Bridgeipelago  
     - Stable:
       - [Latest stable release](https://github.com/Quasky/bridgeipelago/releases/latest)
     - Unstable (hopefully not):
       - [All releases (Stable and Pre-Release)](https://github.com/Quasky/bridgeipelago/releases)
       - Or clone the main repo for a crazy experience `https://github.com/Quasky/bridgeipelago.git`
  1. Copy `config.json.template` to `config.json`
- Discord Config
    1. Fill out the `DiscordToken` with your discord app/bot's token
  1. Fill out `DiscordBroadcastChannel` with the channel ID where the bot posts AP info and accepts player commands
    1. Fill out `DiscordAlertUserID` with your own Discord user ID. The bot mentions that user in debug-channel error alerts. To mention a role instead, enter `&` followed by the role ID; any other value is logged and not mentioned.
    1. Fill out `DiscordDebugChannel` with the channel ID for admin/debug alerts. Use the same ID as `DiscordBroadcastChannel` to keep commands and all channel notifications together, or choose a separate debug channel. The bot needs permission to view and send messages in each configured channel, and attach files in the broadcast channel for charts.
- Archipelago Config
  1. Fill out `ArchipelagoServer` if you're self-hosting the Archipelago Server
  1. Fill out `ArchipelagoPort` with the port number you've been assigned
  1. Fill out `ArchipelagoPassword` with password for your Archipelago room (if you have one)
  1. Fill out `ArchipelagoBotSlot` with the slot name of the bot (Not needed if you used the included yaml)
  1. Fill out `ArchipelagoTrackerURL` with the Tracker room URL
  1. Fill out `ArchipelagoServerURL` with the room URL
  1. Fill out `UniqueID` with the RoomID/seed/an arbitrary value (see [UniqueID](#UniqueID) for detailed usage)
- Item Filter Config
  - See [Item notification filters](item-notifications.md) for flag values and how channel filters differ from DM filters.
  1. Set `BotItemSpoilTraps` to `true` if you'd like to have traps spoiled, or change to `false` to hide traps
  1. Set `BotItemFilterLevel` to the level you'd like: `0`, `1`, or `2` (see the main [Readme](/README.md) for details on what the filter levels mean). Level `2` means progression items only. This value is read from your local `config.json` (ignored by git), not hardcoded in the bot. The generic `config.json.template` keeps the default level `0`.
- Relay Config
  1. Set desired AP to Discord relay options. For hint result notifications,
     see [Hint result notifications](hint-notifications.md).
- Drawbridge Config
  1. Set `DiscordBridgeEnabled` to `true` if you'd like chat from discord bridged to AP.
- Meta Config
  1. Set `FlavorDeathLink` to `true` if you'd like custom flavored deathlink messages.
- Daily Post Config (optional, disabled by default)
  1. Set `Enabled` to `true` to post `$checkcount`, `$checkgraph`, and `$deathcount` results to the broadcast channel once a day, in that order.
  1. Set `Time` to the local 24-hour `HH:MM` time you want the post (default `00:00`).
  1. Set `Timezone` to an IANA timezone such as `America/New_York` (default `America/New_York`).
  1. The bot waits for the next configured time after startup rather than posting immediately, and it remembers the last posted local date in `logs/<UniqueID>/DailyPostState.json` so restarts do not repeat a day. If the process is suspended across the scheduled time, the missed post may still be delivered late once after it resumes.
  1. Each report is attempted once per day. A failed report does not prevent later reports from running. Failures are logged and not retried the same day; the bot tries again at the next day's scheduled time.
  1. `SelfHostNoWeb` mode disables the check graph, so daily posts need the tracker web page.
- Command Config (optional)
  1. `PrefixCommandsEnabled` and `SlashCommandsEnabled` default to `true` and independently enable `$` and `/` commands. Use JSON booleans; missing or malformed values use `true`. Restart after editing.
  1. Disabling prefix commands includes message-only admin commands. Re-enable them through the config file. Disabling slash commands removes them from the configured server on successful sync and rejects stale interactions.
  1. Notification relays and scheduled reports continue even with both command interfaces disabled.
- Notification Config (optional)
  1. `ItemMentionsEnabled` defaults to `false`. Set it to JSON `true` to allow item pings, then each registered player must explicitly opt in with `/notify` or `$notify`. See [item pings and watches](item-watches.md).
  1. `ConnectionUnavailableMessage` and `ConnectionRestoredMessage` customize notices sent after a prolonged room disconnect and after recovery. The built-in defaults are plain text.
  1. Use `{room_url}` for the room URL (the bot supplies `<` and `>` around it to suppress Discord embeds) and `{server_address}` for the plain `host:port` value. Other text, including emoji, is sent as written. Missing, malformed, empty, or non-string values fall back to the built-in messages.
  1. `SleepWakeEditWindowMinutes` (default `15`): if the room is reachable again within this many minutes of the unavailable notice, the bot edits that notice into the restored message instead of posting a second message. `0` always posts a new message.
  1. `HintMessage` (see [Hint result notifications](hint-notifications.md)) replaces the complete hint result announcement. Placeholders: `{receiver}`, `{item}`, `{location}`, `{finder}`, `{status}`, `{entrance}`. Result posts never ping anyone and are capped at Discord's 2000-character limit. The built-in fallback is plain text without any emoji; emoji prefixes in the shipped `config.json.template` are examples, not defaults.

You're free to leave the Advanced Config section as-is unless you know what you're doing.  
Detailed references for the `config.json` can be found on the main [Readme](/README.md)

### Selfhosted AP Servers with no WebHost
If you run a selfhosted AP server but elect to not have the webhost module running, the bot will throw a fit unless you disable WebHost-specific functionality.  
In the `Advanced Config` section:
- Set `SelfHostNoWeb` to `true`

## Step 5) Create Python venv + Dependencies (You only need to do this once)

### Linux / WSL / MacOS

1. Create the venv: `python -m venv bridgeipelago`
1. Activate the venv: `source bridgeipelago/bin/activate`
1. Install dependencies: `pip install -r requirements.txt`

### Windows Users (Powershell):
**Bridgeipelago Windows support has been removed in v3.0. Some day I'll add it back.**  
**You can use WSL to run Bridgeipelago VERY easily. [WSL Setup](https://learn.microsoft.com/en-us/windows/wsl/install)**  
~~You may need to run `Set-ExecutionPolicy Bypass` to enable scripts.~~
~~1. Create the venv: `py -m venv bridgeipelago`~~
~~1. Activate the venv: `./bridgeipelago/Scripts/Activate.ps1`~~
~~1. Install dependencies: `pip install -r requirements.txt`~~

## Step 6) Finally: Running the Bot
Ensure you've joined the venv and set up the config.json file, then run: `python3 bridgeipelago.py`  
~~> Windows users may need to run `py bridgeipelago.py` depending on how Python set up its aliases.~~

You'll see the bot connect to your Discord channel and join the Archipelago game.

# Issues:
You can join the discord and post in the [tech-support](https://discord.gg/wpdPprvYgX) channel for assistance on setting up the bot.

-----
-----

## UniqueID
This value is used to store the bot's files in key'd directories for use in a run.  
When changing rooms / AP runs, you'll need to manually purge the data directories and restart Bridgeipelago or run the [Swapping rooms via Discord](#swapping-rooms-via-discord) process via discord.

## Swapping Rooms via Discord
You have the ability to swap rooms without manually editing the config.json of the bot.
This process will automatically create new directories, and reload all data needed for running Bridgeipelago.  

It's a fairly painless process, but should be done carefully and in order, as to not break Bridgeipelago into a thousand pieces.

**(Make sure the room is up, and connectable by clients before you $reloadtracker!)**

These commands require **Administrator** or **Manage Server** permission in the server. Passwords are never echoed back to the channel.

1. `$setconfig ArchipelagoPort <port>`  
    - Sets the port
1. `$setconfig ArchipelagoTrackerURL <tracker URL>`  
    - Sets the tracker URL
1. `$setconfig ArchipelagoServerURL <server URL>` 
    - Sets the server URL
1. `$setconfig UniqueID <uniqueID>`
    - Sets the new UniqueID
1. $reloadtracker
    - Tells the tracker to reload to fetch new room data, datapackages, and connection data for the new room
1. $reloaddiscord
    - Finally, tell discord to reload to make sure it's cleaned out

By setting the values in this order, you can prep the new connection in steps 1-3, create the new directories in step 4, populate new room data with step 5, then finally; refresh discord in step 6

If at any time this process breaks or throws an error here are some common troubleshooting tips:
  - Stopping Bridgeipelago fully and restarting it
  - Stopping Bridgeipelago, deleting the contents of the data directories, and restarting Bridgeipelago
  - Nuking everything and restarting 
  - Praying to the magic smoke god in your computer to make it work

If none of this works, poke in the discord for help and someone will get you sorted. :)

### Hosted room reconnects and seed identity

Set `ArchipelagoConfig.ExpectedSeedName` in local `config.json` to the exact
`seed_name` of the intended seed before starting the bot. For a locally generated
seed archive, this is the name between `AP_` and `.archipelago` in its multidata
filename. Confirm it against your original seed; do not copy an identity from an
unverified server at an old port. Update it when intentionally changing seeds.
The generic template contains a placeholder, not a usable seed identity.

Before every tracker or hint-client connection, the bot queries the room-status
API with a 10-second timeout and uses its latest port. An API failure postpones
the attempt instead of using a stale port. A sleeping room can retain its old
port in that API, so the bot also checks `RoomInfo.seed_name` before sending login
credentials or replacing cached room data. Missing or mismatched identities
refuse login. Self-hosted rooms skip the API and use the configured port, but
still require the seed identity check.

Endpoint lookups never open `/room/` or wake the room. Discord remains available
while connection attempts fail, including at startup. After repeated failures,
the bot retries once per minute and posts one configured unavailable notice;
a verified successful login posts the matching recovery notice. The periodic
port poll remains as an additional check. A changed port is saved to local config
and announced in the broadcast channel.

### Reports after release

Scheduled check counts and progress graphs stop once every world on the web
tracker has all locations checked or released. Death counts stop independently:
every slot listed in `DailyPostConfig.DeathLinkSlots` must show `Goal Completed`.
A released world does not by itself count as a completed DeathLink goal.

Set `DeathLinkSlots` to the exact names of every DeathLink-enabled player, for
example `["Player One", "Player Two"]`. The roster is explicit because disconnected
clients' DeathLink participation cannot be reliably inferred from online tags or
the death log. Keep it in sync with the session's enabled settings. `null` or an
omitted setting means unknown participation, so death counts keep posting; `[]`
means no DeathLink games, so no scheduled death count is posted. Invalid names or
malformed tracker data skip that day's reports and log the error.

Manual report commands remain available. The tracker check uses a 15-second
timeout, does not wake the room, and repeats at the next scheduled time. With
`SelfHostNoWeb`, completion cannot be determined automatically.

Tracker reconnect failures no longer permanently disable tracking. After three
failed attempts, retries cool down to once per minute and one unavailable notice is
sent per outage; a successful connection resets that warning.
