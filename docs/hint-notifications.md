# Hint result notifications

`RelayConfig.HintMessages` posts each newly discovered Archipelago hint result
to the configured broadcast channel. The generic `config.json.template` keeps
this setting `false`; existing `APClientHint` and `APClientHintLocation` command
relays are unchanged. With `HintMessages` enabled, successful explicit hint
results and tracker subscriptions share one dedupe path. Hint command errors
and point/result text continue to be relayed as before.

The tracker subscribes only to hint stores for slots on its own team. On the
first `Retrieved` snapshot for each store, existing hints are silently
baselined. A hint added before that initial baseline arrives can be silently
baselined too, indistinguishably from a hint that was already in the store's
history. After the baseline, a hint is announced once during normal operation,
even if it appears in both the receiver and finder stores or its found/status
value later changes. State and pending deliveries are saved under that
session's log directory so restarts do not repeat results whose acknowledgments
were persisted, and newly recorded hints can catch up after a disconnect.

Delivery uses persisted deduplication with an at-least-once crash window. If
Discord accepts a message but the process crashes, or the state write fails,
before the delivery acknowledgment is persisted, that pending result can be
sent again after restart. A Discord send failure leaves the result pending for
retry.

The broadcast includes the receiver, item, finder, location, status, and an
entrance when the server supplies one. Unknown game-data IDs use readable ID
fallbacks. `NotificationConfig.HintMessage` can replace the complete result
text. Its literal placeholders are `{receiver}`, `{item}`, `{location}`,
`{finder}`, `{status}`, and `{entrance}`; `{entrance}` is empty when absent and
otherwise includes the ` Entrance: ` prefix. A blank or non-string override
uses the plain built-in message. Explicit `/hint` requests can announce a result that was already
present in the silent baseline. Repeated hint identities are deduplicated;
`/hints` remains available to inspect the room's existing hints.

This relay posts to the configured broadcast channel only. It does not
subscribe to other teams, alter personal DM delivery, change raw AP client
command settings, or apply item progression filters or completed-game snoozing.
If the saved relay state is corrupt, the next store snapshots are treated as
fresh silent baselines to avoid posting the room's full hint history.
