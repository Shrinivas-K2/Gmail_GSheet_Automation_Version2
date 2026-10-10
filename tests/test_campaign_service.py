"""CampaignService with fake sheet, fake Gmail, fake tracker: no Excel, no Gmail, no DB."""

from app.domain.entities import SendStatus
from app.domain.interfaces import SendTracker, Template
from app.services.campaign_service import CampaignService
from app.services.composers import AttachmentResolver, CampaignComposer
from app.services.placeholders import PlaceholderRenderer
from app.services.retry import RetryingSender
from app.services.validation import AddressListParser, EmailValidator
from app.domain.entities import SheetSchema
from tests.fakes import (
    FakeActivityLog, FakeClock, FakeMailGateway, FakeReporter, FakeSheetFactory, FakeTemplates,
)


class RecordingTracker(SendTracker):
    def __init__(self): self.calls = []
    def start_run(self, location, tab, template, total): self.calls.append(("start", tab, total)); return 7
    def record(self, run_id, tab, row, email, status, mail=None, result=None, error=None):
        self.calls.append(("record", run_id, email, status, error))
    def log_event(self, event, message="", level="INFO", run_id=None, name=None, to_email=None):
        self.calls.append(("event", event, to_email))
    def finish_run(self, run_id, counters): self.calls.append(("finish", run_id, counters.sent, counters.failed, counters.skipped))


def build(book, gmail):
    reporter, clock, tracker, activity = FakeReporter(), FakeClock(), RecordingTracker(), FakeActivityLog()
    templates = FakeTemplates({"Generic.html": Template("Hello {Company}", "Hi {Name}")})
    factory = FakeSheetFactory(book)
    composer = CampaignComposer(templates, PlaceholderRenderer(), AddressListParser(),
                                AttachmentResolver([], reporter), SheetSchema())
    service = CampaignService(factory, composer, RetryingSender(gmail, 2, 0, clock, reporter),
                              tracker, activity, EmailValidator(), SheetSchema(), reporter, clock, 2)
    return service, factory, tracker, activity, reporter, clock


def test_campaign_sends_skips_and_marks_failures():
    book = {"Rapido": {"headers": ["Name", "Email", "Company", "Status"], "rows": [
        ["Asha", "asha@x.com", "Rapido", None],          # will be sent
        ["Bala", "bala@x.com", "Rapido", "Sent"],        # already sent -> skipped
        ["Chitra", "not-an-email", "Rapido", None],      # invalid
        ["Dev", "fail@x.com", "Rapido", None],           # Gmail fails
        [None, None, None, None],                        # blank row ignored
    ]}}
    gmail = FakeMailGateway(fail_for=["fail@x.com"])
    service, factory, tracker, activity, reporter, clock = build(book, gmail)

    totals = service.run(factory.default_location(), ["Rapido"], "Generic.html")

    assert (totals.total, totals.sent, totals.failed, totals.skipped) == (4, 1, 2, 1)
    assert [m.to for m in gmail.sent] == ["asha@x.com"]
    assert gmail.sent[0].subject == "Hello Rapido" and gmail.sent[0].html_body == "Hi Asha"

    assert factory.cell("Rapido", 2, "Status") == "Sent"
    assert factory.cell("Rapido", 3, "Status") == "Sent"                  # untouched
    assert factory.cell("Rapido", 4, "Status") == "Invalid Email"
    assert factory.cell("Rapido", 5, "Status") == "Failed - Fake Gmail 500"

    assert [e[2] for e in activity.entries] == ["Sent", "Invalid Email", "Failed"]
    statuses = [c[3] for c in tracker.calls if c[0] == "record"]
    assert statuses == [SendStatus.SENT, SendStatus.INVALID_EMAIL, SendStatus.FAILED]
    assert ("event", "SKIPPED", "bala@x.com") in tracker.calls
    assert tracker.calls[0] == ("start", "Rapido", 4) and tracker.calls[-1] == ("finish", 7, 1, 2, 1)

    assert factory.created[0].saved == 1 and factory.created[0].closed
    assert clock.slept.count(2) == 2          # delay after sent + failed, not after skip / invalid


def test_missing_tab_is_reported_and_logged_not_crashed():
    service, factory, tracker, *_ , reporter, _ = build({"Other": {"headers": ["Email"], "rows": []}}, FakeMailGateway())
    totals = service.run(factory.default_location(), ["Rapido"], "Generic.html")
    assert totals.total == 0 and "Sheet 'Rapido' not found." in reporter.text
    assert ("event", "SHEET_ERROR", None) in tracker.calls


def test_statuses_are_saved_even_if_a_crash_happens_half_way():
    book = {"T": {"headers": ["Name", "Email", "Company", "Status"], "rows": [
        ["A", "a@x.com", "C", None], ["B", "b@x.com", "C", None]]}}

    class ExplodingGmail(FakeMailGateway):
        def send(self, mail):
            if mail.to == "b@x.com":
                raise KeyboardInterrupt()
            return super().send(mail)

    service, factory, *_ = build(book, ExplodingGmail())
    try:
        service.run(factory.default_location(), ["T"], "Generic.html")
    except KeyboardInterrupt:
        pass
    assert factory.cell("T", 2, "Status") == "Sent" and factory.created[0].saved == 1
