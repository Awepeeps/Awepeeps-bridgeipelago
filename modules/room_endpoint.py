"""Resolve hosted ports and check RoomInfo before sending credentials."""

from urllib.parse import urlsplit, urlunsplit


def expected_seed(config):
    value = config.get("ExpectedSeedName")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Set ArchipelagoConfig.ExpectedSeedName to the intended seed name")
    return value.strip()


def verify_seed(config, room_info):
    if room_info.get("seed_name") != expected_seed(config):
        raise ValueError("Server seed identity does not match ExpectedSeedName; login refused")


def resolve_port(config, self_host, get):
    expected_seed(config)
    if self_host:
        value = config["ArchipelagoPort"]
    else:
        url = urlsplit(config["ArchipelagoServerURL"])
        parts = url.path.strip("/").split("/")
        if url.scheme not in ("http", "https") or not url.netloc or len(parts) != 2 or parts[0] != "room":
            raise ValueError("ArchipelagoServerURL must be a room URL")
        api = urlunsplit((url.scheme, url.netloc, "/api/room_status/" + parts[1], "", ""))
        response = get(api, timeout=10, allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Room status did not return HTTP 200")
        value = response.json()["last_port"]
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Room port must be an integer")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("Room port is outside 1-65535")
    return port
