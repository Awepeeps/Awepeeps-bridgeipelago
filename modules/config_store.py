"""Replace configuration files without exposing a partially written document."""

import json
import os
import tempfile


def save_config_atomic(path, data):
    """Write beside the destination, then atomically publish the complete JSON.

    Temporary files are owner-only (including when replacing a missing file).
    A serialization, flush, or replacement failure leaves the old file intact.
    """
    destination = os.path.abspath(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=os.path.dirname(destination),
            prefix=".bridgeipelago-config-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = stream.name
            json.dump(data, stream, indent=4)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
