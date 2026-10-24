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
A Discord `$hint` or `/hint` request also adds a watch when Archipelago returns
an unresolved hint for an item in your registered slot. Hinting still has the normal server hint cost;
watching alone does not reveal an item's location.

Automatic watches use the canonical item returned by Archipelago, rather than
the text you typed. For example, if the server resolves `Mask Fragement: Left`
to `Mask Fragment: Left`, the watch uses `Mask Fragment: Left`. A declined
request, a request with no hint result, or a result for an already-found item
does not create a new watch. This works even when hint-result announcements
are disabled. Only the requester gets the automatic watch, and they must
still be registered for the receiving slot when the result is processed.
Hints requested directly from an Archipelago game client do not create a
personal watch; use `/watch` for those.

Resolved results are also matched to the requested item, so an unrelated hint
for the same slot cannot create a watch during a concurrent request. If a
query is ambiguous or refers to a broad item group, automatic watching may
leave the list unchanged; use `/watch` with the exact item name in that case.

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

A currently registered player's pending watch overrides `BotItemFilterLevel`.
For example, a watched filler item is posted even in a progression-only
channel. The override does not require ping consent: a player with mode `off`
still requests a channel post by watching an item. Mentions continue to require
both global enablement and the recipient's explicit `all` or `watched` mode.
Unwatched items keep the configured channel filter.

The separate pure-trap spoiler setting and `SnoozeCompletedGames` still apply.
An item hidden by those settings produces no channel post or ping. Observing
its arrival still completes the matching watch. Likewise, a matching arrival
completes a watch while pings are globally disabled or the player has selected `off`.

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
