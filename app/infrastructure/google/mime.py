"""
google/mime.py

Turns an OutgoingMail into the MIME message Gmail expects.
This is e-mail PROTOCOL detail, so it lives in the infrastructure layer.
"""

import base64
import mimetypes
import os
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.domain.entities import OutgoingMail


class MimeMessageBuilder:

    def build(self, mail: OutgoingMail) -> dict:
        """Returns the body of Gmail's messages.send call."""

        message = MIMEMultipart()
        message["To"] = mail.to

        if mail.bcc:
            message["Bcc"] = ", ".join(mail.bcc)
        if mail.cc:
            message["Cc"] = ", ".join(mail.cc)

        message["Subject"] = mail.subject

        # Thread headers: answer my previous mail in the same conversation
        if mail.in_reply_to:
            message["In-Reply-To"] = mail.in_reply_to
        if mail.references:
            message["References"] = mail.references

        message.attach(MIMEText(mail.html_body, "html", "utf-8"))

        for path in mail.attachments:
            message.attach(self._attachment(path))

        body = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}

        if mail.thread_id:
            body["threadId"] = mail.thread_id

        return body

    @staticmethod
    def _attachment(path: str) -> MIMEBase:

        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        main_type, sub_type = content_type.split("/", 1)

        with open(path, "rb") as file:
            part = MIMEBase(main_type, sub_type)
            part.set_payload(file.read())

        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        return part
