# Item notification filters

The bot filters channel item posts using the classification flags received from
the Archipelago server. It does not classify items by name or by how useful
they seem in-game.

## Item flags and filter levels are different

Network item flags are bit flags:

- `0`: normal/filler.
- `1`: progression (the progression bit).
- `2`: useful (the useful bit).
- `4`: trap (the trap bit).

Flags can combine: `3` means progression + useful, `5` progression + trap,
`6` useful + trap, and `7` all three. See the
[Archipelago item classification source](https://github.com/ArchipelagoMW/Archipelago/blob/0.6.7/BaseClasses.py#L1393).

`ItemFilterConfig.BotItemFilterLevel` is a separate setting:

- `2`: progression only; any flag with bit `1` set passes (`1`, `3`, `5`, `7`).
- `1`: progression or useful; any flag with bit `1` or `2` set passes.
- `0`: all non-pure-trap items, including normal/filler.

Pure traps (flag `4`) bypass that level check and are posted only when
`ItemFilterConfig.BotItemSpoilTraps` is `true`. Combined flags follow the
bit checks above; `BotItemSpoilTraps: false` is not a blanket exclusion of
every item that also has a trap bit.

## Configure the channel feed

These example sections keep progression-only channel posts, hide pure traps,
and suppress item posts to finished players:

```json
"ItemFilterConfig": {
    "BotItemSpoilTraps": false,
    "BotItemFilterLevel": 2
},
"AdvancedConfig": {
    "SnoozeCompletedGames": true
}
```

Merge these values into the existing sections of your ignored local
`config.json`; keep the other configuration keys. Restart the bot after editing.
The values persist across restarts and are not hardcoded community preferences.
The generic `config.json.template` defaults to filter level `0`.

`SnoozeCompletedGames` checks the **recipient's** recorded goal status. It
suppresses posts to that finished player, even for progression items; it does
not suppress an item merely because its sender has finished.

## Channel feed versus other notifications

Item events are logged before channel filtering. Non-trap items sent by other
players can still be queued for personal DMs even when hidden in the channel.
`$ketchmeup` and `/ketchmeup` select their filter per request; pure traps require
filter `0` and must have been queued with trap spoilers enabled. Self-found
items can appear in the channel but are not queued as missed items for DMs.

Chat, goal, release, collect, countdown, and DeathLink relays have separate
`RelayConfig` switches. Changing the item filter does not change those switches.

For example, in the Awepeeps Fall 2026 session, the server flagged **200 Spirit
Light** as progression (`1`) and **100 Spirit Light** as filler (`0`). At filter
level `2`, the former is posted and the latter is hidden. Check each event's
server flags rather than assuming all amounts of a resource share a category.
