# Hint result notifications

`RelayConfig.HintMessages` posts each newly discovered Archipelago hint result
to the configured broadcast channel. The generic `config.json.template` keeps
this setting `false`; existing `APClientHint` and `APClientHintLocation` command
relays are unchanged. With `HintMessages` enabled, successful explicit hint
results and tracker subscriptions share one dedupe path. Hint command errors
and point/result text continue to be relayed as before.

The tracker subscribes only to hint stores for slots on its own team. On the
first `Retrieved` snapshot for each store, existing hints are silently
baselined; `SetReply` packets that arrive before that baseline are buffered
(bounded to the most recent ones) and silently baselined with it, and a
missing or malformed store snapshot counts as an empty baseline. A hint added
before that initial baseline arrives can be silently
baselined too, indistinguishably from a hint that was already in the store's
history. After the baseline, a hint is announced once during normal operation,
even if it appears in both the receiver and finder stores or its found/status
value later changes. State and pending deliveries are saved under that
session's log directory so restarts do not repeat results whose acknowledgments
were persisted, and newly recorded hints can catch up after a disconnect.

Delivery uses persisted deduplication with an at-least-once crash window. If
Discord accepts a message but the process crashes before the delivery
acknowledgment is persisted, that pending result can be sent again after
restart. If the state file cannot be written after Discord accepted a result,
this process does not send that result again; a restart may send it once more.
Pending results are restored
on the first chat-loop pass after the Discord process starts, even if the
tracker has not reconnected yet. Transient send failures are retried with
exponential backoff: eleven attempts in total, waiting 2, 4, 8, 16, 32, 64,
128, 256, 300, and 300 seconds between them. After the eleventh failure, about
18.5 minutes after the first, the result moves to a persisted deadletter map
instead of blocking other results.
A deadlettered result is not retried again or restored on restart. Discord HTTP
400 and 403 rejections are deadlettered immediately. These retry limits are
constants in code, not `config.json` keys. Delivery failures are logged as short
summaries without the message payload, throttled per result so a stuck queue
cannot flood the log. Every result is handled in isolation on the normal
chat-loop cadence, so one failing result never starves channel chat. Rendered
result text is capped at Discord's 2000-character message limit.

The broadcast includes the receiver, item, finder, location, status, and an
entrance when the server supplies one. Unknown game-data IDs use readable ID
fallbacks. `NotificationConfig.HintMessage` can replace the complete result
text. Its literal placeholders are `{receiver}`, `{item}`, `{location}`,
`{finder}`, `{status}`, and `{entrance}`; `{entrance}` is empty when absent and
otherwise includes the ` Entrance: ` prefix. A blank or non-string override
uses the plain built-in message, which has no emojis; the emoji prefixes in
the shipped `config.json.template` are examples, not defaults. Result posts
suppress Discord mentions and escape literal `@` characters. Explicit `/hint`
requests can announce a result that was already
present in the silent baseline. Repeated hint identities are deduplicated;
`/hints` remains available to inspect the room's existing hints.

This relay posts to the configured broadcast channel only. It does not
subscribe to other teams, alter personal DM delivery, change raw AP client
command settings, or apply item progression filters or completed-game snoozing.
If the saved relay state is corrupt, the next store snapshots are treated as
fresh silent baselines to avoid posting the room's full hint history. The
unreadable file is moved to `<name>.corrupt` (or `<name>.corrupt.N` when earlier
copies exist) before anything is written, so its raw contents stay available for manual
recovery. If it cannot be moved, saves are blocked rather than overwriting
it. The problem is logged once at startup.
