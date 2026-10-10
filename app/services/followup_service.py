"""
followup_service.py

Use case: CHECK replies and send FOLLOW-UPS.

    1. load the mails still waiting for a reply            (repository)
    2. look at every Gmail thread: did somebody reply?     (thread reader + analyzer)
    3. save results in the database and in the sheet       (repository + sheet sync)
    4. decide who needs a follow-up                        (policy)
    5. send it inside the same thread                      (composer + sender)

Each step is its own small method. Everything the service needs is passed in
through the constructor (dependency injection) and typed as an INTERFACE.
"""

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from app.config.settings import FollowupSettings
from app.domain.entities import (
    FollowupDecision, FollowupSummary, MailFilter, ReplyAnalysis, ReplyType,
    SendResult, SentMailRecord, SentScope,
)
from app.domain.interfaces import (
    ActivityLog, Clock, Connectable, FollowupPolicy, FollowupRepository, Prompter,
    Reporter, TemplateRepository, ThreadReader,
)
from app.services.composers import FollowupComposer, TemplateError
from app.services.reply_analysis import ThreadAnalyzer
from app.services.retry import RetryingSender
from app.services.sheet_sync import SheetSyncService


@dataclass
class FollowupOptions:
    mail_filter: MailFilter = field(default_factory=MailFilter)
    ask_scope: bool = False          # ask "which company?" when nothing was selected
    interactive: bool = False        # a person is at the keyboard (can confirm)
    check_only: bool = False
    dry_run: bool = False
    sync_only: bool = False
    assume_yes: bool = False
    template: Optional[str] = None
    limit: Optional[int] = None


@dataclass
class _CheckOutcome:
    analyses: Dict[int, ReplyAnalysis] = field(default_factory=dict)
    replied: List[SentMailRecord] = field(default_factory=list)
    bounced: List[SentMailRecord] = field(default_factory=list)
    auto: List[SentMailRecord] = field(default_factory=list)
    failed: List[SentMailRecord] = field(default_factory=list)


class FollowupService:

    def __init__(self, repository: FollowupRepository, connection: Connectable,
                 threads: ThreadReader, analyzer: ThreadAnalyzer, policy: FollowupPolicy,
                 composer: FollowupComposer, sender: RetryingSender,
                 templates: TemplateRepository, sheet_sync: SheetSyncService,
                 activity: ActivityLog, prompter: Prompter, reporter: Reporter,
                 clock: Clock, settings: FollowupSettings):
        self._repo = repository
        self._connection = connection
        self._threads = threads
        self._analyzer = analyzer
        self._policy = policy
        self._composer = composer
        self._sender = sender
        self._templates = templates
        self._sync = sheet_sync
        self._activity = activity
        self._prompter = prompter
        self._reporter = reporter
        self._clock = clock
        self._s = settings

    # ==================================================================
    # Entry point
    # ==================================================================
    def run(self, options: FollowupOptions) -> FollowupSummary:

        summary = FollowupSummary()
        template = options.template or self._s.template

        # Fail now, not after checking 50 threads
        if not (options.check_only or options.sync_only) and not self._templates.exists(template):
            self._reporter.error(f"Template not found : templates/{template}")
            self._reporter.error("Fix followup.template in app/config/settings.py")
            return summary

        self._repo.init()

        mail_filter = options.mail_filter
        if mail_filter.is_empty and options.ask_scope:
            mail_filter = self._choose_scope(mail_filter)

        dry = options.dry_run
        mode = "sync_sheet" if options.sync_only else ("check_only" if options.check_only else "normal")
        run_id = None if dry else self._repo.start_run(mode, mail_filter)

        if options.sync_only:
            self._sync_only(mail_filter, dry)
            self._repo.finish_run(run_id, 0, 0, 0, 0, 0)
            return summary

        # ---- 1. which mails are waiting for a reply?
        open_mails = self._repo.fetch_sent(mail_filter, SentScope.OPEN)
        self._reporter.info(f"\nMails waiting for a reply : {len(open_mails)}")

        if not open_mails:
            self._reporter.info("Nothing to check.")
            self._repo.finish_run(run_id, 0, 0, 0, 0, 0)
            return summary

        # ---- 2. check replies
        outcome = self._check_replies(open_mails, dry)

        summary.checked = len(open_mails) - len(outcome.failed)
        summary.replied = len(outcome.replied)
        summary.bounced = len(outcome.bounced)
        summary.auto_replies = len(outcome.auto)
        summary.check_failed = len(outcome.failed)

        # ---- 3. sheet: Reply_Status
        self._update_reply_status(mail_filter, dry, outcome)

        # ---- 4 + 5. follow-ups
        if not options.check_only:
            summary.sent, summary.failed = self._follow_up(
                open_mails, outcome, options, template, run_id)

        self._repo.finish_run(run_id, summary.checked, summary.replied, summary.bounced,
                              summary.sent, summary.failed)
        self._print_summary(summary)
        return summary

    # ==================================================================
    # Which company?
    # ==================================================================
    def _choose_scope(self, mail_filter: MailFilter) -> MailFilter:

        sent = self._repo.fetch_sent(mail_filter, SentScope.ALL)
        if not sent:
            return mail_filter

        table: Dict[str, Dict[str, int]] = {}
        for rec in sent:
            key = rec.sheet_tab or rec.company or "?"
            row = table.setdefault(key, {"sent": 0, "replied": 0, "followups": 0})
            row["sent"] += 1
            row["replied"] += 1 if rec.reply_received else 0
            row["followups"] += rec.followup_count

        chosen = self._prompter.choose_scope(table)
        return replace(mail_filter, companies=tuple(chosen)) if chosen else mail_filter

    # ==================================================================
    # Step: check replies
    # ==================================================================
    def _check_replies(self, open_mails: List[SentMailRecord], dry: bool) -> _CheckOutcome:

        self._reporter.info("\nLogging in to Gmail...")
        self._connection.connect()
        me = self._threads.my_address()
        self._reporter.info(f"Logged in as {me}\n")

        self._reporter.info("-" * 70)
        self._reporter.info(" STEP 1: Checking replies")
        self._reporter.info("-" * 70)

        outcome = _CheckOutcome()

        for i, rec in enumerate(open_mails, start=1):

            label = f"[{i}/{len(open_mails)}] {rec.to_email}"

            try:
                analysis = self._analyzer.analyze(self._threads.fetch_thread(rec.thread_id), me)
            except Exception as e:
                self._reporter.info(f"{label} -> could not check ({str(e)[:60]})")
                outcome.failed.append(rec)
                continue

            outcome.analyses[rec.id] = analysis

            if analysis.reply_type == ReplyType.REPLY:
                self._reporter.info(f"{label} -> REPLIED")
                outcome.replied.append(rec)
            elif analysis.reply_type == ReplyType.BOUNCE:
                self._reporter.info(f"{label} -> BOUNCED (address does not work)")
                outcome.bounced.append(rec)
            elif analysis.reply_type == ReplyType.AUTO_REPLY:
                self._reporter.info(f"{label} -> auto-reply only (still counts as no reply)")
                outcome.auto.append(rec)
            else:
                self._reporter.info(f"{label} -> no reply")

            if not dry:
                self._repo.save_check(rec, analysis)

            self._clock.sleep(self._s.check_delay_seconds)

        return outcome

    # ==================================================================
    # Step: write Reply_Status into the sheet
    # ==================================================================
    def _queue_reply_status(self, records: List[SentMailRecord]) -> None:
        for rec in records:
            value = self._s.bounce_value if rec.reply_type == ReplyType.BOUNCE else self._s.reply_yes_value
            self._sync.queue(rec, self._s.reply_status_column, value)

    def _update_reply_status(self, mail_filter: MailFilter, dry: bool, outcome: _CheckOutcome) -> None:

        self._reporter.info("\n" + "-" * 70)
        self._reporter.info(f" Sheet update ({self._s.reply_status_column})")
        self._reporter.info("-" * 70)

        if dry:
            self._reporter.info(f"(dry-run) {len(outcome.replied) + len(outcome.bounced)} "
                                f"new reply/bounce found - nothing written.")
            return

        if not self._sync.enabled:
            self._reporter.info("Skipped (--no-sheet-update).")
            return

        # EVERY mail with a reply / bounce - not only the new ones - so a sheet
        # that missed an earlier update is repaired. Correct cells are skipped.
        self._queue_reply_status(self._repo.fetch_sent(mail_filter, SentScope.CLOSED))

        if not self._sync.has_pending:
            self._reporter.info("No replies or bounces found yet, so there is nothing to write.")
            return

        self._print_sync_report(self._sync.flush(), repair_hint=True)

    def _sync_only(self, mail_filter: MailFilter, dry: bool) -> None:

        closed = self._repo.fetch_sent(mail_filter, SentScope.CLOSED)
        self._reporter.info(f"\nMails with a known reply / bounce : {len(closed)}")
        self._queue_reply_status(closed)

        if dry:
            self._reporter.info("(dry-run) nothing written")
            return

        self._print_sync_report(self._sync.flush(), repair_hint=False)

    def _print_sync_report(self, report, repair_hint: bool) -> None:
        self._reporter.info(f"Sheet cells updated : {report.written}   "
                            f"(already up to date: {report.unchanged})")
        for problem in report.problems:
            self._reporter.info(f"  ! {problem}")
        if report.problems and repair_hint:
            self._reporter.info("  Fix the problem (e.g. close the Excel file), then run:")
            self._reporter.info("  python followup_main.py --sync-sheet --all")

    # ==================================================================
    # Step: follow-ups
    # ==================================================================
    def _follow_up(self, open_mails, outcome: _CheckOutcome, options: FollowupOptions,
                   template: str, run_id) -> Tuple[int, int]:

        now = self._clock.now()
        closed_ids = {r.id for r in outcome.replied} | {r.id for r in outcome.bounced}

        plan: List[Tuple[SentMailRecord, FollowupDecision]] = []
        not_due = finished = 0

        for rec in open_mails:

            # replied / bounced / could not be checked -> NEVER follow up
            if rec.id in closed_ids or rec.id not in outcome.analyses:
                continue

            decision = self._policy.evaluate(rec, now)

            if decision.due:
                plan.append((rec, decision))
            elif decision.reason.startswith("max"):
                finished += 1
            else:
                not_due += 1

        if options.limit:
            plan = plan[:options.limit]

        self._reporter.info("\n" + "-" * 70)
        self._reporter.info(" STEP 2: Follow-ups")
        self._reporter.info("-" * 70)
        self._reporter.info(f"Due now      : {len(plan)}")
        self._reporter.info(f"Not due yet  : {not_due}")
        self._reporter.info(f"Finished     : {finished}  (max follow-ups reached)")

        if plan:
            self._reporter.info(f"\n  {'To':<34}{'Company/Tab':<16}{'No.':>4}  Template")
            for rec, decision in plan:
                self._reporter.info(f"  {rec.to_email:<34}{(rec.company or rec.sheet_tab or ''):<16}"
                                    f"{decision.number:>4}  {template}")

        if options.dry_run:
            self._reporter.info("\n(dry-run) nothing was sent or saved.")
            return 0, 0

        # Sending cannot be undone, so ask first
        if plan and not options.assume_yes:
            if not options.interactive:
                self._reporter.info("\nNot an interactive terminal - add --yes to send. Stopping.")
                return 0, 0
            if not self._prompter.confirm(f"Send {len(plan)} follow-up mail(s)?"):
                self._reporter.info("Cancelled. Nothing was sent.")
                return 0, 0

        sent = failed = 0

        for i, (rec, decision) in enumerate(plan, start=1):

            self._reporter.info(f"[{i}/{len(plan)}] Follow-up {decision.number} -> {rec.to_email}")

            result, subject = self._send_one(rec, decision.number, template, outcome.analyses[rec.id])

            self._repo.save_followup(rec, run_id, decision.number, template, subject, result)

            if result.success:
                sent += 1
                self._reporter.info("Sent Successfully\n")
                self._activity.write(rec.name or "", rec.to_email, f"Follow-up {decision.number} Sent")
                self._sync.queue(rec, self._s.followup_status_column,
                                 f"Follow-up {decision.number} sent {self._clock.now():%Y-%m-%d}")
            else:
                failed += 1
                self._reporter.info(f"Failed : {result.message}\n")
                self._activity.write(rec.name or "", rec.to_email,
                                     f"Follow-up {decision.number} Failed", result.message)

            self._clock.sleep(self._s.send_delay_seconds)

        if sent and self._sync.enabled and self._s.followup_status_column:
            report = self._sync.flush()
            self._reporter.info(f"Sheet cells updated ({self._s.followup_status_column}) : {report.written}")
            for problem in report.problems:
                self._reporter.info(f"  ! {problem}")

        return sent, failed

    def _send_one(self, rec: SentMailRecord, number: int, template: str,
                  analysis: ReplyAnalysis) -> Tuple[SendResult, Optional[str]]:
        """Returns (result, subject that was used)."""

        # Template problems are not temporary, so no retry for them
        try:
            mail = self._composer.compose(rec, number, template, analysis)
        except TemplateError as e:
            return SendResult(False, str(e), None, 0, [str(e)]), None

        return self._sender.send(mail), mail.subject

    # ==================================================================
    def _print_summary(self, s: FollowupSummary) -> None:
        self._reporter.info("\n" + "=" * 70)
        self._reporter.info(f" Checked          : {s.checked}")
        self._reporter.info(f" Replied          : {s.replied}")
        self._reporter.info(f" Bounced          : {s.bounced}")
        self._reporter.info(f" Auto-replies     : {s.auto_replies}")
        self._reporter.info(f" Check failed     : {s.check_failed}")
        self._reporter.info(f" Follow-ups sent  : {s.sent}")
        self._reporter.info(f" Follow-ups failed: {s.failed}")
        self._reporter.info("=" * 70)
