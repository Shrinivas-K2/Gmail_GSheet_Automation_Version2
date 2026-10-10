"""
app/domain/interfaces.py

The CONTRACTS (abstractions) the services depend on.

Dependency rule:   services  ->  these interfaces  <-  infrastructure
The services never import Gmail, Excel, SQLAlchemy ... they only know these
small interfaces. Anything that fulfils a contract can be plugged in:
a real Gmail gateway, or a fake one in a test.

Interfaces are kept SMALL on purpose (Interface Segregation): a class that
only needs to send a mail is not forced to know about reading threads.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional

from app.domain.entities import (
    FollowupDecision, MailFilter, MailThread, OutgoingMail, ReplyAnalysis,
    RunCounters, SendResult, SendStatus, SentMailRecord, SentReceipt,
    SentScope, SheetLocation, SheetRow,
)


# =========================================================== mail provider
class Connectable(ABC):
    @abstractmethod
    def connect(self) -> None:
        """Log in (may open the browser the first time)."""


class MailSender(ABC):
    @abstractmethod
    def send(self, mail: OutgoingMail) -> SentReceipt:
        """Send one mail. Raises an exception when it fails."""


class ThreadReader(ABC):
    @abstractmethod
    def fetch_thread(self, thread_id: str) -> MailThread: ...

    @abstractmethod
    def my_address(self) -> str: ...


# =========================================================== sheets
class RowReader(ABC):
    @abstractmethod
    def open_tab(self, tab: str) -> None: ...

    @abstractmethod
    def read_rows(self) -> List[SheetRow]: ...


class CellWriter(ABC):
    @abstractmethod
    def write_cell(self, row_number: int, column: str, value) -> None:
        """Write a cell; the column is created at the end of the header if missing."""

    @abstractmethod
    def save(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class SheetGateway(RowReader, CellWriter):
    """Excel file or Google Spreadsheet - same behaviour for the services."""

    @property
    @abstractmethod
    def location(self) -> SheetLocation: ...

    @abstractmethod
    def describe(self) -> str:
        """Human readable place, e.g. the full Excel path."""


class SheetGatewayFactory(ABC):
    @abstractmethod
    def create(self, location: SheetLocation) -> SheetGateway: ...

    @abstractmethod
    def default_location(self, kind: Optional[str] = None,
                         workbook: Optional[str] = None) -> SheetLocation: ...


# =========================================================== templates / logs / UI
class Template:
    def __init__(self, subject: str, body: str):
        self.subject = subject
        self.body = body


class TemplateRepository(ABC):
    @abstractmethod
    def load(self, name: str) -> Template: ...

    @abstractmethod
    def exists(self, name: str) -> bool: ...


class ActivityLog(ABC):
    """The human readable text log (logs/mail_log.txt)."""

    @abstractmethod
    def write(self, name: str, email: str, status: str, message: str = "") -> None: ...


class Reporter(ABC):
    """Everything the program wants to tell the user."""

    @abstractmethod
    def info(self, text: str = "") -> None: ...

    @abstractmethod
    def warn(self, text: str) -> None: ...

    @abstractmethod
    def error(self, text: str) -> None: ...


class Prompter(ABC):
    """Questions asked to the user (kept out of the services)."""

    @abstractmethod
    def choose_scope(self, table: Dict[str, Dict[str, int]]) -> List[str]:
        """Show sent/replied counts per company; return chosen names ([] = all)."""

    @abstractmethod
    def confirm(self, question: str) -> bool: ...


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...

    @abstractmethod
    def sleep(self, seconds: float) -> None: ...


# =========================================================== tracking (database)
class SendTracker(ABC):
    """Stores what happened while sending the first mails."""

    @abstractmethod
    def start_run(self, location: SheetLocation, tab: str, template: str,
                  total: int) -> Optional[int]: ...

    @abstractmethod
    def record(self, run_id: Optional[int], tab: str, row: SheetRow, email: str,
               status: SendStatus, mail: Optional[OutgoingMail] = None,
               result: Optional[SendResult] = None, error: Optional[str] = None) -> None: ...

    @abstractmethod
    def log_event(self, event: str, message: str = "", level: str = "INFO",
                  run_id: Optional[int] = None, name: Optional[str] = None,
                  to_email: Optional[str] = None) -> None: ...

    @abstractmethod
    def finish_run(self, run_id: Optional[int], counters: RunCounters) -> None: ...


class FollowupRepository(ABC):
    """Reads sent mails and stores reply / follow-up results."""

    @abstractmethod
    def init(self) -> None: ...

    @abstractmethod
    def fetch_sent(self, mail_filter: MailFilter, scope: SentScope) -> List[SentMailRecord]: ...

    @abstractmethod
    def save_check(self, record: SentMailRecord, analysis: ReplyAnalysis) -> None: ...

    @abstractmethod
    def start_run(self, mode: str, mail_filter: MailFilter) -> int: ...

    @abstractmethod
    def finish_run(self, run_id: Optional[int], checked: int, replied: int,
                   bounced: int, sent: int, failed: int) -> None: ...

    @abstractmethod
    def save_followup(self, record: SentMailRecord, run_id: Optional[int], number: int,
                      template: str, subject: Optional[str], result: SendResult) -> None: ...


# =========================================================== business rules
class FollowupPolicy(ABC):
    @abstractmethod
    def evaluate(self, record: SentMailRecord, now: datetime) -> FollowupDecision:
        """Is the next follow-up due for this mail?"""


class ReplyRule(ABC):
    """One way to recognise a kind of incoming message (Strategy pattern)."""

    @abstractmethod
    def matches(self, message) -> bool: ...
