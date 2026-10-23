"""Protocol and durable delivery state for Archipelago hint result relays."""

import copy
import json
import os
import re
import tempfile
import time

MAX_DISCORD_TEXT_LENGTH = 2000
DEFAULT_MAX_DELIVERY_ATTEMPTS = 11
DEFAULT_RETRY_BASE_SECONDS = 2.0
DEFAULT_RETRY_MAX_SECONDS = 300.0
DEFAULT_LOG_INTERVAL_SECONDS = 60.0


def hint_identity(hint):
    """Return the stable identity of a hint, excluding mutable status fields."""
    return "|".join(str(hint.get(field, "")) for field in (
        "team", "receiving_player", "finding_player", "location", "item"
    ))


def hint_subscription_keys(connected):
    """Return the own-team read-hints keys advertised by a Connected packet."""
    team = connected.get("team")
    players = connected.get("players", [])
    slots = set()
    if isinstance(players, list):
        for player in players:
            if isinstance(player, dict) and player.get("team") == team:
                slot = player.get("slot")
                if isinstance(slot, int) and slot > 0:
                    slots.add(slot)
    # Group slots are not always present in players. Include a group slot when
    # at least one of its members is a player on this team.
    slot_info = connected.get("slot_info", {})
    if isinstance(slot_info, dict):
        for slot, info in slot_info.items():
            try:
                slot_id = int(slot)
            except (TypeError, ValueError):
                continue
            members = info.get("group_members", []) if isinstance(info, dict) else []
            if not isinstance(members, (list, tuple, set)):
                members = []
            member_slots = set()
            for member in members:
                try:
                    member_slots.add(int(member))
                except (TypeError, ValueError):
                    continue
            if slot_id in slots or (slot_id > 0 and slots.intersection(member_slots)):
                slots.add(slot_id)
    return team, ["_read_hints_{}_{}".format(team, slot) for slot in sorted(slots)]


def normalize_requested_hint(message, team=None):
    """Convert the 0.6.7 PrintJSON Hint envelope to the subscription shape."""
    item = message.get("item", {})
    if not isinstance(item, dict):
        item = {}
    status = 40 if message.get("found") else 0
    entrance = None
    extra = message.get("data", [])
    if isinstance(extra, list):
        for part in extra:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "hint_status":
                status = part.get("hint_status", status)
            elif part.get("type") == "entrance_name":
                entrance = part.get("text")
    return {
        "team": team,
        "receiving_player": message.get("receiving"),
        "finding_player": item.get("player"),
        "location": item.get("location"),
        "item": item.get("item"),
        "found": message.get("found", False),
        "status": status,
        "entrance": entrance,
        "item_flags": item.get("flags", 0),
    }


def delivery_error_status(error):
    """Return the HTTP status attached to a delivery error, when it has one."""
    status = getattr(error, "status", None)
    if isinstance(status, bool):
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def is_permanent_delivery_error(error):
    """Discord rejects malformed or forbidden messages without a useful retry."""
    return delivery_error_status(error) in (400, 403)


def _error_name(error):
    return type(error).__name__


def _safe_log_identity(identity):
    text = str(identity).replace("\r", " ").replace("\n", " ")
    return text[:160]


def unused_corrupt_path(path):
    """Return ``<path>.corrupt``, or ``<path>.corrupt.N`` when earlier copies exist."""
    candidate = path + ".corrupt"
    number = 1
    while os.path.lexists(candidate):
        candidate = "{}.corrupt.{}".format(path, number)
        number += 1
    return candidate


class LogThrottle:
    """Allow one log line per key per interval without depending on wall time."""

    def __init__(self, interval=DEFAULT_LOG_INTERVAL_SECONDS, clock=time.monotonic):
        self.interval = max(0.0, float(interval))
        self.clock = clock
        self._last = {}

    def should_log(self, key):
        now = self.clock()
        previous = self._last.get(key)
        if previous is not None and now - previous < self.interval:
            return False
        self._last[key] = now
        return True


class HintRelayState:
    """Durable snapshots, dedupe identities, and retryable result outbox.

    Version 1 files written before delivery metadata existed are still valid:
    missing retry/deadletter maps are treated as empty. Every mutation is
    applied to a copy and only becomes visible in memory after the atomic file
    write succeeds, so a failed write cannot silently drop a pending result.
    Two pieces of delivery bookkeeping are kept in memory for this process when
    their write fails: the retry or deadletter outcome of a failed send, and the
    acknowledgment of a send Discord accepted (``sent_unacknowledged``).
    """

    def __init__(
        self,
        path,
        clock=time.time,
        max_attempts=DEFAULT_MAX_DELIVERY_ATTEMPTS,
        retry_base_seconds=DEFAULT_RETRY_BASE_SECONDS,
        retry_max_seconds=DEFAULT_RETRY_MAX_SECONDS,
        log_interval_seconds=DEFAULT_LOG_INTERVAL_SECONDS,
    ):
        self.path = path
        self.clock = clock
        self.max_attempts = max(1, int(max_attempts))
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.retry_max_seconds = max(0.0, float(retry_max_seconds))
        self.data = self._empty_data()
        self.corrupt = False
        # Where the unreadable file was moved, or why it could not be moved yet.
        self.corrupt_backup = None
        self.corrupt_error = None
        # Identities Discord accepted in this process whose acknowledgment is not
        # persisted yet. They are never sent again by this process.
        self.sent_unacknowledged = set()
        self.log_throttle = LogThrottle(log_interval_seconds, clock)
        self._load()

    @staticmethod
    def _empty_data():
        return {
            "version": 1,
            "snapshots": {},
            "announced": [],
            "pending": {},
            "retries": {},
            "dead": {},
        }

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as state_file:
                data = json.load(state_file)
            if (not isinstance(data, dict) or data.get("version") != 1
                    or not isinstance(data.get("snapshots"), dict)
                    or not isinstance(data.get("announced"), list)
                    or not isinstance(data.get("pending"), dict)):
                raise ValueError("invalid hint relay state shape")
            # Delivery metadata was added after the first v1 format. Old files
            # simply do not have these maps.
            if not isinstance(data.get("retries"), dict):
                data["retries"] = {}
            if not isinstance(data.get("dead"), dict):
                data["dead"] = {}
            self.data = data
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A corrupt file must not create a historical flood. A fresh first
            # snapshot is treated as a silent baseline.
            self.corrupt = True
            # Keep the unreadable file for recovery, so the next save cannot overwrite it.
            try:
                self._set_corrupt_aside()
            except OSError as error:
                self.corrupt_error = str(error)

    def _set_corrupt_aside(self):
        """Move the unreadable state file to a free ``.corrupt`` name; no-op once moved."""
        if self.corrupt_backup is not None or not os.path.lexists(self.path):
            return
        backup = unused_corrupt_path(self.path)
        os.replace(self.path, backup)
        self.corrupt_backup = backup
        self.corrupt_error = None

    def _save_data(self, data):
        if self.corrupt:
            # Retried before every write; if it keeps failing, the write fails instead of overwriting.
            self._set_corrupt_aside()
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="hint-relay-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as state_file:
                json.dump(data, state_file, separators=(",", ":"))
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _commit(self, data):
        """Persist first, then expose the new state in memory."""
        self._save_data(data)
        self.data = data

    def _commit_keeping_memory(self, data):
        """Persist a failed-delivery outcome, keeping it in memory if the write fails."""
        try:
            self._commit(data)
            return True
        except Exception:
            self.data = data
            return False

    def observe_snapshot(self, key, hints):
        """Record a full SetReply snapshot and return newly pending identities."""
        if not isinstance(hints, list):
            return 0
        data = copy.deepcopy(self.data)
        key = str(key)
        previous = data["snapshots"].get(key)
        current = {}
        for raw_hint in hints:
            if not isinstance(raw_hint, dict):
                continue
            hint = dict(raw_hint)
            identity = hint_identity(hint)
            if identity.strip("|"):
                current[identity] = hint
        if previous is None:
            # The first snapshot for a key is a baseline unless it recovers a
            # durable pending delivery from an earlier process.
            data["snapshots"][key] = list(current)
            self._commit(data)
            return 0

        previously_observed = set()
        for snapshot in data["snapshots"].values():
            previously_observed.update(snapshot)
        announced = set(data["announced"])
        dead = set(data["dead"])
        added = 0
        for identity, hint in current.items():
            if identity in previously_observed or identity in announced or identity in dead:
                continue
            if identity not in data["pending"]:
                data["pending"][identity] = hint
                added += 1
        data["snapshots"][key] = list(current)
        self._commit(data)
        return added

    def enqueue_requested(self, hint):
        """Add an explicit /hint result even when it exists in a baseline."""
        if not isinstance(hint, dict):
            return False
        identity = hint_identity(hint)
        if (identity in self.data["announced"] or identity in self.data["pending"]
                or identity in self.data["dead"]):
            return False
        data = copy.deepcopy(self.data)
        data["pending"][identity] = dict(hint)
        self._commit(data)
        return True

    def pending_items(self):
        return list(self.data["pending"].items())

    def due_pending_items(self, now=None):
        """Return pending records whose retry backoff has elapsed."""
        if now is None:
            now = self.clock()
        due = []
        for identity, hint in self.data["pending"].items():
            retry = self.data["retries"].get(identity, {})
            next_attempt = retry.get("next_attempt", 0) if isinstance(retry, dict) else 0
            try:
                next_attempt = float(next_attempt)
            except (TypeError, ValueError):
                next_attempt = 0.0
            if next_attempt <= now:
                due.append((identity, hint))
        return due

    def mark_delivered(self, identity):
        """Acknowledge a successful delivery; only persisted once the write lands."""
        if identity not in self.data["pending"]:
            return False
        data = copy.deepcopy(self.data)
        data["announced"].append(identity)
        data["pending"].pop(identity, None)
        data["retries"].pop(identity, None)
        data["dead"].pop(identity, None)
        self._commit(data)
        self.sent_unacknowledged.discard(identity)
        return True

    def record_delivery_failure(self, identity, error, permanent=False, now=None):
        """Persist retry backoff or deadletter metadata for a failed delivery.

        The outcome is applied in memory even if the write fails, so this
        process does not retry the result on the very next pass. ``persisted``
        reports whether the write landed.
        """
        if identity not in self.data["pending"]:
            return None
        if now is None:
            now = self.clock()
        status = delivery_error_status(error)
        permanent = bool(permanent or status in (400, 403))
        retry = self.data["retries"].get(identity)
        if not isinstance(retry, dict):
            retry = {}
        try:
            attempts = int(retry.get("attempts", 0)) + 1
        except (TypeError, ValueError):
            attempts = 1
        data = copy.deepcopy(self.data)
        if permanent or attempts >= self.max_attempts:
            reason = "http_{}".format(status) if permanent else "retry_limit"
            data["dead"][identity] = {
                "attempts": attempts,
                "status": status,
                "reason": reason,
                "error": _error_name(error),
                "dead_at": now,
            }
            data["pending"].pop(identity, None)
            data["retries"].pop(identity, None)
            persisted = self._commit_keeping_memory(data)
            return {
                "dead": True,
                "attempts": attempts,
                "max_attempts": self.max_attempts,
                "reason": reason,
                "status": status,
                "error": _error_name(error),
                "persisted": persisted,
            }
        delay = min(
            self.retry_base_seconds * (2 ** (attempts - 1)),
            self.retry_max_seconds,
        )
        data["retries"][identity] = {
            "attempts": attempts,
            "next_attempt": now + delay,
            "status": status,
            "error": _error_name(error),
        }
        persisted = self._commit_keeping_memory(data)
        return {
            "dead": False,
            "attempts": attempts,
            "max_attempts": self.max_attempts,
            "retry_in": delay,
            "next_attempt": now + delay,
            "status": status,
            "error": _error_name(error),
            "persisted": persisted,
        }

    def deadletter_items(self):
        return list(self.data["dead"].items())

    def should_log_delivery_issue(self, key):
        try:
            return self.log_throttle.should_log(key)
        except Exception:
            return True


def _delivery_outcome_summary(identity, outcome):
    identity = _safe_log_identity(identity)
    status = outcome.get("status")
    status_text = "" if status is None else ", HTTP {}".format(status)
    if not outcome.get("persisted", True):
        status_text += "; state not saved"
    if outcome.get("dead"):
        return (
            "hint relay delivery deadlettered for {}: {} after {}/{} attempts "
            "({}{})".format(
                identity, outcome.get("reason"), outcome.get("attempts"),
                outcome.get("max_attempts"), outcome.get("error", "error"), status_text,
            )
        )
    return (
        "hint relay delivery failed for {}: attempt {}/{}, retry in {:.0f}s "
        "({}{})".format(
            identity, outcome.get("attempts"), outcome.get("max_attempts"),
            float(outcome.get("retry_in", 0)), outcome.get("error", "error"),
            status_text,
        )
    )


async def deliver_pending_hints(state, send, log=None, now=None):
    """Deliver due pending hints with per-item isolation and bounded retries.

    One bad item never prevents later items from being attempted. Permanent
    HTTP 400/403 rejections and exhausted transient retries move to the durable
    deadletter map. ``log`` receives short summaries that never include the
    hint payload or rendered message.
    """
    try:
        due_items = state.due_pending_items(now)
    except Exception as error:
        if log is not None and state.should_log_delivery_issue("delivery-state"):
            log("hint relay delivery state unavailable: " + _error_name(error))
        return 0

    delivered = 0
    for identity, hint in due_items:
        # Discord already accepted this result in this process, so only its
        # acknowledgment is retried. Sending it again would duplicate the post.
        if identity not in state.sent_unacknowledged:
            try:
                await send(hint)
            except Exception as error:
                try:
                    outcome = state.record_delivery_failure(identity, error)
                except Exception as record_error:
                    if log is not None and state.should_log_delivery_issue(identity):
                        log(
                            "hint relay delivery failure could not be recorded for {}: {}".format(
                                _safe_log_identity(identity), _error_name(record_error)
                            )
                        )
                    continue
                if log is not None and outcome is not None and state.should_log_delivery_issue(identity):
                    log(_delivery_outcome_summary(identity, outcome))
                continue
            state.sent_unacknowledged.add(identity)
        try:
            state.mark_delivered(identity)
        except Exception as error:
            # The message was accepted but the acknowledgement was not durable.
            # Keeping it pending preserves the documented at-least-once window.
            if log is not None and state.should_log_delivery_issue(identity):
                log(
                    "hint relay delivery acknowledgement failed for {}: {}".format(
                        _safe_log_identity(identity), _error_name(error)
                    )
                )
            continue
        delivered += 1
    return delivered


def format_hint_status(status, found=False):
    labels = {0: "Unspecified", 10: "No priority", 20: "Avoid", 30: "Priority", 40: "Found"}
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 40 if found else 0
    return labels.get(status, "Status {}".format(status))


def render_hint_message(template, fallback, placeholders):
    """Render a hint template by literal replacement, with a safe fallback."""
    if not isinstance(template, str) or not template.strip():
        template = fallback
    pattern = re.compile(r"\{(" + "|".join(re.escape(str(name)) for name in placeholders) + r")\}")
    rendered = pattern.sub(lambda match: str(placeholders[match.group(1)]), template)
    rendered = rendered.replace("@", "@\u200b").replace("```", "'''")
    return rendered[:MAX_DISCORD_TEXT_LENGTH]
