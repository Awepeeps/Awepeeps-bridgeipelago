# Opt-in item pings and watches

These features belong to the Awepeeps `awepeeps-patches` branch. Upstream
registration, catch-up, and hints do not provide these preferences.

## Enable pings

Merge this into the existing `NotificationConfig` section of your ignored
`config.json`, then restart:

```json
"NotificationConfig": {
    "ItemMentionsEnabled": true
}
```

The default is `false`. Only JSON `true` enables pings. Each player must also
register for a slot and explicitly select a notification mode. Registering,
watching an item, and asking for a hint do not opt anyone into pings.

## Player commands

Use the configured broadcast channel. Both command interfaces are enabled by
default; `CommandConfig` can disable either one.

- `/notify mode:all` or `$notify all`: opt into pings for item posts addressed
  to any slot you registered for.
- `/notify mode:watched` or `$notify watched`: opt into pings only for items
  on your watch list.
- `/notify mode:off` or `$notify off`: stop pings.
- `/watch slot:deathpie item:Mikiri Counter` or
  `$watch deathpie | Mikiri Counter`: watch an item without requesting a hint
  or spending hint points.
- `/unwatch slot:deathpie item:Mikiri Counter` or
  `$unwatch deathpie | Mikiri Counter`: remove a watch.
- `/watches` or `$watches`: list your pending watches.

Use the exact slot and item names; matching ignores case. Watches require
registration for the slot and an item known to that slot's game. Multiple
people can register for the same slot and keep independent preferences.
A Discord `$hint` or `/hint` request also adds a watch for a recognized item
in your registered slot. Hinting still has the normal server hint cost;
watching alone does not reveal an item's location.

Automatic watches apply to Discord hint requests with a recognized item name.
They are added when the request is queued, even if the server later declines
the hint, for example because the slot lacks points. Hints requested directly
from an Archipelago game client do not create a personal watch; use `/watch`
for those. Existing hint-result announcements continue as before.

## Delivery and storage

Pings appear on the existing item notification, outside its code block.
Only the selected users are allowed mentions. Item text cannot enable role
or everyone mentions. Hint announcements and chat relays still suppress
mentions.

For unusually large messages or subscriber lists, the post is split into
bounded chunks to respect Discord's message and allowed-mention limits.
Ordinary item posts keep the pings on the same message. Discord notification
preferences can still suppress push alerts even when a mention is present;
see [Discord's allowed mentions reference](https://docs.discord.com/developers/resources/message#allowed-mentions-object).

Watches do not override `BotItemFilterLevel`, trap spoiler settings, or
`SnoozeCompletedGames`. An item hidden by those settings produces no channel
post or ping. Observing its arrival still completes the matching watch.
Likewise, a matching arrival completes a watch while pings are globally
disabled or the player has selected `off`.

Preferences and watches are stored under the session's ignored registration
data, keyed by stable Discord user ID. They survive restarts and are separate
for each `ArchipelagoConfig.UniqueID`. Clearing registrations also clears
notification consent and watches; register and opt in again afterwards.

The existing registration files still use Discord usernames. If you change
your username, you may need to register again so new slot commands can find
your registration. `/notify mode:off` and `$notify off` remain available even
when no registration can be found.

A watch means the next observed matching copy, not a required quantity or a
search of already-received items. It does not scan a spoiler or query the
server for an item's location. Repeated copies can be watched again after
the first watch completes.

The last 5,000 observed item events are remembered to suppress repeated pings
across reconnects and restarts. Very old replays outside that window can ping
again. Discord sends and local saves are separate operations: a crash after
Discord accepts a post but before the state is saved can repeat a ping.
A failed send leaves the watch pending; this feature does not add a durable
retry queue for the existing item feed.
