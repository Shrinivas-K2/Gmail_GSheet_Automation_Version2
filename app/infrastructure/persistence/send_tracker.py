"""
persistence/send_tracker.py

Stores what happens while sending the first mails.

    SqlSendTracker   writes to PostgreSQL
    NullSendTracker  does nothing (used when the database is switched off)
"""

import json
from typing import Optional

from sqlalchemy import func, select

from app.domain.entities import (
    OutgoingMail, RunCounters, SendResult, SendStatus, SheetLocation, SheetRow,
)
from app.domain.interfaces import Clock, SendTracker
from app.infrastructure.persistence.database import Database
from app.infrastructure.persistence.orm import ContactRow, EmailRow, LogRow, RunRow


def _clean(value) -> Optional[str]:
    """Empty cell / whitespace -> None, everything else -> trimmed text."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class NullSendTracker(SendTracker):
    """Null Object pattern: same interface, no effect. No 'if db_enabled' anywhere else."""

    def start_run(self, location, tab, template, total):
        return None

    def record(self, run_id, tab, row, email, status, mail=None, result=None, error=None):
        pass

    def log_event(self, event, message="", level="INFO", run_id=None, name=None, to_email=None):
        pass

    def finish_run(self, run_id, counters):
        pass


class SqlSendTracker(SendTracker):

    def __init__(self, database: Database, clock: Clock):
        self._db = database
        self._clock = clock

    def start_run(self, location: SheetLocation, tab: str, template: str, total: int) -> Optional[int]:

        run = RunRow(
            data_source=location.kind,
            workbook_name=location.workbook_name,
            spreadsheet_id=location.spreadsheet_id,
            sheet_tab=tab,
            email_template=template,
            total_count=total,
        )

        with self._db.session() as s:
            s.add(run)
            s.flush()                                   # gives run.id without committing yet
            s.add(LogRow(run_id=run.id, event="RUN_STARTED", message=f"{tab}: {total} records"))
            s.commit()
            return run.id

    def record(self, run_id: Optional[int], tab: str, row: SheetRow, email: str,
               status: SendStatus, mail: Optional[OutgoingMail] = None,
               result: Optional[SendResult] = None, error: Optional[str] = None) -> None:

        if run_id is None:
            return

        receipt = result.receipt if (result and status == SendStatus.SENT) else None
        attempts = (result.attempts if result else 0) or 1

        with self._db.session() as s:

            contact_id = self._upsert_contact(s, row, email)

            record = EmailRow(
                run_id=run_id,
                contact_id=contact_id,
                gmail_message_id=receipt.message_id if receipt else None,
                gmail_thread_id=receipt.thread_id if receipt else None,
                to_email=email,
                cc_emails=(mail.cc if mail else None) or None,
                bcc_emails=(mail.bcc if mail else None) or None,
                subject=mail.subject if mail else None,
                email_template=mail.template_name if mail else None,
                attachments=([a.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for a in mail.attachments]
                             if mail else None) or None,
                status=status.value,
                error_message=error,
                attempts=attempts,
                sent_at=self._clock.now() if status == SendStatus.SENT else None,
                sheet_tab=tab,
                sheet_row_number=row.number,
                # default=str turns dates etc. into text so JSON never fails
                row_snapshot=json.loads(json.dumps(row.snapshot(), default=str)),
            )
            s.add(record)
            s.flush()

            s.add(LogRow(
                run_id=run_id,
                email_id=record.id,
                level="INFO" if status == SendStatus.SENT else "ERROR",
                event={"sent": "SENT", "failed": "FAILED", "invalid_email": "INVALID_EMAIL"}[status.value],
                message=error,
                name=_clean(row.get("Name")),
                to_email=email,
                details=({"attempts": attempts, "errors": result.errors}
                         if result and attempts > 1 else None),
            ))
            s.commit()

    @staticmethod
    def _upsert_contact(s, row: SheetRow, email: str) -> Optional[int]:
        """Find the person by e-mail (case-insensitive) or create them."""

        if not email:
            return None

        contact = s.scalar(select(ContactRow).where(func.lower(ContactRow.email) == email.lower()))

        name, company, role = _clean(row.get("Name")), _clean(row.get("Company")), _clean(row.get("Role"))

        if contact is None:
            contact = ContactRow(email=email, name=name, company=company, role=role)
            s.add(contact)
            s.flush()
        else:
            # keep the newest non-empty values
            contact.name = name or contact.name
            contact.company = company or contact.company
            contact.role = role or contact.role

        return contact.id

    def log_event(self, event: str, message: str = "", level: str = "INFO",
                  run_id: Optional[int] = None, name: Optional[str] = None,
                  to_email: Optional[str] = None) -> None:

        with self._db.session() as s:
            s.add(LogRow(run_id=run_id, event=event, message=message, level=level,
                         name=_clean(name), to_email=to_email))
            s.commit()

    def finish_run(self, run_id: Optional[int], counters: RunCounters) -> None:

        if run_id is None:
            return

        with self._db.session() as s:
            run = s.get(RunRow, run_id)
            run.finished_at = self._clock.now()
            run.total_count = counters.total
            run.sent_count = counters.sent
            run.failed_count = counters.failed
            run.skipped_count = counters.skipped
            s.add(LogRow(run_id=run_id, event="RUN_FINISHED",
                         message=f"sent={counters.sent} failed={counters.failed} skipped={counters.skipped}"))
            s.commit()
