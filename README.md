<p align="center">
<img src="assets/bridgeipelago_r.png" alt="[bridgeipelago logo]" width="300"/>
</p>

# bridgeipelago

Discord bridge for Archipelago in python

[Discord Link](https://discord.gg/5v9P3qNPXp)

## Setup:
See the [SETUP GUIDE](docs/setup.md)  

There is also a [DOCKER](docs/docker.md) setup guide as well (if you're into that kind of thing)

You can change rooms via discord by following the directions in [SWAPPING ROOMS IN DISCORD](docs/setup.md#swapping-rooms-via-discord)  

**Bridgeipelago Windows OS support has been removed in v3.0. Some day I'll add it back, if I'm feeling brave enough.**  
**You can use WSL to run Bridgeipelago VERY easily. [WSL Setup](https://learn.microsoft.com/en-us/windows/wsl/install)**  

## Functionality:

### Core
This bot will monitor and track progress as you play through your Archipelago run.  
This allows for some fun stats to be collected, logging from the bot on item checks that are found, and death notices from DeathLink players.

When a check is found or a deathlink is received, it'll output the check in a discord channel.

```
Examples:
An item for yourself: 
> Quasky_SM64 found their Power Star
> Check: LLL: Boil the Big Bully

An item for someone else:
> Quasky_SM64 sent Lens of Truth to Quasky_OOT
> Check: LLL: Bully the Bullies

When someone with deathlink dies, it'll shame them in this channel.
> Deathlink received from: Quasky_OOT
> Cause: He was a dummy
```

When an archipelago.gg room goes to sleep after a long stretch without new checks, the bot posts a notice with the room link. Opening that link wakes the room, and the bot posts again once it reconnects. The bot does not keep the room awake.

What this bot will NOT do:
 - DM / ping you without consent
 - Find items for you
 - Find the reason the children of this world have forgotten the meaning of Christmas


In v1.2.5 and above, messages will be color-coded to their usefulness in the Archipelago run.  
|Color|Item Type|
|---|---
|YELLOW|Progression Item|
|BLUE|Useful Item|
|WHITE|Filler/Normal Item|
|RED|Trap Item|

---
### Ketchmeup
You can also ask the bot to DM you all the items you've gained since last asking him.
(See the registration below)

**IMPORTANT NOTES:**
- This sends only the items queued for your registered slots since your last catch-up. Each Discord account keeps its own position, so catching up never removes items for other players registered to the same slot, and the queue file itself is not deleted. Items sent to you while you're still playing will appear in your next catch-up, because the bot has no idea if you're playing or not.
- A slot you newly register for starts at the queue's current end. Accounts registered before this change have no saved position, so their first catch-up sends everything still in that slot's queue file.
- Overlapping `$ketchmeup` calls from the same account run one at a time, so two quick calls never DM the same items twice.
- The bot will NOT log items you give to your own game. You're expected to remember what you find for yourself
- However the bot only understands slots. So if you're playing two games, the bot just assumes the other game is someone else and logs it. While ignoring the checks from your own game.

```
Example:
You         || Item                  || Sender      || Location
Quasky_OOT2 || Piece of Heart        || Quasky_OOT4 || Gerudo Training Ground Near Scarecrow Chest
Quasky_OOT2 || Rupees (20)           || Quasky_OOT4 || Ganons Castle Shadow Trial Front Chest
Quasky_OOT2 || Bow                   || Quasky_OOT4 || Ganons Tower Boss Key Chest
Quasky_OOT2 || Gold Skulltula Token  || Quasky_OOT4 || Sheik at Colossus
Quasky_OOT2 || Rupees (200)          || Quasky_OOT4 || KF Midos Bottom Left Chest
Quasky_OOT2 || Gold Skulltula Token  || Quasky_OOT4 || KF Shop Item 6
```
Hopefully that makes sense. 

---
---

## Commands
For item flag values, channel filter levels, traps, finished-player suppression,
and how channel posts differ from personal DMs, see [Item notification filters](docs/item-notifications.md).

**All commands are case-sensitive.** `CommandConfig.PrefixCommandsEnabled`
and `CommandConfig.SlashCommandsEnabled` independently enable the `$` and `/`
interfaces; both default to `true`. Use JSON booleans and restart after editing
config. Disabling `$` commands also disables message-only admin commands;
edit the config file to re-enable them. Disabling `/` commands removes them
from the configured server on the next successful sync. Notification relays
and scheduled daily posts continue with either or both interfaces disabled.

|Player Commands|Description|
|---|---|
|`$register <slot>`|Adds the slot provided to the user's registration file. A blank slot gets the usage reply instead. Queue file names are derived from the slot: ordinary names keep `<name>.csv`, while Windows device names and characters such as `:` or `/` are percent-encoded (`Con` is stored as `%63on.csv`, `A:B` as `a%3Ab.csv`)|
|`$listreg`|Lists all the slots you are registered for|
|`$clearreg`|Clears the user's registration file|
|`$ketchmeup <filter>`|DMs the checks queued for your registered slots since your last catch-up (tracked per Discord account; see Ketchmeup above)|
||`2` - Only Logical Progression Items|
||`1` - Logical + Useful items|
||`0` or empty - Logical + Useful + Normal items + Traps\*|
||\*Traps will only be sent if the filter is 0 AND BotItemSpoilTraps is enabled in the config|
|`$groupcheck <slot>`|DMs the user all checks in the slot's ItemQueue file (the full stored queue, not a per-account position), used to catch up on group games|
|`$hints`|DMs the hinted items for a player's registered slots|
|`$hint <slot> \| <item>`|Allows you to request a hint from Archipelago and it'll be relayed to discord. The request is acknowledged publicly in the channel, naming you, with mentions suppressed. The slot and item are each limited to 100 characters, as with `/hint`. A hint spends that slot's hint points, so you can only request one for a slot you're registered to (server admins can request any slot); a bare `$hint` replies with the usage|
|`$deathcount`|Scans the deathlog and tallies up the current deathcount for each slot|
|`$checkcount`|Fetches the current Arch server's progress in simple txt format|
|`$checkgraph`|Plots the current Arch progress in a picture|

See [Hint result notifications](docs/hint-notifications.md) for relay settings,
deduplication, and delivery behavior.

Use `/notify` or `$notify` to opt into item pings and `/watch` or `$watch` to
track an item without spending hint points. Pings also require the global
`NotificationConfig.ItemMentionsEnabled` setting (default `false`). See
[opt-in item pings and watches](docs/item-watches.md) for commands and behavior.

Watched items bypass the channel's item filter level while mentions remain
opt-in. Discord hints create watches from the server's resolved item, so a
typo accepted by Archipelago still watches the canonical item. Pure-trap
spoiler settings and finished-player suppression remain in effect.

|Maintenance Commands|Description|
|---|---|
|`$iloveyou`|We all need to hear this sometimes.|
|`$hello`|The bot says hello!|
|`$reloadtracker`|Forces the tracker client to reload|
|`$reloaddiscord`|Forces the discord bot to reload|
|`$setconfig <key> <value>`|Allows you to set config.json options via discord|
||Current keys: `ArchipelagoPort`, `ArchipelagoPassword`, `ArchipelagoTrackerURL`, `ArchipelagoServerURL`, `UniqueID`|
|`$ArchInfo`|\[CONSOLE] General bot details for debugging config values [1]|

`$setconfig`, `$reloadtracker`, `$reloaddiscord`, and `$archinfo` only run for members with **Administrator** or **Manage Server** permission in the server; other members get a refusal. The bot reads that permission from Discord, not from the message. `$setconfig` accepts `ArchipelagoPassword` from an admin but never echoes a password or token back to the channel, and `$archinfo` prints secret-named values (keys containing `token`, `password`, or `secret`) as `<redacted>`. `UniqueID` values containing path separators or control characters are rejected. If the room password was ever typed in the channel, rotate it on the room. `$hello` and `$iloveyou` are not gated. Bot commands are only read in the broadcast channel.

Relayed Archipelago chat, hint results, and `/hint` or `$hint` acknowledgements are sent with Discord mentions suppressed, so they never ping anyone. Debug-channel error alerts still mention the `DiscordAlertUserID` you configure.

**\[1] DebugMode only commands**  
**\[1] DebugMode can expose unintended system information. Use with care.**
  
## config.json Keys and Descriptions
|Key|Description|
|---|---|
|**Discord Config**||
|`DiscordToken`|Your Discord Bot's token|
|`DiscordBroadcastChannel`|Discord channel ID for AP notifications and player commands|
|`DiscordAlertUserID`|Optional Discord **user** ID, or `&` followed by a role ID, to mention in debug-channel error alerts [7]. Any other value is logged and not mentioned. CheckArchHost alerts once after 5 consecutive failed checks; the next valid check after that alert posts a recovery notice|
|`DiscordDebugChannel`|Discord channel ID for admin/debug alerts; may match `DiscordBroadcastChannel` to keep bot activity in one channel|
|`AnnounceConnection`|Send a debug-channel startup announcement (default: `false`)|
|||
|**Archipelago Config**||
|`ArchipelagoServer`|The URL of the Archipelago server you'd like to connect to|
|`ArchipelagoPort`|The port of the Archipelago server you'd like to connect to|
|`ArchipelagoPassword`|The password of the Archipelago room you'd like to connect to|
|`ArchipelagoBotSlot`|The name of the slot you'd like the bot to use when connecting to archipelago|
|`ArchipelagoTrackerURL`|URL of the tracker you'd like to query|
|`ArchipelagoServerURL`|URL of the server you'd like to query|
|`UniqueID`|This UniqueID will sort all the game data into a key'd folder so you can swap between rooms via discord [2]|
|||
|**Item Filter Config**||
|`BotItemSpoilTraps`|The Bot will spoil traps by posting them in chat and ketchmeup [3]|
|`BotItemFilterLevel`|Sets the bot filter level `0` - `1` - `2`, to exclude items from Discord posts [3]. This is read from your local `config.json`, which is ignored by git; it is not hardcoded.|
||All items are still be queued for ketchmeup|
||`2` - Only Logical Progression Items|
||`1` - Logical + Useful items|
||`0` - Logical + Useful + Normal items|
|||
|**Relay Config**||
|`ChatMessages`|Will relay chat messages (This also includes any ! messages, eg. !hint, !release, !collect)|
|`ServerChatMessages`|Will relay server chat messages|
|`GoalMessages`|Will relay goal messages|
|`ReleaseMessages`|Will relay release messages|
|`CollectMessages`|Will relay collect messages|
|`CountdownMessages`|Will relay server countdown messages|
|`DeathlinkMessages`|Will relay deathlink messages|
|`SendOwnSlotMessages`|By default, the bot will not relay its own chat messages from the AP room to the Discord channel in order to prevent infinite relay loops. If you're loading the bot into an existing AP without a slot for the bot to connect to, however, you may want to disable this feature in order to relay chat messages for the player using that slot. The bot will never relay Discord messages back from the room to the channel, even with this option on.|
|`APClientHelp`|Will relay `!help` to discord|
|`APClientLicense`|Will relay `!License` to discord|
|`APClientCountdown`|etc...|
|`APClientOptions`|etc...|
|`APClientAdmin`||
|`APClientPlayers`||
|`APClientStatus`||
|`APClientRelease`||
|`APClientCollect`||
|`APClientRemaining`||
|`APClientMissing`||
|`APClientChecked`||
|`APClientAlias`||
|`APClientGetItem`||
|`APClientHint`||
|`APClientHintLocation`||
|`APClientVideo`|You get the idea. :)|
|||
|**Command Config**||
|`PrefixCommandsEnabled`|Enable `$` commands, including admin commands (default `true`). Missing or malformed values use `true`.|
|`SlashCommandsEnabled`|Enable player `/` commands (default `true`). Missing or malformed values use `true`; disabled commands are rejected and removed from the configured server on successful sync.|
|||
|**Drawbridge Config**||
|`DiscordBridgeEnabled`|Will toggle the ability to send messages to AP from Discord|
|||
|**Daily Post Config**||
|`Enabled`|Post `$checkcount`, `$checkgraph`, and `$deathcount` results to the broadcast channel once per local day, in that order (default: `false`)|
|`Time`|Local 24-hour `HH:MM` time for the daily post (default: `00:00`)|
|`Timezone`|IANA timezone for `Time` and the once-per-day date, e.g. `America/New_York` (default: `America/New_York`)|
||Daylight saving changes are handled automatically. The bot waits for the next configured time after startup and does not catch up on a post missed while the bot was stopped. If the process is suspended across the scheduled time, it may still deliver that missed post late once after it resumes.|
||The last posted local date is stored in `logs/<UniqueID>/DailyPostState.json`, so reconnects and restarts cannot post twice in one day.|
||Each report is attempted once per day in order: `$checkcount`, `$checkgraph`, then `$deathcount`. A failed report does not prevent later reports from running. Failures are logged and not retried the same day; the bot tries again at the next day's scheduled time.|
||`SelfHostNoWeb` mode disables the check graph, so daily posts require the tracker web page.|
|||
|**Notification Config**||
|`ItemMentionsEnabled`|Allow opted-in registrants to be mentioned on item posts (default `false`). Only JSON `true` enables this. Each player must also choose `all` or `watched` with `/notify` or `$notify`; registration and hints do not grant consent. See [item watches](docs/item-watches.md).|
|`ConnectionUnavailableMessage`|Message sent after a sustained Archipelago disconnect. Defaults to `Archipelago connection unavailable. {room_url}`. `{room_url}` is wrapped in `<` and `>` to suppress Discord link embeds.|
|`ConnectionRestoredMessage`|Message sent after a room connection recovers. Defaults to `Archipelago connection restored: {server_address}`. `{server_address}` is the plain host and port, such as `archipelago.gg:38281`.|
||Both messages support literal `{room_url}` and `{server_address}` substitutions. Empty or non-string values use the plain text default.|
|`SleepWakeEditWindowMinutes`|If the room is reachable again within this many minutes of the unavailable notice, the bot edits that notice into the restored message instead of posting a second one. Defaults to `15`; `0` always posts a new message. Invalid values use `15`.|
|||
|**Meta Config**||
|`FlavorDeathLink`|Will change deathlink messages to have a little more personality [4]|
|`DeathLinkLottery`|Unused... for now >:) |
|||
|**Advanced Config**||
|`LoggingDirectory`|Directory of the bot's own logs [5]|
|`PlayerRegistrationDirectory`|Directory of the Player Registration Mappings [5]|
|`PlayerItemQueueDirectory`|Directory that stores player item queues [5]|
|`ArchipelagoDataDirectory`|Directory for the Archipelago data packages [5]|
|`QueueOverclock`|The speed the bot will process messages|
||1 - 1/second (default)|
||0.5 - 2/second|
||0.2 - 5/second|
||0.1 - 10/second|
|`SnoozeCompletedGames`|Enables skipping posting items to discord FOR completed games|
|`JoinMessage`|A custom join message (console only) for the bot|
|`DebugMode`|Enables extra debug chat/bot options [6]|
|`SelfHostNoWeb`|Disabled WebHost-specific functionality for self-hosted games with no WebHost module|
|`CycleDiscord`|Setting to a value above 0 will restart the discord process every X seconds|

**\[7] Use a numeric user ID, or `&` followed by a numeric role ID (for example `&123456789`) to mention a role. The `&` must be the first character.**

**\[2] Check [UniqueID](docs/setup.md/#UniqueID) in the setup readme on what this value is used for**  

**\[3] Items will still be logged in the BotLog**

**\[4] Edit /modules/DeathlinkFlavor.py to your heart's content for custom flavor**

**\[5] Ensure directories end in a /**  
**\[5] These should be four different directories, all these logs in the same place will break the bot.**

**\[6] DebugMode can expose unintended system information. Use with care.**

---
---

### Special Thanks

Thank you to the people who encouraged me to pour my time into this bot. Specifically the Zajcats.

Thank you to Rainwave Discord community for help with a private alpha-test, bugfixing, and being my eternal beta-testers.

Thank you to [endrawr](https://github.com/endrawr) and [ZeroShork](https://github.com/ZeroShork) for getting the basics of the docker implementation set up and allowing me to use their work.

Thank you to the Archipelago Community for making an amazing platform for the world to use.
