-- =====================================================================
-- COMPLETE SCHEMA (7 tables). Optional: the program creates missing tables
-- automatically on its first run. Safe to re-run (IF NOT EXISTS everywhere).
--
--   1) CREATE DATABASE gmail_automation;                 (once, as postgres)
--   2) psql -U postgres -d gmail_automation -f sql/schema.sql
-- =====================================================================

-- =====================================================================
-- Gmail Automation : PostgreSQL schema  (first-mail tables)
--
-- 1) Create the database (run this separately, connected to "postgres"):
--        CREATE DATABASE gmail_automation;
--
-- 2) Connect to gmail_automation, then run this file:
--        psql -U postgres -d gmail_automation -f schema.sql
--    (or paste it into the pgAdmin / DBeaver Query Tool)
--
-- Safe to re-run: every statement uses IF NOT EXISTS.
-- Matches models.py exactly, so the Python code and this file agree.
-- =====================================================================


-- =====================================================================
-- 1. RUNS : one row per execution of main.py for one sheet tab
-- =====================================================================
CREATE TABLE IF NOT EXISTS runs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,                       -- NULL = run did not finish

    data_source     TEXT NOT NULL,                     -- excel | google_sheet
    workbook_name   TEXT,                              -- Cold_Mail.xlsx / Google spreadsheet name
    spreadsheet_id  TEXT,                              -- Google only (NULL for Excel)
    sheet_tab       TEXT NOT NULL,                     -- Rapido, HPE ...
    email_template  TEXT NOT NULL,                     -- Generic.html

    total_count     INTEGER NOT NULL DEFAULT 0,
    sent_count      INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    skipped_count   INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT ck_runs_source CHECK (data_source IN ('excel','google_sheet'))
);


-- =====================================================================
-- 2. CONTACTS : one row per person (unique by email, case-insensitive)
-- =====================================================================
CREATE TABLE IF NOT EXISTS contacts (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       TEXT NOT NULL,
    name        TEXT,
    company     TEXT,
    role        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- lower() so "A@x.com" and "a@x.com" count as the same person
CREATE UNIQUE INDEX IF NOT EXISTS ux_contacts_email
    ON contacts (lower(email));


-- =====================================================================
-- 3. EMAILS : one row per email processed (main tracking table)
-- =====================================================================
CREATE TABLE IF NOT EXISTS emails (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    run_id            BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    contact_id        BIGINT REFERENCES contacts(id),  -- NULL if email cell was blank

    -- Gmail send response (NULL when the send failed / was not attempted)
    gmail_message_id  TEXT,
    gmail_thread_id   TEXT,

    -- What was sent
    to_email          TEXT NOT NULL,
    cc_emails         TEXT[],
    bcc_emails        TEXT[],
    subject           TEXT,
    email_template    TEXT,
    attachments       TEXT[],                          -- file names only

    -- Result
    status            TEXT NOT NULL,                   -- sent | failed | invalid_email
    error_message     TEXT,
    attempts          INTEGER NOT NULL DEFAULT 1,
    sent_at           TIMESTAMPTZ,                     -- only when status = 'sent'
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Where it came from in the sheet
    sheet_tab         TEXT,
    sheet_row_number  INTEGER,
    row_snapshot      JSONB,                           -- whole row as it was at send time

    CONSTRAINT ck_emails_status
        CHECK (status IN ('sent','failed','invalid_email'))
);

CREATE INDEX IF NOT EXISTS ix_emails_run      ON emails (run_id);
CREATE INDEX IF NOT EXISTS ix_emails_contact  ON emails (contact_id);
CREATE INDEX IF NOT EXISTS ix_emails_thread   ON emails (gmail_thread_id);

-- A Gmail message id can be stored only once (failed rows have NULL, so they are ignored)
CREATE UNIQUE INDEX IF NOT EXISTS ux_emails_gmail_msg
    ON emails (gmail_message_id)
    WHERE gmail_message_id IS NOT NULL;


-- =====================================================================
-- 4. LOGS : every event (database version of mail_log.txt)
-- =====================================================================
CREATE TABLE IF NOT EXISTS logs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    level       TEXT NOT NULL DEFAULT 'INFO',          -- DEBUG | INFO | WARNING | ERROR
    event       TEXT NOT NULL,                         -- RUN_STARTED, SENT, FAILED, INVALID_EMAIL,
                                                       -- SKIPPED, SHEET_ERROR, RUN_FINISHED
    message     TEXT,

    run_id      BIGINT REFERENCES runs(id)   ON DELETE CASCADE,
    email_id    BIGINT REFERENCES emails(id) ON DELETE SET NULL,

    name        TEXT,
    to_email    TEXT,
    details     JSONB,                                 -- e.g. {"attempts": 3, "errors": [...]}

    CONSTRAINT ck_logs_level
        CHECK (level IN ('DEBUG','INFO','WARNING','ERROR'))
);

CREATE INDEX IF NOT EXISTS ix_logs_run    ON logs (run_id);
CREATE INDEX IF NOT EXISTS ix_logs_email  ON logs (email_id);
CREATE INDEX IF NOT EXISTS ix_logs_time   ON logs (logged_at);


-- =====================================================================
-- Quick check after running (should list 4 tables):
--     \dt
--   or
--     SELECT table_name FROM information_schema.tables
--     WHERE table_schema = 'public' ORDER BY 1;
-- =====================================================================

-- =====================================================================
-- FOLLOW-UP TABLES
-- =====================================================================
-- 1) Reply status + counters, one row per sent mail
CREATE TABLE IF NOT EXISTS email_replies (
    email_id          BIGINT PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
    reply_received    BOOLEAN NOT NULL DEFAULT false,
    reply_type        TEXT,                          -- reply | auto_reply | bounce
    replied_at        TIMESTAMPTZ,
    reply_from        TEXT,
    reply_snippet     TEXT,
    last_checked_at   TIMESTAMPTZ,
    followup_count    INTEGER NOT NULL DEFAULT 0,
    last_followup_at  TIMESTAMPTZ
);

-- 2) One row per run of Followup/main.py
CREATE TABLE IF NOT EXISTS followup_runs (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    mode              TEXT NOT NULL,                 -- normal | check_only | sync_sheet
    filters           JSONB,
    checked_count     INTEGER NOT NULL DEFAULT 0,
    replied_count     INTEGER NOT NULL DEFAULT 0,
    bounced_count     INTEGER NOT NULL DEFAULT 0,
    followups_sent    INTEGER NOT NULL DEFAULT 0,
    followups_failed  INTEGER NOT NULL DEFAULT 0
);

-- 3) One row per follow-up mail
CREATE TABLE IF NOT EXISTS followups (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email_id          BIGINT NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    followup_run_id   BIGINT REFERENCES followup_runs(id) ON DELETE SET NULL,
    followup_number   INTEGER NOT NULL,
    to_email          TEXT NOT NULL,
    subject           TEXT,
    template          TEXT,
    gmail_message_id  TEXT,
    gmail_thread_id   TEXT,
    status            TEXT NOT NULL,
    error_message     TEXT,
    attempts          INTEGER NOT NULL DEFAULT 1,
    sent_at           TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_followups_status CHECK (status IN ('sent','failed'))
);

CREATE INDEX IF NOT EXISTS ix_followups_email  ON followups (email_id);
CREATE INDEX IF NOT EXISTS ix_followups_thread ON followups (gmail_thread_id);
