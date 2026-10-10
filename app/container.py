"""
app/container.py   -   the COMPOSITION ROOT

The ONE place where concrete classes are chosen and connected together.
Everywhere else the program only knows interfaces.

    Container(settings).campaign_service()    -> sends the first mails
    Container(settings).followup_service()    -> checks replies + follow-ups

Want to change something?  Change it here:
    use a different database       -> build another SendTracker / FollowupRepository
    send through another provider  -> build another MailSender
    add a new data source          -> register it in sheets/factory.py
"""

from functools import cached_property
from typing import Optional

from app.config.settings import Settings, load_settings
from app.domain.entities import SheetSchema
from app.domain.interfaces import Clock, Prompter, Reporter, SendTracker
from app.infrastructure.clock import SystemClock
from app.infrastructure.console import ConsolePrompter, ConsoleReporter
from app.infrastructure.files import FileActivityLog, FileTemplateRepository
from app.infrastructure.google.gmail_gateway import GmailGateway
from app.infrastructure.google.mime import MimeMessageBuilder
from app.infrastructure.google.oauth import GoogleOAuth
from app.infrastructure.persistence.database import Database
from app.infrastructure.persistence.followup_repository import SqlFollowupRepository
from app.infrastructure.persistence.safe import FailSafe
from app.infrastructure.persistence.send_tracker import NullSendTracker, SqlSendTracker
from app.infrastructure.sheets.factory import RegistrySheetGatewayFactory, build_sheet_factory
from app.services.campaign_service import CampaignService
from app.services.composers import AttachmentResolver, CampaignComposer, FollowupComposer
from app.services.followup_policy import WaitDaysPolicy
from app.services.followup_service import FollowupService
from app.services.placeholders import PlaceholderRenderer
from app.services.reply_analysis import ThreadAnalyzer
from app.services.retry import RetryingSender
from app.services.sheet_sync import SheetSyncService
from app.services.validation import AddressListParser, EmailValidator


class Container:

    def __init__(self, settings: Optional[Settings] = None, reporter: Optional[Reporter] = None,
                 clock: Optional[Clock] = None, prompter: Optional[Prompter] = None):
        self.settings = settings or load_settings()
        self.reporter = reporter or ConsoleReporter()
        self.clock = clock or SystemClock()
        self.prompter = prompter or ConsolePrompter()

    # ------------------------------------------------------------ shared pieces
    @cached_property
    def schema(self) -> SheetSchema:
        c = self.settings.columns
        return SheetSchema(c.email, c.cc, c.bcc, c.status, c.skip_sent)

    @cached_property
    def templates(self) -> FileTemplateRepository:
        return FileTemplateRepository(self.settings.paths.templates_dir)

    @cached_property
    def activity_log(self) -> FileActivityLog:
        return FileActivityLog(self.settings.paths.log_file)

    @cached_property
    def sheet_gateways(self) -> RegistrySheetGatewayFactory:
        g = self.settings.google
        sheets_oauth = GoogleOAuth(g.credentials_file, g.sheets_token_file, g.sheets_scopes)
        return build_sheet_factory(self.settings.paths, self.settings.source, sheets_oauth)

    @cached_property
    def database(self) -> Database:
        return Database(self.settings.database.url)

    def _gmail(self, token_file: str, scopes) -> GmailGateway:
        g = self.settings.google
        return GmailGateway(GoogleOAuth(g.credentials_file, token_file, scopes), MimeMessageBuilder())

    @cached_property
    def sending_gmail(self) -> GmailGateway:
        """Login with the existing token.json (send permission only)."""
        g = self.settings.google
        return self._gmail(g.gmail_token_file, g.gmail_scopes)

    @cached_property
    def followup_gmail(self) -> GmailGateway:
        """Login that can also READ replies (token_followup.json)."""
        g = self.settings.google
        return self._gmail(g.followup_token_file, g.followup_scopes)

    # ------------------------------------------------------------ send tracker
    def send_tracker(self) -> SendTracker:
        """Database on -> stored (and a DB problem can never stop sending). Off -> does nothing."""

        if not self.settings.database.enabled:
            return NullSendTracker()

        try:
            self.database.create_schema()
            self.reporter.info("Database connected.")
            return FailSafe(SqlSendTracker(self.database, self.clock), self.reporter)
        except Exception as e:
            self.reporter.warn(f"[DB] Disabled - could not connect: {e}")
            return NullSendTracker()

    # ------------------------------------------------------------ use cases
    def campaign_service(self) -> CampaignService:

        s = self.settings
        renderer = PlaceholderRenderer()

        composer = CampaignComposer(
            self.templates, renderer, AddressListParser(),
            AttachmentResolver(s.sending.attachments, self.reporter), self.schema)

        sender = RetryingSender(self.sending_gmail, s.sending.retry_attempts,
                                s.sending.retry_pause_seconds, self.clock, self.reporter)

        return CampaignService(
            self.sheet_gateways, composer, sender, self.send_tracker(), self.activity_log,
            EmailValidator(), self.schema, self.reporter, self.clock, s.sending.delay_seconds)

    def followup_service(self, days: Optional[int] = None, max_followups: Optional[int] = None,
                         update_sheets: bool = True) -> FollowupService:

        s = self.settings
        waits = list(s.followup.wait_days)

        if days is not None:
            waits = [days] * len(waits)
        if max_followups is not None:
            waits = waits[:max_followups]

        gmail = self.followup_gmail
        renderer = PlaceholderRenderer()

        composer = FollowupComposer(
            self.templates, renderer,
            AttachmentResolver(s.followup.attachments, self.reporter), s.followup.subject_mode)

        sender = RetryingSender(gmail, s.sending.retry_attempts, s.sending.retry_pause_seconds,
                                self.clock, self.reporter)

        sync = SheetSyncService(self.sheet_gateways, self.schema, self.reporter, enabled=update_sheets)

        return FollowupService(
            repository=SqlFollowupRepository(self.database, self.clock), connection=gmail, threads=gmail,
            analyzer=ThreadAnalyzer(), policy=WaitDaysPolicy(waits), composer=composer,
            sender=sender, templates=self.templates, sheet_sync=sync, activity=self.activity_log,
            prompter=self.prompter, reporter=self.reporter, clock=self.clock, settings=s.followup)
