"""
campaign_service.py

Use case: SEND the first mails.

    for each sheet tab -> for each row -> skip / validate / send / write Status

The service only talks to INTERFACES (sheet gateway, composer, sender,
tracker, log, reporter, clock), so it has no idea whether the data comes from
Excel or Google Sheets, or whether a database is connected.
"""

from typing import List

from app.domain.entities import RunCounters, SendStatus, SheetLocation, SheetRow, SheetSchema
from app.domain.interfaces import (
    ActivityLog, Clock, Reporter, SendTracker, SheetGateway, SheetGatewayFactory,
)
from app.services.composers import CampaignComposer
from app.services.retry import RetryingSender
from app.services.validation import EmailValidator


class CampaignService:

    def __init__(self, gateways: SheetGatewayFactory, composer: CampaignComposer,
                 sender: RetryingSender, tracker: SendTracker, activity: ActivityLog,
                 validator: EmailValidator, schema: SheetSchema, reporter: Reporter,
                 clock: Clock, delay_seconds: float):
        self._gateways = gateways
        self._composer = composer
        self._sender = sender
        self._tracker = tracker
        self._activity = activity
        self._validator = validator
        self._schema = schema
        self._reporter = reporter
        self._clock = clock
        self._delay = delay_seconds

    # ------------------------------------------------------------------
    def run(self, location: SheetLocation, tabs: List[str], template_name: str) -> RunCounters:

        totals = RunCounters()

        for tab in tabs:
            self._reporter.info(f"\n{'=' * 60}")
            self._reporter.info(f"Reading Sheet : {tab}")
            self._reporter.info("=" * 60)

            totals.add(self._process_tab(location, tab, template_name))

        self._reporter.info("=" * 60)
        self._reporter.info("Email Sending Completed")
        self._reporter.info("=" * 60)
        self._reporter.info(f"Total Records : {totals.total}")
        self._reporter.info(f"Sent          : {totals.sent}")
        self._reporter.info(f"Failed        : {totals.failed}")
        self._reporter.info(f"Skipped       : {totals.skipped}")
        self._reporter.info("=" * 60)

        return totals

    # ------------------------------------------------------------------
    def _process_tab(self, location: SheetLocation, tab: str, template_name: str) -> RunCounters:

        counters = RunCounters()
        gateway = self._gateways.create(location)

        try:
            gateway.open_tab(tab)
            rows = gateway.read_rows()
        except Exception as e:
            self._reporter.error(str(e))
            self._tracker.log_event("SHEET_ERROR", str(e), level="ERROR")
            return counters

        counters.total = len(rows)
        self._reporter.info(f"Total Records : {counters.total}\n")

        # one "run" row per sheet tab in the database (no-op if the DB is off)
        run_id = self._tracker.start_run(gateway.location, tab, template_name, counters.total)

        try:
            for index, row in enumerate(rows, start=1):
                self._process_row(gateway, tab, template_name, run_id, index, row, counters)
        finally:
            # even if something crashes half way, keep the statuses already written
            gateway.save()
            gateway.close()

        self._tracker.finish_run(run_id, counters)
        return counters

    # ------------------------------------------------------------------
    def _process_row(self, gateway: SheetGateway, tab: str, template_name: str,
                     run_id, index: int, row: SheetRow, counters: RunCounters) -> None:

        email = self._schema.email_of(row)
        name = row.get("Name", "")

        self._reporter.info(f"[{index}/{counters.total}] Sending -> {email}")

        # ---- already sent
        if self._schema.already_sent(row):
            counters.skipped += 1
            self._reporter.info("Already Sent. Skipping...\n")
            self._tracker.log_event("SKIPPED", "Already Sent", run_id=run_id,
                                    name=name, to_email=email)
            return

        # ---- invalid address
        if not self._validator.is_valid(email):
            self._write_status(gateway, row, "Invalid Email")
            self._activity.write(name, email, "Invalid Email")
            self._tracker.record(run_id, tab, row, email, SendStatus.INVALID_EMAIL,
                                 error="Invalid Email")
            counters.failed += 1
            return

        # ---- send
        mail = self._composer.compose(template_name, row)
        result = self._sender.send(mail)

        if result.success:
            self._write_status(gateway, row, "Sent")
            counters.sent += 1
            self._activity.write(name, email, "Sent")
            self._tracker.record(run_id, tab, row, email, SendStatus.SENT, mail, result)
            self._reporter.info("Sent Successfully\n")
        else:
            self._write_status(gateway, row, "Failed", result.message)
            counters.failed += 1
            self._activity.write(name, email, "Failed", result.message)
            self._tracker.record(run_id, tab, row, email, SendStatus.FAILED, mail, result,
                                 error=result.message)
            self._reporter.info(f"Failed : {result.message}\n")

        self._clock.sleep(self._delay)

    def _write_status(self, gateway: SheetGateway, row: SheetRow, status: str, message: str = "") -> None:
        value = f"{status} - {message}" if message else status
        gateway.write_cell(row.number, self._schema.status, value)
