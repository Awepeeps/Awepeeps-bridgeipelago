"""Durable, seed-scoped item notification consent and watch preferences."""

import json
import os
import tempfile


class ItemNotificationState:
    """Store preferences by stable Discord ID; malformed state is never overwritten."""

    def __init__(self, path):
        self.path = path
        self.available = True
        self.data = {"users": {}, "delivered": []}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as source:
                    data = json.load(source)
                if (not isinstance(data, dict) or not isinstance(data.get("users"), dict)
                        or not isinstance(data.get("delivered", []), list)
                        or any(not isinstance(value, str) for value in data.get("delivered", []))):
                    raise ValueError("invalid item notification state")
                for user_id, user in data["users"].items():
                    if not str(user_id).isdigit():
                        raise ValueError("invalid item notification user ID")
                    if (not isinstance(user, dict) or user.get("mode", "off") not in ("off", "all", "watched")
                            or not isinstance(user.get("watches", []), list)
                            or any(not isinstance(watch, dict) or not isinstance(watch.get("slot"), str)
                                   or not isinstance(watch.get("item"), str) for watch in user.get("watches", []))
                            or not isinstance(user.get("registration_name", ""), str)):
                        raise ValueError("invalid item notification preferences")
                users = {}
                for user_id, user in data["users"].items():
                    users[str(user_id)] = {
                        "mode": user.get("mode", "off"),
                        "watches": user.get("watches", []),
                        "registration_name": user.get("registration_name", ""),
                        "registration_slots": user.get("registration_slots", []),
                    }
                    if not isinstance(users[str(user_id)]["registration_slots"], list) or any(
                            not isinstance(slot, str) for slot in users[str(user_id)]["registration_slots"]):
                        raise ValueError("invalid registered slots")
                self.data = {"users": users, "delivered": data.get("delivered", [])[-5000:]}
            except (OSError, ValueError, TypeError):
                self.available = False

    def _user(self, user_id):
        if not self.available or not str(user_id).isdigit():
            return None
        return self.data["users"].get(str(user_id))

    def _save(self):
        if not self.available:
            return False
        directory = os.path.dirname(os.path.abspath(self.path))
        temporary = None
        try:
            os.makedirs(directory, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix="item-notifications-", suffix=".json", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(self.data, output, indent=2, sort_keys=True)
                output.write("\n")
            os.replace(temporary, self.path)
            return True
        except OSError:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            return False

    def _change(self, action):
        if not self.available:
            return False
        previous = json.loads(json.dumps(self.data))
        try:
            action()
            if self._save():
                return True
        except OSError:
            pass
        self.data = previous
        return False

    def bind_registration(self, user_id, registration_name, registration_slots=None):
        """Bind an explicit Discord ID to its legacy registration filename."""
        if not str(user_id).isdigit() or not isinstance(registration_name, str) or not registration_name:
            return False
        def bind():
            user = self.data["users"].setdefault(str(user_id), {"mode": "off", "watches": [], "registration_name": "", "registration_slots": []})
            user["registration_name"] = registration_name
            user["registration_slots"] = ([str(slot) for slot in registration_slots]
                                           if registration_slots is not None else [])
        return self._change(bind)

    def registered_user_ids(self, slot, registrations_directory):
        """Resolve registrations by the explicitly bound legacy filename, without member-cache access."""
        if not self.available:
            return []
        wanted = str(slot).casefold()
        result = []
        for user_id, user in self.data["users"].items():
            name = user.get("registration_name", "")
            if not name:
                continue
            try:
                with open(os.path.join(registrations_directory, name + ".json"), "r", encoding="utf-8") as source:
                    slots = json.load(source)
                confirmed = user.get("registration_slots", [])
                if (isinstance(slots, list) and isinstance(confirmed, list)
                        and any(str(value).casefold() == wanted for value in slots)
                        and any(str(value).casefold() == wanted for value in confirmed)):
                    result.append(int(user_id))
            except (OSError, ValueError, TypeError):
                continue
        return result

    def set_mode(self, user_id, mode):
        if mode not in ("off", "all", "watched") or not str(user_id).isdigit():
            return False
        def update():
            user = self.data["users"].setdefault(str(user_id), {"mode": "off", "watches": [], "registration_name": "", "registration_slots": []})
            user["mode"] = mode
        return self._change(update)

    def add_watch(self, user_id, slot, item):
        if not str(user_id).isdigit():
            return False
        watch = {"slot": str(slot), "item": str(item)}
        def add():
            user = self.data["users"].setdefault(str(user_id), {"mode": "off", "watches": [], "registration_name": "", "registration_slots": []})
            if not any(self._same(watch, existing) for existing in user["watches"]):
                user["watches"].append(watch)
        return self._change(add)

    @staticmethod
    def _same(left, right):
        return (str(left.get("slot", "")).casefold(), str(left.get("item", "")).casefold()) == (
            str(right.get("slot", "")).casefold(), str(right.get("item", "")).casefold()
        )

    def remove_watch(self, user_id, slot, item):
        user = self._user(user_id)
        if user is None:
            return False
        remaining = [watch for watch in user.get("watches", [])
                     if not self._same(watch, {"slot": slot, "item": item})]
        if len(remaining) == len(user.get("watches", [])):
            return True
        return self._change(lambda: user.__setitem__("watches", remaining))

    def watches(self, user_id):
        user = self._user(user_id)
        return list(user.get("watches", [])) if user else []

    def mode(self, user_id):
        user = self._user(user_id)
        return user.get("mode", "off") if user else "off"

    def recipients(self, slot, item, registered_user_ids):
        """Return only registered users with explicit consent matching this item."""
        if not self.available:
            return []
        result = []
        for user_id in set(str(value) for value in registered_user_ids):
            user = self._user(user_id)
            if not user:
                continue
            mode = user.get("mode", "off")
            if mode == "all" or (mode == "watched" and any(
                self._same(watch, {"slot": slot, "item": item}) for watch in user.get("watches", [])
            )):
                result.append(int(user_id))
        return result

    def complete_delivery(self, event_key, slot, item, registered_user_ids):
        """Record one observed delivery and consume matching watches durably."""
        if not self.available or not event_key or self.was_delivered(event_key):
            return []
        registered = set(str(value) for value in registered_user_ids)
        recipients = self.recipients(slot, item, registered)
        def complete():
            self.data["delivered"].append(str(event_key))
            self.data["delivered"] = self.data["delivered"][-5000:]
            for user_id in registered:
                user = self.data["users"].get(user_id)
                if user:
                    user["watches"] = [watch for watch in user.get("watches", [])
                                       if not self._same(watch, {"slot": slot, "item": item})]
        return recipients if self._change(complete) else []

    def was_delivered(self, event_key):
        return bool(self.available and event_key in self.data["delivered"])

    def unregister(self, user_id):
        if not self.available:
            return False
        return self._change(lambda: self.data["users"].pop(str(user_id), None))
