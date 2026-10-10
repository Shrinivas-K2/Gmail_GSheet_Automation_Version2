"""
followup_policy.py

WHEN is a follow-up due?  Kept in its own class so the rule can be replaced
(for example "only on weekdays") without touching the service.
"""

from datetime import datetime, timedelta
from typing import Sequence

from app.domain.entities import FollowupDecision, SentMailRecord
from app.domain.interfaces import FollowupPolicy


class WaitDaysPolicy(FollowupPolicy):
    """
    wait_days = (3, 5):  follow-up 1 after 3 days, follow-up 2 after 5 more days,
                         then stop.   (0, 0) = no waiting.
    """

    def __init__(self, wait_days: Sequence[int]):
        self._waits = list(wait_days)

    def evaluate(self, record: SentMailRecord, now: datetime) -> FollowupDecision:

        number = record.followup_count + 1

        if number > len(self._waits):
            return FollowupDecision(False, number, "max follow-ups reached")

        baseline = record.last_followup_at or record.sent_at

        if baseline is None:
            return FollowupDecision(False, number, "no send date")

        due_on = baseline + timedelta(days=self._waits[number - 1])

        if now < due_on:
            return FollowupDecision(False, number, f"due on {due_on:%d %b %Y}")

        return FollowupDecision(True, number)
