"""Bound tracker retries without permanently disabling recovery."""


class TrackerRecovery:
    def __init__(self):
        self.failures = 0
        self.alerted = False
        self.retry_after = 0

    def connected(self):
        self.failures = 0
        self.alerted = False
        self.retry_after = 0

    def failed(self, now):
        self.failures += 1
        if self.failures < 3:
            return False
        self.retry_after = now + 60
        first_alert = not self.alerted
        self.alerted = True
        return first_alert

    def ready(self, now):
        return now >= self.retry_after
