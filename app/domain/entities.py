"""
app/domain/entities.py

The "nouns" of the program: small objects that carry data.
NO Gmail, NO SQLAlchemy, NO Excel in this file - pure Python.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple


# ---------------------------------------------------------------- enums
class SendStatus(str, Enum):
    SENT = "sent"
    FAILED = "failed"
    INVALID_EMAIL = "invalid_email"


class ReplyType(str, Enum):
    REPLY = "reply"            # a real answer
    BOUNCE = "bounce"          # delivery failure
    AUTO_REPLY = "auto_reply"  # out-of-office etc.


class SentScope(str, Enum):
    """Which sent mails to load from the database."""
    OPEN = "open"        # still waiting for a reply
    CLOSED = "closed"    # replied or bounced
    ALL = "all"


# ---------------------------------------------------------------- sheets
@dataclass(frozen=True)
class SheetLocation:
    """Identifies a workbook: an Excel file or a Google Spreadsheet."""
    kind: str                              # "excel" | "google_sheet"
    workbook_name: str                     # Cold_Mail.xlsx  /  Cold_Mail
    spreadsheet_id: Optional[str] = None   # Google only


@dataclass(frozen=True)
class SheetRow:
    """One data row of a sheet tab."""
    number: int                            # row number in the sheet (header = 1)
    values: Mapping[str, Any]              # {"Name": "...", "Email": "..."}

    def get(self, column: str, default=None):
        return self.values.get(column, default)

    def text(self, column: str) -> str:
        """Cell as trimmed text; empty cell -> ''."""
        value = self.values.get(column)
        return "" if value is None else str(value).strip()

    def snapshot(self) -> Dict[str, Any]:
        return dict(self.values)


@dataclass(frozen=True)
class SheetSchema:
    """Knows which columns mean what, and the simple rules about them."""
    email: str = "Email"
    cc: str = "CC"
    bcc: str = "BCC"
    status: str = "Status"
    skip_sent: bool = True

    def email_of(self, row: SheetRow) -> str:
        return row.text(self.email)

    def already_sent(self, row: SheetRow) -> bool:
        if not self.skip_sent:
            return False
        return row.text(self.status).lower() == "sent"


# ---------------------------------------------------------------- mail
@dataclass
class OutgoingMail:
    to: str
    subject: str
    html_body: str
    cc: List[str] = field(default_factory=list)
    bcc: List[str] = field(default_factory=list)
    attachments: List[str] = field(default_factory=list)   # file paths
    template_name: str = ""
    # Only for follow-ups: keep the mail inside the same Gmail conversation
    thread_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[str] = None


@dataclass(frozen=True)
class SentReceipt:
    """What the mail provider returns after a successful send."""
    message_id: Optional[str]
    thread_id: Optional[str]


@dataclass
class SendResult:
    success: bool
    message: str
    receipt: Optional[SentReceipt] = None
    attempts: int = 0
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- threads & replies
@dataclass(frozen=True)
class ThreadMessage:
    """One message of a conversation, in a provider-neutral form."""
    from_address: str
    subject: str = ""
    snippet: str = ""
    received_at: Optional[datetime] = None
    message_id: Optional[str] = None
    references: Optional[str] = None
    auto_submitted: str = ""
    is_outgoing: bool = False       # sent by me
    is_draft: bool = False


@dataclass(frozen=True)
class MailThread:
    messages: Tuple[ThreadMessage, ...] = ()


@dataclass(frozen=True)
class ReplyAnalysis:
    """Result of looking at a thread."""
    reply_type: Optional[ReplyType] = None
    replied_at: Optional[datetime] = None
    reply_from: Optional[str] = None
    snippet: Optional[str] = None
    # My latest mail in the thread (needed to answer inside the same thread)
    last_message_id: Optional[str] = None
    references: Optional[str] = None
    subject: Optional[str] = None


# ---------------------------------------------------------------- stored mails
@dataclass(frozen=True)
class SentMailRecord:
    """A mail that was sent earlier (loaded from the database)."""
    id: int
    to_email: str
    subject: Optional[str]
    thread_id: str
    message_id: Optional[str]
    sent_at: Optional[datetime]
    cc: Tuple[str, ...]
    bcc: Tuple[str, ...]
    snapshot: Mapping[str, Any]
    company: Optional[str]
    name: Optional[str]
    sheet_tab: Optional[str]
    sheet_row_number: Optional[int]
    location: SheetLocation
    run_id: int
    reply_received: bool = False
    reply_type: Optional[ReplyType] = None
    followup_count: int = 0
    last_followup_at: Optional[datetime] = None


@dataclass(frozen=True)
class MailFilter:
    """Which sent mails the user selected."""
    companies: Tuple[str, ...] = ()
    emails: Tuple[str, ...] = ()
    run_id: Optional[int] = None
    since: Optional[datetime] = None

    @property
    def is_empty(self) -> bool:
        return not (self.companies or self.emails or self.run_id or self.since)

    def describe(self) -> Dict[str, Any]:
        return {"companies": list(self.companies), "emails": list(self.emails),
                "run_id": self.run_id,
                "since": self.since.isoformat() if self.since else None}


@dataclass(frozen=True)
class FollowupDecision:
    due: bool
    number: int
    reason: str = ""


# ---------------------------------------------------------------- counters
@dataclass
class RunCounters:
    total: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0

    def add(self, other: "RunCounters") -> None:
        self.total += other.total
        self.sent += other.sent
        self.failed += other.failed
        self.skipped += other.skipped


@dataclass
class FollowupSummary:
    checked: int = 0
    replied: int = 0
    bounced: int = 0
    auto_replies: int = 0
    check_failed: int = 0
    sent: int = 0
    failed: int = 0
