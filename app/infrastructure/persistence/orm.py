"""
persistence/orm.py

The database tables as SQLAlchemy classes (one class = one table).
Same table and column names as before, so your existing PostgreSQL data keeps working.

    runs            one execution of main.py for one sheet tab
    contacts        one person (unique by email)
    emails          one first mail (Gmail ids, status, sheet row ...)
    logs            every event
    email_replies   reply status + follow-up counters of a mail
    followup_runs   one run of followup_main.py
    followups       one follow-up mail
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index,
    Integer, JSON, Text, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Postgres types on PostgreSQL, simple fallbacks on SQLite (used by the tests)
BigIntPK = BigInteger().with_variant(Integer, "sqlite")
JsonType = JSON().with_variant(JSONB, "postgresql")
TextArray = ARRAY(Text).with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


def _pk():
    return mapped_column(BigIntPK, Identity(always=True), primary_key=True)


def _fk(target: str, **kw):
    return mapped_column(BigIntPK, ForeignKey(target, **kw))


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[int] = _pk()
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    data_source: Mapped[str] = mapped_column(Text)
    workbook_name: Mapped[Optional[str]] = mapped_column(Text)
    spreadsheet_id: Mapped[Optional[str]] = mapped_column(Text)
    sheet_tab: Mapped[str] = mapped_column(Text)
    email_template: Mapped[str] = mapped_column(Text)

    total_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (CheckConstraint("data_source IN ('excel','google_sheet')", name="ck_runs_source"),)


class ContactRow(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = _pk()
    email: Mapped[str] = mapped_column(Text)
    name: Mapped[Optional[str]] = mapped_column(Text)
    company: Mapped[Optional[str]] = mapped_column(Text)
    role: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # lower(): "A@x.com" and "a@x.com" are the same person
    __table_args__ = (Index("ux_contacts_email", func.lower(email), unique=True),)


class EmailRow(Base):
    __tablename__ = "emails"

    id: Mapped[int] = _pk()
    run_id: Mapped[int] = _fk("runs.id", ondelete="CASCADE")
    contact_id: Mapped[Optional[int]] = _fk("contacts.id")      # NULL if the email cell was blank

    gmail_message_id: Mapped[Optional[str]] = mapped_column(Text)
    gmail_thread_id: Mapped[Optional[str]] = mapped_column(Text)

    to_email: Mapped[str] = mapped_column(Text)
    cc_emails: Mapped[Optional[list]] = mapped_column(TextArray)
    bcc_emails: Mapped[Optional[list]] = mapped_column(TextArray)
    subject: Mapped[Optional[str]] = mapped_column(Text)
    email_template: Mapped[Optional[str]] = mapped_column(Text)
    attachments: Mapped[Optional[list]] = mapped_column(TextArray)

    status: Mapped[str] = mapped_column(Text)                   # sent | failed | invalid_email
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sheet_tab: Mapped[Optional[str]] = mapped_column(Text)
    sheet_row_number: Mapped[Optional[int]] = mapped_column(Integer)
    row_snapshot: Mapped[Optional[dict]] = mapped_column(JsonType)

    __table_args__ = (
        CheckConstraint("status IN ('sent','failed','invalid_email')", name="ck_emails_status"),
        Index("ix_emails_run", "run_id"),
        Index("ix_emails_contact", "contact_id"),
        Index("ix_emails_thread", "gmail_thread_id"),
        Index("ux_emails_gmail_msg", "gmail_message_id", unique=True,
              postgresql_where=(gmail_message_id.isnot(None)),
              sqlite_where=(gmail_message_id.isnot(None))),
    )


class LogRow(Base):
    __tablename__ = "logs"

    id: Mapped[int] = _pk()
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    level: Mapped[str] = mapped_column(Text, default="INFO")
    event: Mapped[str] = mapped_column(Text)
    message: Mapped[Optional[str]] = mapped_column(Text)

    run_id: Mapped[Optional[int]] = _fk("runs.id", ondelete="CASCADE")
    email_id: Mapped[Optional[int]] = _fk("emails.id", ondelete="SET NULL")

    name: Mapped[Optional[str]] = mapped_column(Text)
    to_email: Mapped[Optional[str]] = mapped_column(Text)
    details: Mapped[Optional[dict]] = mapped_column(JsonType)

    __table_args__ = (
        CheckConstraint("level IN ('DEBUG','INFO','WARNING','ERROR')", name="ck_logs_level"),
        Index("ix_logs_run", "run_id"),
        Index("ix_logs_email", "email_id"),
        Index("ix_logs_time", "logged_at"),
    )


class EmailReplyRow(Base):
    __tablename__ = "email_replies"

    email_id: Mapped[int] = mapped_column(BigIntPK, ForeignKey("emails.id", ondelete="CASCADE"),
                                          primary_key=True)
    reply_received: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    reply_type: Mapped[Optional[str]] = mapped_column(Text)              # reply | auto_reply | bounce
    replied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reply_from: Mapped[Optional[str]] = mapped_column(Text)
    reply_snippet: Mapped[Optional[str]] = mapped_column(Text)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    followup_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_followup_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FollowupRunRow(Base):
    __tablename__ = "followup_runs"

    id: Mapped[int] = _pk()
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str] = mapped_column(Text)                              # normal | check_only | sync_sheet
    filters: Mapped[Optional[dict]] = mapped_column(JsonType)
    checked_count: Mapped[int] = mapped_column(Integer, default=0)
    replied_count: Mapped[int] = mapped_column(Integer, default=0)
    bounced_count: Mapped[int] = mapped_column(Integer, default=0)
    followups_sent: Mapped[int] = mapped_column(Integer, default=0)
    followups_failed: Mapped[int] = mapped_column(Integer, default=0)


class FollowupRow(Base):
    __tablename__ = "followups"

    id: Mapped[int] = _pk()
    email_id: Mapped[int] = _fk("emails.id", ondelete="CASCADE")
    followup_run_id: Mapped[Optional[int]] = _fk("followup_runs.id", ondelete="SET NULL")

    followup_number: Mapped[int] = mapped_column(Integer)
    to_email: Mapped[str] = mapped_column(Text)
    subject: Mapped[Optional[str]] = mapped_column(Text)
    template: Mapped[Optional[str]] = mapped_column(Text)

    gmail_message_id: Mapped[Optional[str]] = mapped_column(Text)
    gmail_thread_id: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(Text)                            # sent | failed
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('sent','failed')", name="ck_followups_status"),
        Index("ix_followups_email", "email_id"),
        Index("ix_followups_thread", "gmail_thread_id"),
    )
