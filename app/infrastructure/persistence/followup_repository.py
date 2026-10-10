"""
persistence/followup_repository.py

Reads sent mails and stores reply / follow-up results (Repository pattern:
the rest of the program sees plain objects, never SQL).
"""

from typing import List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.exc import OperationalError

from app.domain.entities import (
    MailFilter, ReplyAnalysis, ReplyType, SendResult, SentMailRecord, SentScope, SheetLocation,
)
from app.domain.errors import DatabaseUnavailableError
from app.domain.interfaces import Clock, FollowupRepository
from app.infrastructure.persistence.database import Database, aware
from app.infrastructure.persistence.orm import (
    ContactRow, EmailReplyRow, EmailRow, FollowupRow, FollowupRunRow, LogRow, RunRow,
)


class SqlFollowupRepository(FollowupRepository):

    def __init__(self, database: Database, clock: Clock):
        self._db = database
        self._clock = clock

    def init(self) -> None:
        try:
            self._db.create_schema()
        except OperationalError as e:
            raise DatabaseUnavailableError(str(e).splitlines()[0]) from e

    # ------------------------------------------------------------ read
    def fetch_sent(self, mail_filter: MailFilter, scope: SentScope) -> List[SentMailRecord]:

        stmt = (
            select(EmailRow, ContactRow, RunRow, EmailReplyRow)
            .join(RunRow, EmailRow.run_id == RunRow.id)
            .outerjoin(ContactRow, EmailRow.contact_id == ContactRow.id)
            .outerjoin(EmailReplyRow, EmailReplyRow.email_id == EmailRow.id)
            .where(EmailRow.status == "sent", EmailRow.gmail_thread_id.isnot(None))
            .order_by(EmailRow.id)
        )

        if scope == SentScope.OPEN:
            stmt = stmt.where(
                or_(EmailReplyRow.reply_received.is_(None), EmailReplyRow.reply_received.is_(False)),
                or_(EmailReplyRow.reply_type.is_(None), EmailReplyRow.reply_type != "bounce"),
            )
        elif scope == SentScope.CLOSED:
            stmt = stmt.where(or_(EmailReplyRow.reply_received.is_(True),
                                  EmailReplyRow.reply_type == "bounce"))

        # ---- filters chosen by the user
        companies = [c.lower() for c in mail_filter.companies]
        if companies:
            # "Company" column OR the sheet tab name (tabs are named after companies)
            stmt = stmt.where(or_(func.lower(ContactRow.company).in_(companies),
                                  func.lower(EmailRow.sheet_tab).in_(companies)))

        emails = [e.lower() for e in mail_filter.emails]
        if emails:
            stmt = stmt.where(func.lower(EmailRow.to_email).in_(emails))

        if mail_filter.run_id:
            stmt = stmt.where(EmailRow.run_id == mail_filter.run_id)

        if mail_filter.since:
            stmt = stmt.where(EmailRow.sent_at >= mail_filter.since)

        with self._db.session() as s:
            return [self._to_record(e, c, r, er) for e, c, r, er in s.execute(stmt).all()]

    @staticmethod
    def _to_record(email, contact, run, reply) -> SentMailRecord:
        return SentMailRecord(
            id=email.id,
            to_email=email.to_email,
            subject=email.subject,
            thread_id=email.gmail_thread_id,
            message_id=email.gmail_message_id,
            sent_at=aware(email.sent_at),
            cc=tuple(email.cc_emails or ()),
            bcc=tuple(email.bcc_emails or ()),
            snapshot=dict(email.row_snapshot or {}),
            company=contact.company if contact else None,
            name=contact.name if contact else None,
            sheet_tab=email.sheet_tab or run.sheet_tab,
            sheet_row_number=email.sheet_row_number,
            location=SheetLocation(run.data_source, run.workbook_name, run.spreadsheet_id),
            run_id=run.id,
            # no email_replies row yet = never checked = no reply, 0 follow-ups
            reply_received=bool(reply.reply_received) if reply else False,
            reply_type=ReplyType(reply.reply_type) if reply and reply.reply_type else None,
            followup_count=(reply.followup_count or 0) if reply else 0,
            last_followup_at=aware(reply.last_followup_at) if reply else None,
        )

    # ------------------------------------------------------------ write: replies
    @staticmethod
    def _reply_row(s, email_id: int) -> EmailReplyRow:
        row = s.get(EmailReplyRow, email_id)
        if row is None:
            row = EmailReplyRow(email_id=email_id, reply_received=False, followup_count=0)
            s.add(row)
        return row

    def save_check(self, record: SentMailRecord, analysis: ReplyAnalysis) -> None:

        kind = analysis.reply_type

        with self._db.session() as s:
            row = self._reply_row(s, record.id)
            row.last_checked_at = self._clock.now()

            if kind is not None:
                row.reply_type = kind.value
                row.replied_at = analysis.replied_at
                row.reply_from = analysis.reply_from
                row.reply_snippet = analysis.snippet

            if kind == ReplyType.REPLY:
                row.reply_received = True

            if kind in (ReplyType.REPLY, ReplyType.BOUNCE):
                s.add(LogRow(
                    email_id=record.id,
                    event="REPLY_FOUND" if kind == ReplyType.REPLY else "BOUNCE_FOUND",
                    level="INFO" if kind == ReplyType.REPLY else "WARNING",
                    message=analysis.snippet,
                    name=record.name, to_email=record.to_email,
                    details={"reply_from": analysis.reply_from},
                ))

            s.commit()

    # ------------------------------------------------------------ write: runs / follow-ups
    def start_run(self, mode: str, mail_filter: MailFilter) -> int:
        with self._db.session() as s:
            run = FollowupRunRow(mode=mode, filters=mail_filter.describe())
            s.add(run)
            s.commit()
            return run.id

    def finish_run(self, run_id: Optional[int], checked: int, replied: int,
                   bounced: int, sent: int, failed: int) -> None:

        if run_id is None:
            return

        with self._db.session() as s:
            run = s.get(FollowupRunRow, run_id)
            run.finished_at = self._clock.now()
            run.checked_count = checked
            run.replied_count = replied
            run.bounced_count = bounced
            run.followups_sent = sent
            run.followups_failed = failed
            s.commit()

    def save_followup(self, record: SentMailRecord, run_id: Optional[int], number: int,
                      template: str, subject: Optional[str], result: SendResult) -> None:

        receipt = result.receipt if result.success else None

        with self._db.session() as s:

            s.add(FollowupRow(
                email_id=record.id,
                followup_run_id=run_id,
                followup_number=number,
                to_email=record.to_email,
                subject=subject,
                template=template,
                gmail_message_id=receipt.message_id if receipt else None,
                gmail_thread_id=receipt.thread_id if receipt else None,
                status="sent" if result.success else "failed",
                error_message=None if result.success else result.message,
                attempts=result.attempts or 1,
                sent_at=self._clock.now() if result.success else None,
            ))

            if result.success:
                row = self._reply_row(s, record.id)
                row.followup_count = (row.followup_count or 0) + 1
                row.last_followup_at = self._clock.now()

            s.add(LogRow(
                email_id=record.id,
                event="FOLLOWUP_SENT" if result.success else "FOLLOWUP_FAILED",
                level="INFO" if result.success else "ERROR",
                message=None if result.success else result.message,
                name=record.name, to_email=record.to_email,
                details={"followup_number": number, "followup_run_id": run_id,
                         "attempts": result.attempts,
                         "errors": result.errors if result.attempts > 1 else None},
            ))
            s.commit()
