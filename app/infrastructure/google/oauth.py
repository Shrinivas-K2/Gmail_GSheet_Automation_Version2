"""
google/oauth.py

ONE place for the Google OAuth login. (The old code had this logic copied
three times: Gmail, Sheets, Follow-up.)

    GoogleOAuth(credentials_file, token_file, scopes).credentials()

First run opens the browser once and saves the token file; after that the
login is automatic and the token is refreshed when it expires.
"""

import os
from typing import Optional, Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


class GoogleOAuth:

    def __init__(self, credentials_file: str, token_file: str, scopes: Sequence[str]):
        self._credentials_file = credentials_file
        self._token_file = token_file
        self._scopes = list(scopes)
        self._cached: Optional[Credentials] = None

    def credentials(self) -> Credentials:

        if self._cached and self._cached.valid:
            return self._cached

        creds = None

        if os.path.exists(self._token_file):
            creds = Credentials.from_authorized_user_file(self._token_file, self._scopes)

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(self._credentials_file, self._scopes)
            creds = flow.run_local_server(port=0)

            with open(self._token_file, "w") as token:
                token.write(creds.to_json())

        self._cached = creds
        return creds
