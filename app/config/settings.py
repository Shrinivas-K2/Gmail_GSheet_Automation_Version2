"""
app/config/settings.py

ALL settings of the project live here (the old config.py + Followup/followup_config.py).

Settings are plain frozen dataclasses: they hold values only, no behaviour.
Secrets (database password ...) come from the environment / .env file.

The rest of the program never reads os.environ or hard-coded paths: it
receives a `Settings` object (dependency injection).
"""

import os
from dataclasses import dataclass, field
from typing import Tuple

try:                                     # optional: read a .env file
    from dotenv import load_dotenv
except ImportError:                      # pragma: no cover
    load_dotenv = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# PATHS
# ============================================================
@dataclass(frozen=True)
class PathSettings:
    base_dir: str = BASE_DIR
    data_dir: str = os.path.join(BASE_DIR, "data")
    templates_dir: str = os.path.join(BASE_DIR, "templates")
    logs_dir: str = os.path.join(BASE_DIR, "logs")
    secrets_dir: str = os.path.join(BASE_DIR, "secrets")

    excel_file: str = os.path.join(BASE_DIR, "data", "Cold_Mail.xlsx")
    resume_file: str = os.path.join(BASE_DIR, "data", "SRINIVASA_K_SWE.pdf")
    log_file: str = os.path.join(BASE_DIR, "logs", "mail_log.txt")

    def secret(self, name: str) -> str:
        """
        Path of a secret file. Looks in secrets/ first; if the file is still in
        the project root (old layout) that one is used, so nothing breaks.
        """
        preferred = os.path.join(self.secrets_dir, name)
        legacy = os.path.join(self.base_dir, name)
        if not os.path.exists(preferred) and os.path.exists(legacy):
            return legacy
        return preferred


# ============================================================
# SHEET COLUMNS
# ============================================================
@dataclass(frozen=True)
class ColumnSettings:
    email: str = "Email"
    cc: str = "CC"
    bcc: str = "BCC"
    status: str = "Status"
    skip_sent: bool = True            # rows already marked "Sent" are skipped


# ============================================================
# DATA SOURCE (where the contacts come from)
# ============================================================
@dataclass(frozen=True)
class SourceSettings:
    kind: str = "google_sheet"                       # "excel" | "google_sheet"
    google_spreadsheet_name: str = "Cold_Mail"
    google_spreadsheet_id: str = "1BNaAPH-8Dj5zW7d2NKJCZVBEgTLWt3F82T_1bXmWyBU"           # optional; more reliable than the name


# ============================================================
# SENDING (first mails)
# ============================================================
@dataclass(frozen=True)
class SendingSettings:
    default_sheets: Tuple[str, ...] = ("Testing",)
    default_template: str = "Generic.html"
    attachments: Tuple[str, ...] = (PathSettings.resume_file,)
    delay_seconds: float = 2              # pause between mails
    retry_attempts: int = 3
    retry_pause_seconds: float = 1


# ============================================================
# GOOGLE LOGIN  (three separate token files = nothing breaks)
# ============================================================
@dataclass(frozen=True)
class GoogleSettings:
    credentials_file: str = "credentials.json"
    gmail_token_file: str = "token.json"                 # send only
    sheets_token_file: str = "token_sheets.json"         # Google Sheets
    followup_token_file: str = "token_followup.json"     # send + read replies

    gmail_scopes: Tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.send",)
    sheets_scopes: Tuple[str, ...] = (
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
    )
    followup_scopes: Tuple[str, ...] = (
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
    )


# ============================================================
# DATABASE
# ============================================================
@dataclass(frozen=True)
class DatabaseSettings:
    enabled: bool = False
    url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/gmail_automation"


# ============================================================
# FOLLOW-UPS
# ============================================================
@dataclass(frozen=True)
class FollowupSettings:
    # Days to wait before follow-up 1, 2, ...   (0, 0) = no waiting.
    # The number of items is also the maximum number of follow-ups per mail.
    wait_days: Tuple[int, ...] = (0, 0)

    # ONE template (inside templates/) used for every follow-up mail
    template: str = "Followup_1.html"

    # "reply"    -> subject "Re: <original subject>"  (stays in the Gmail thread)
    # "template" -> subject from the template's  <!-- SUBJECT: ... -->  line
    subject_mode: str = "reply"

    attachments: Tuple[str, ...] = ()          # e.g. (resume_path,) to attach again

    # Columns written back into the Excel / Google Sheet (created if missing)
    reply_status_column: str = "Reply_Status"
    reply_yes_value: str = "Yes"
    bounce_value: str = "Bounced"
    followup_status_column: str = "Followup_Status"      # "" = do not write

    check_delay_seconds: float = 0.2
    send_delay_seconds: float = 2


# ============================================================
# ALL SETTINGS
# ============================================================
@dataclass(frozen=True)
class Settings:
    paths: PathSettings = field(default_factory=PathSettings)
    columns: ColumnSettings = field(default_factory=ColumnSettings)
    source: SourceSettings = field(default_factory=SourceSettings)
    sending: SendingSettings = field(default_factory=SendingSettings)
    google: GoogleSettings = field(default_factory=GoogleSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    followup: FollowupSettings = field(default_factory=FollowupSettings)


def load_settings() -> Settings:
    """Defaults above + overrides from environment / .env (DATABASE_URL, USE_DB, DATA_SOURCE)."""

    if load_dotenv:
        load_dotenv(os.path.join(BASE_DIR, ".env"))

    paths = PathSettings()

    google = GoogleSettings(
        credentials_file=paths.secret("credentials.json"),
        gmail_token_file=paths.secret("token.json"),
        sheets_token_file=paths.secret("token_sheets.json"),
        followup_token_file=paths.secret("token_followup.json"),
    )

    database = DatabaseSettings(
        enabled=os.getenv("USE_DB", "false").strip().lower() == "true",
        url=os.getenv("DATABASE_URL", DatabaseSettings.url),
    )

    source = SourceSettings(
        kind=os.getenv("DATA_SOURCE", SourceSettings.kind).strip().lower(),
    )

    return Settings(paths=paths, google=google, database=database, source=source)
