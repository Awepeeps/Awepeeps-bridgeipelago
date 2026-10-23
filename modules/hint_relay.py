"""Protocol and durable delivery state for Archipelago hint result relays."""

import json
import os
import re
import tempfile


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


class HintRelayState:
    """Durable snapshots, dedupe identities, and retryable result outbox."""

    def __init__(self, path):
        self.path = path
        self.data = {"version": 1, "snapshots": {}, "announced": [], "pending": {}}
        self.corrupt = False
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as state_file:
                data = json.load(state_file)
            if (not isinstance(data, dict) or data.get("version") != 1
                    or not isinstance(data.get("snapshots"), dict)
                    or not isinstance(data.get("announced"), list)
                    or not isinstance(data.get("pending"), dict)):
                raise ValueError("invalid hint relay state shape")
            self.data = data
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A corrupt file must not create a historical flood. A fresh first
            # snapshot is treated as a silent baseline.
            self.corrupt = True

    def _save(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="hint-relay-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as state_file:
                json.dump(self.data, state_file, separators=(",", ":"))
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def observe_snapshot(self, key, hints):
        """Record a full SetReply snapshot and return newly pending identities."""
        if not isinstance(hints, list):
            return 0
        previous = self.data["snapshots"].get(str(key))
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
            self.data["snapshots"][str(key)] = list(current)
            self._save()
            return 0

        previously_observed = set()
        for snapshot in self.data["snapshots"].values():
            previously_observed.update(snapshot)
        announced = set(self.data["announced"])
        added = 0
        for identity, hint in current.items():
            if identity in previously_observed or identity in announced:
                continue
            if identity not in self.data["pending"]:
                self.data["pending"][identity] = hint
                added += 1
        self.data["snapshots"][str(key)] = list(current)
        self._save()
        return added

    def enqueue_requested(self, hint):
        """Add an explicit /hint result even when it exists in a baseline."""
        if not isinstance(hint, dict):
            return False
        identity = hint_identity(hint)
        if identity in self.data["announced"] or identity in self.data["pending"]:
            return False
        self.data["pending"][identity] = dict(hint)
        self._save()
        return True

    def deliver_pending(self, deliver):
        """Deliver pending records; persist the acknowledgement only on success."""
        sent = 0
        for identity, hint in self.pending_items():
            deliver(hint)
            self.mark_delivered(identity)
            sent += 1
        return sent

    def pending_items(self):
        return list(self.data["pending"].items())

    def mark_delivered(self, identity):
        if identity in self.data["pending"]:
            self.data["announced"].append(identity)
            self.data["pending"].pop(identity, None)
            self._save()


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
    return rendered.replace("@", "@\u200b").replace("```", "'''")
