"""
tests/fakes.py

Small in-memory stand-ins for the real adapters. Because the services only
depend on INTERFACES, a test can plug these in - no Gmail, Excel or database.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.domain.entities import (
    MailThread, OutgoingMail, SentReceipt, SheetLocation, SheetRow,
)
from app.domain.interfaces import (
    ActivityLog, Clock, Connectable, MailSender, Prompter, Reporter, SheetGateway,
    SheetGatewayFactory, Template, TemplateRepository, ThreadReader,
)


class FakeReporter(Reporter):
    def __init__(self):
        self.lines: List[str] = []

    def info(self, text=""): self.lines.append(text)
    def warn(self, text): self.lines.append(text)
    def error(self, text): self.lines.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class FakeClock(Clock):
    def __init__(self, now: Optional[datetime] = None):
        self._now = now or datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
        self.slept: List[float] = []

    def now(self): return self._now
    def sleep(self, seconds): self.slept.append(seconds)


class FakeActivityLog(ActivityLog):
    def __init__(self): self.entries = []
    def write(self, name, email, status, message=""): self.entries.append((name, email, status, message))


class FakeTemplates(TemplateRepository):
    def __init__(self, templates: Dict[str, Template]): self._t = templates
    def load(self, name): return self._t[name]
    def exists(self, name): return name in self._t


class FakePrompter(Prompter):
    def __init__(self, scope=None, confirm=True):
        self.scope, self._confirm, self.asked_confirm = scope or [], confirm, 0

    def choose_scope(self, table): return self.scope

    def confirm(self, question):
        self.asked_confirm += 1
        return self._confirm


class FakeMailGateway(Connectable, MailSender, ThreadReader):
    """Records every mail; `fail_for` addresses raise; `threads` feed the reply checker."""

    def __init__(self, me="me@gmail.com", threads: Optional[Dict[str, MailThread]] = None,
                 fail_for=()):
        self.me, self.threads, self.fail_for = me, threads or {}, set(fail_for)
        self.sent: List[OutgoingMail] = []
        self.fetched: List[str] = []
        self.connected = False
        self._n = 0

    def connect(self): self.connected = True

    def send(self, mail):
        if mail.to in self.fail_for:
            raise Exception("Fake Gmail 500")
        self._n += 1
        self.sent.append(mail)
        return SentReceipt(f"msg{self._n}", mail.thread_id or f"thr{self._n}")

    def my_address(self): return self.me
    def fetch_thread(self, thread_id):
        self.fetched.append(thread_id)
        return self.threads[thread_id]


class FakeSheetGateway(SheetGateway):
    """A workbook held in memory: {tab: {"headers": [...], "rows": [[...], ...]}}"""

    def __init__(self, book, location):
        self._book, self._location, self._tab = book, location, None
        self.saved = 0
        self.closed = False

    @property
    def location(self): return self._location
    def describe(self): return f"fake:{self._location.workbook_name}"

    def open_tab(self, tab):
        if tab not in self._book:
            raise Exception(f"Sheet '{tab}' not found.")
        self._tab = tab

    def read_rows(self):
        data = self._book[self._tab]
        out = []
        for i, cells in enumerate(data["rows"], start=2):
            values = {h: (cells[c] if c < len(cells) else None) for c, h in enumerate(data["headers"])}
            if all(v is None for v in values.values()):
                continue
            out.append(SheetRow(i, values))
        return out

    def write_cell(self, row_number, column, value):
        data = self._book[self._tab]
        if column not in data["headers"]:
            data["headers"].append(column)
        col = data["headers"].index(column)
        row = data["rows"][row_number - 2]
        row.extend([None] * (col + 1 - len(row)))
        row[col] = value

    def save(self): self.saved += 1
    def close(self): self.closed = True


class FakeSheetFactory(SheetGatewayFactory):
    def __init__(self, book, kind="excel", name="Cold_Mail.xlsx"):
        self.book, self.kind, self.name = book, kind, name
        self.created: List[FakeSheetGateway] = []

    def create(self, location):
        gateway = FakeSheetGateway(self.book, location)
        self.created.append(gateway)
        return gateway

    def default_location(self, kind=None, workbook=None):
        return SheetLocation(kind or self.kind, workbook or self.name)

    def cell(self, tab, row_number, column):
        data = self.book[tab]
        if column not in data["headers"]:
            return None
        row = data["rows"][row_number - 2]
        index = data["headers"].index(column)
        return row[index] if index < len(row) else None
