"""
google/gmail_gateway.py

The ONLY class that talks to the Gmail API.
It fulfils three small contracts: Connectable, MailSender, ThreadReader.
Gmail's JSON is converted into our own neutral objects here, so nothing
outside this file needs to know what Gmail's answers look like.
"""

from datetime import datetime, timezone
from email.utils import parseaddr

from googleapiclient.discovery import build

from app.domain.entities import MailThread, OutgoingMail, SentReceipt, ThreadMessage
from app.domain.interfaces import Connectable, MailSender, ThreadReader
from app.infrastructure.google.mime import MimeMessageBuilder
from app.infrastructure.google.oauth import GoogleOAuth

# "metadata" format = headers only, no mail body
_THREAD_HEADERS = ["From", "To", "Subject", "Date", "Message-ID", "References", "Auto-Submitted"]


class GmailGateway(Connectable, MailSender, ThreadReader):

    def __init__(self, oauth: GoogleOAuth, mime: MimeMessageBuilder):
        self._oauth = oauth
        self._mime = mime
        self._service = None

    # ---- Connectable
    def connect(self) -> None:
        if self._service is None:
            self._service = build("gmail", "v1", credentials=self._oauth.credentials())

    @property
    def _api(self):
        self.connect()
        return self._service

    # ---- MailSender
    def send(self, mail: OutgoingMail) -> SentReceipt:
        response = self._api.users().messages().send(
            userId="me", body=self._mime.build(mail)
        ).execute()

        # Gmail answers {"id": "...", "threadId": "...", "labelIds": [...]}
        return SentReceipt(message_id=response.get("id"), thread_id=response.get("threadId"))

    # ---- ThreadReader
    def my_address(self) -> str:
        return self._api.users().getProfile(userId="me").execute()["emailAddress"]

    def fetch_thread(self, thread_id: str) -> MailThread:

        raw = self._api.users().threads().get(
            userId="me", id=thread_id, format="metadata", metadataHeaders=_THREAD_HEADERS
        ).execute()

        return MailThread(tuple(self._to_message(m) for m in raw.get("messages", [])))

    @staticmethod
    def _to_message(raw: dict) -> ThreadMessage:

        headers = {h["name"].lower(): h["value"]
                   for h in raw.get("payload", {}).get("headers", [])}
        labels = set(raw.get("labelIds", []))

        millis = raw.get("internalDate")
        received = datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc) if millis else None

        return ThreadMessage(
            from_address=parseaddr(headers.get("from", ""))[1],
            subject=headers.get("subject", ""),
            snippet=raw.get("snippet", ""),
            received_at=received,
            message_id=headers.get("message-id"),
            references=headers.get("references"),
            auto_submitted=headers.get("auto-submitted", ""),
            is_outgoing="SENT" in labels,
            is_draft="DRAFT" in labels,
        )
