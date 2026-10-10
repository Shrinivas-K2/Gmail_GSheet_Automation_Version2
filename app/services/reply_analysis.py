"""
reply_analysis.py

Looks at a conversation and decides: did somebody reply?

Each way of recognising a message is a small class (a RULE).
To recognise something new (for example "unsubscribe"), add a new rule class
and register it - nothing that exists has to change (Open/Closed principle).

  reply       a real answer                      -> follow-ups stop
  bounce      the address does not work          -> follow-ups stop
  auto_reply  out-of-office / automatic reply    -> follow-ups continue
"""

from typing import List, Optional, Sequence, Tuple

from app.domain.entities import MailThread, ReplyAnalysis, ReplyType, ThreadMessage
from app.domain.interfaces import ReplyRule


class BounceRule(ReplyRule):
    def matches(self, message: ThreadMessage) -> bool:
        sender = message.from_address.lower()
        return "mailer-daemon" in sender or sender.startswith("postmaster@")


class AutoReplyRule(ReplyRule):

    _SUBJECT_PREFIXES = ("automatic reply", "out of office", "autoreply", "auto-reply")

    def matches(self, message: ThreadMessage) -> bool:
        auto = message.auto_submitted.strip().lower()

        if auto not in ("", "no"):
            return True

        return message.subject.lower().startswith(self._SUBJECT_PREFIXES)


class ThreadAnalyzer:

    # A real reply beats a bounce, a bounce beats an auto-reply
    PRIORITY = (ReplyType.REPLY, ReplyType.BOUNCE, ReplyType.AUTO_REPLY)

    def __init__(self, rules: Optional[Sequence[Tuple[ReplyType, ReplyRule]]] = None):
        # Checked in this order. A message matching no rule is a normal REPLY.
        self._rules = list(rules) if rules is not None else [
            (ReplyType.BOUNCE, BounceRule()),
            (ReplyType.AUTO_REPLY, AutoReplyRule()),
        ]

    def classify(self, message: ThreadMessage) -> ReplyType:
        for reply_type, rule in self._rules:
            if rule.matches(message):
                return reply_type
        return ReplyType.REPLY

    def analyze(self, thread: MailThread, my_address: str) -> ReplyAnalysis:

        mine = my_address.lower()
        incoming: List[Tuple[ReplyType, ThreadMessage]] = []
        last_mine: Optional[ThreadMessage] = None

        for message in thread.messages:

            if message.is_draft:
                continue

            # My own mail (also covers the BCC copy I receive of my own mail)
            if message.is_outgoing or message.from_address.lower() == mine:
                last_mine = message
                continue

            incoming.append((self.classify(message), message))

        chosen = None
        for wanted in self.PRIORITY:
            chosen = next(((t, m) for t, m in incoming if t == wanted), None)
            if chosen:
                break

        reply_type = replied_at = reply_from = snippet = None

        if chosen:
            reply_type, message = chosen
            replied_at = message.received_at
            reply_from = message.from_address or None
            snippet = (message.snippet or "")[:300] or None

        return ReplyAnalysis(
            reply_type=reply_type,
            replied_at=replied_at,
            reply_from=reply_from,
            snippet=snippet,
            last_message_id=last_mine.message_id if last_mine else None,
            references=last_mine.references if last_mine else None,
            subject=last_mine.subject if last_mine else None,
        )
