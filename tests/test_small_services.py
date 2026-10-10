"""Unit tests of the small, pure pieces (no I/O at all)."""

from datetime import datetime, timedelta, timezone

from app.domain.entities import (
    MailThread, ReplyType, SentMailRecord, SheetLocation, SheetRow, SheetSchema, ThreadMessage,
)
from app.domain.interfaces import ReplyRule, Template
from app.services.composers import AttachmentResolver, CampaignComposer, FollowupComposer, TemplateError
from app.services.followup_policy import WaitDaysPolicy
from app.services.placeholders import PlaceholderRenderer
from app.services.reply_analysis import ThreadAnalyzer
from app.services.retry import RetryingSender
from app.services.validation import AddressListParser, EmailValidator
from tests.fakes import FakeClock, FakeMailGateway, FakeReporter, FakeTemplates

UTC = timezone.utc


# ------------------------------------------------------------ placeholders / validation
def test_placeholders_replace_and_blank_none():
    r = PlaceholderRenderer()
    assert r.render("Hi {Name} at {Company}", {"Name": "Asha", "Company": None}) == "Hi Asha at "
    assert r.unfilled("Hello {Role}, <style>a{color:red}</style>") == ["{Role}"]


def test_email_validator_and_address_parser():
    v, p = EmailValidator(), AddressListParser()
    assert v.is_valid("a.b+c@x.co") and not v.is_valid("nope") and not v.is_valid(None)
    assert p.split("a@x.com; b@y.com, c@z.com") == ["a@x.com", "b@y.com", "c@z.com"]
    assert p.split(None) == []


# ------------------------------------------------------------ reply analysis
def msg(sender, **kw):
    return ThreadMessage(from_address=sender, **kw)


def analyze(*messages, rules=None):
    return ThreadAnalyzer(rules).analyze(MailThread(tuple(messages)), "me@gmail.com")


def test_no_reply():
    a = analyze(msg("me@gmail.com", is_outgoing=True, message_id="<1>", subject="Hello"))
    assert a.reply_type is None and a.last_message_id == "<1>" and a.subject == "Hello"


def test_real_reply_bounce_autoreply_and_priority():
    mine = msg("me@gmail.com", is_outgoing=True, message_id="<1>")
    assert analyze(mine, msg("asha@x.com", snippet="Thanks")).reply_type == ReplyType.REPLY
    assert analyze(mine, msg("Mailer-Daemon@googlemail.com")).reply_type == ReplyType.BOUNCE
    assert analyze(mine, msg("x@y.com", auto_submitted="auto-replied")).reply_type == ReplyType.AUTO_REPLY
    assert analyze(mine, msg("x@y.com", subject="Automatic reply: away")).reply_type == ReplyType.AUTO_REPLY
    # a real reply beats an auto-reply that arrived first
    both = analyze(mine, msg("x@y.com", auto_submitted="auto-replied"), msg("asha@x.com"))
    assert both.reply_type == ReplyType.REPLY


def test_own_bcc_copy_and_drafts_are_not_replies():
    a = analyze(msg("me@gmail.com", is_outgoing=True), msg("ME@gmail.com"), msg("x@y.com", is_draft=True))
    assert a.reply_type is None


def test_new_rule_can_be_added_without_changing_existing_code():
    class UnsubscribeRule(ReplyRule):
        def matches(self, m): return "unsubscribe" in m.subject.lower()

    analyzer = ThreadAnalyzer([(ReplyType.BOUNCE, UnsubscribeRule())])      # reuse a type for the demo
    assert analyzer.classify(msg("a@b.com", subject="Please unsubscribe me")) == ReplyType.BOUNCE


# ------------------------------------------------------------ policy
def record(sent_days_ago=0, count=0, last_followup_days_ago=None, now=datetime(2026, 10, 10, 12, tzinfo=UTC)):
    return SentMailRecord(
        id=1, to_email="a@x.com", subject="Subj", thread_id="t", message_id="m",
        sent_at=now - timedelta(days=sent_days_ago), cc=(), bcc=(), snapshot={"Name": "Asha"},
        company="Co", name="Asha", sheet_tab="Tab", sheet_row_number=2,
        location=SheetLocation("excel", "Cold_Mail.xlsx"), run_id=1, followup_count=count,
        last_followup_at=(now - timedelta(days=last_followup_days_ago)
                          if last_followup_days_ago is not None else None))


NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)


def test_policy_zero_wait_is_due_immediately_and_stops_at_max():
    p = WaitDaysPolicy([0, 0])
    assert p.evaluate(record(), NOW).due
    d = p.evaluate(record(count=2), NOW)
    assert not d.due and d.reason == "max follow-ups reached"


def test_policy_waits_counted_from_last_followup():
    p = WaitDaysPolicy([3, 5])
    assert not p.evaluate(record(sent_days_ago=2), NOW).due
    assert p.evaluate(record(sent_days_ago=3), NOW).due
    d = p.evaluate(record(sent_days_ago=10, count=1, last_followup_days_ago=2), NOW)
    assert not d.due and d.number == 2 and d.reason.startswith("due on")
    assert p.evaluate(record(sent_days_ago=10, count=1, last_followup_days_ago=5), NOW).due


# ------------------------------------------------------------ composers
def templates():
    return FakeTemplates({
        "first.html": Template("Hello {Company}", "Hi {Name}"),
        "fu.html": Template("Re: {OriginalSubject}", "Hi {Name}, sent {SentDate}, role {Role}"),
        "fu_ok.html": Template("", "Hi {Name} (#{FollowupNumber})"),
    })


def test_campaign_composer_builds_mail_with_cc_bcc_and_missing_attachment_warning(tmp_path):
    reporter = FakeReporter()
    existing = tmp_path / "cv.pdf"; existing.write_text("x")
    composer = CampaignComposer(templates(), PlaceholderRenderer(), AddressListParser(),
                                AttachmentResolver([str(existing), "nope.pdf"], reporter), SheetSchema())
    row = SheetRow(2, {"Name": "Asha", "Email": " a@x.com ", "Company": "Rapido", "CC": "c1@x.com; c2@x.com", "BCC": None})
    mail = composer.compose("first.html", row)
    assert (mail.to, mail.subject, mail.html_body) == ("a@x.com", "Hello Rapido", "Hi Asha")
    assert mail.cc == ["c1@x.com", "c2@x.com"] and mail.bcc == [] and mail.attachments == [str(existing)]
    assert "Attachment not found : nope.pdf" in reporter.text


def test_followup_composer_threads_the_mail_and_blocks_unfilled_placeholders():
    from app.domain.entities import ReplyAnalysis
    composer = FollowupComposer(templates(), PlaceholderRenderer(), AttachmentResolver([], FakeReporter()))
    analysis = ReplyAnalysis(last_message_id="<orig@mail>", references="<root@mail>", subject="Subj")
    mail = composer.compose(record(sent_days_ago=4), 2, "fu_ok.html", analysis)
    assert mail.subject == "Re: Subj" and mail.html_body == "Hi Asha (#2)"
    assert (mail.thread_id, mail.in_reply_to, mail.references) == ("t", "<orig@mail>", "<root@mail> <orig@mail>")
    try:
        composer.compose(record(), 1, "fu.html", analysis)          # snapshot has no {Role}
        assert False, "should have raised"
    except TemplateError as e:
        assert "{Role}" in str(e)


def test_followup_subject_template_mode():
    from app.domain.entities import ReplyAnalysis
    composer = FollowupComposer(templates(), PlaceholderRenderer(), AttachmentResolver([], FakeReporter()), "template")
    t = FakeTemplates({"x.html": Template("Quick note for {Name}", "Hi {Name}")})
    composer._templates = t
    assert composer.compose(record(), 1, "x.html", ReplyAnalysis()).subject == "Quick note for Asha"


# ------------------------------------------------------------ retry decorator
def mail():
    from app.domain.entities import OutgoingMail
    return OutgoingMail(to="a@x.com", subject="s", html_body="b")


def test_retry_succeeds_first_time_and_fails_after_all_attempts():
    ok = RetryingSender(FakeMailGateway(), 3, 1, FakeClock(), FakeReporter()).send(mail())
    assert ok.success and ok.attempts == 1 and ok.receipt.message_id == "msg1"

    clock, reporter = FakeClock(), FakeReporter()
    bad = RetryingSender(FakeMailGateway(fail_for=["a@x.com"]), 3, 1, clock, reporter).send(mail())
    assert not bad.success and bad.attempts == 3 and len(bad.errors) == 3 and bad.message == "Fake Gmail 500"
    assert clock.slept == [1, 1]                       # pause between tries, not after the last
    assert "Retry 3/3 for a@x.com..." in reporter.text
