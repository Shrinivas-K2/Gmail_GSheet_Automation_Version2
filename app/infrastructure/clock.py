"""
clock.py

Time and sleeping behind an interface, so tests can run instantly.
"""

import time
from datetime import datetime, timezone

from app.domain.interfaces import Clock


class SystemClock(Clock):

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)
