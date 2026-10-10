"""
composers.py

Turn "template + data" into an OutgoingMail.

    CampaignComposer   first mail   (template + one sheet row)
    FollowupComposer   follow-up    (template + stored mail, stays in the same thread)

They only know the TemplateRepository INTERFACE, never files or Gmail.
"""

import os
from typing import List, Sequence

from app.domain.entities import (
    OutgoingMail, ReplyAnalysis, SentMailRecord, SheetRow, SheetSchema,
)
from app.domain.interfaces import Reporter, TemplateRepository
from app.services.placeholders import PlaceholderRenderer
from app.services.validation import AddressListParser


class TemplateError(Exception):
    """The template cannot be used for this mail (for example a missing value)."""


class AttachmentResolver:
    """Keeps only the attachments that exist (and warns about the others)."""

    def __init__(self, paths: Sequence[str], reporter: Reporter):
        self._paths = list(paths)
        self._reporter = reporter

    def existing(self) -> List[str]:
        found = []
        for path in self._paths:
            if os.path.exists(path):
                found.append(path)
            else:
                self._reporter.warn(f"Attachment not found : {path}")
        return found


class CampaignComposer:

    def __init__(self, templates: TemplateRepository, renderer: PlaceholderRenderer,
                 parser: AddressListParser, attachments: AttachmentResolver,
                 schema: SheetSchema):
        self._templates = templates
        self._renderer = renderer
        self._parser = parser
        self._attachments = attachments
        self._schema = schema

    def compose(self, template_name: str, row: SheetRow) -> OutgoingMail:

        template = self._templates.load(template_name)
        values = row.values

        return OutgoingMail(
            to=row.text(self._schema.email),
            subject=self._renderer.render(template.subject, values),
            html_body=self._renderer.render(template.body, values),
            cc=self._parser.split(row.text(self._schema.cc)),
            bcc=self._parser.split(row.text(self._schema.bcc)),
            attachments=self._attachments.existing(),
            template_name=template_name,
        )


class FollowupComposer:

    def __init__(self, templates: TemplateRepository, renderer: PlaceholderRenderer,
                 attachments: AttachmentResolver, subject_mode: str = "reply"):
        self._templates = templates
        self._renderer = renderer
        self._attachments = attachments
        self._subject_mode = subject_mode

    def compose(self, record: SentMailRecord, number: int, template_name: str,
                analysis: ReplyAnalysis) -> OutgoingMail:

        template = self._templates.load(template_name)

        # Values usable as {Placeholders}: the row as it was sent + a few extras
        values = dict(record.snapshot)
        values.update({
            "FollowupNumber": number,
            "OriginalSubject": record.subject or "",
            "SentDate": record.sent_at.strftime("%d %b %Y") if record.sent_at else "",
        })

        body = self._renderer.render(template.body, values)

        # Refuse to send a mail that still contains "{Role}" etc.
        leftovers = self._renderer.unfilled(body)
        if leftovers:
            raise TemplateError(f"Unfilled placeholders in template: {', '.join(leftovers)}")

        original = record.subject or analysis.subject or ""

        if self._subject_mode == "template" and template.subject:
            subject = self._renderer.render(template.subject, values)
        else:
            subject = original if original.lower().startswith("re:") else f"Re: {original}"

        references = None
        if analysis.last_message_id:
            references = f"{analysis.references or ''} {analysis.last_message_id}".strip()

        return OutgoingMail(
            to=record.to_email,
            subject=subject,
            html_body=body,
            cc=list(record.cc),
            bcc=list(record.bcc),
            attachments=self._attachments.existing(),
            template_name=template_name,
            thread_id=record.thread_id,
            in_reply_to=analysis.last_message_id,
            references=references,
        )
