# Gmail Automation

Send personalised mails from an **Excel file or Google Sheet**, track everything in **PostgreSQL**,
check **who replied**, and send **follow-ups inside the same Gmail thread**.

```
python main.py HPE                          # send the first mails (tab "HPE")
python followup_main.py                     # check replies + send follow-ups
```

---------------------------------------------------------------------------------------------------

## 1. Architecture

```
            ┌──────────────────────────  main.py / followup_main.py  ───────────────────────────┐
            │                         (thin entry points, no logic)                              │
            └───────────────────────────────────────┬────────────────────────────────────────────┘
                                                    ▼
   app/cli/            parse the command line, ask nothing, decide nothing
                                                    ▼
   app/container.py    COMPOSITION ROOT - the only place that picks concrete classes
                                                    ▼
   ┌────────────────────────────────────────  app/services  ──────────────────────────────────────────┐
   │  CampaignService      FollowupService      SheetSyncService          (use cases / workflows)      │
   │  CampaignComposer     FollowupComposer     ThreadAnalyzer + rules    WaitDaysPolicy               │
   │  RetryingSender       PlaceholderRenderer  EmailValidator                                         │
   └───────────────────────────────────────────────┬────────────────────────────────────────────────────┘
                                                   │ depends ONLY on interfaces
                                                   ▼
   ┌────────────────────────────────────────  app/domain  ────────────────────────────────────────────┐
   │  entities.py   SheetRow, OutgoingMail, MailThread, ReplyAnalysis, SentMailRecord ... (pure data)   │
   │  interfaces.py MailSender, ThreadReader, SheetGateway, SendTracker, FollowupRepository, Clock ... │
   └───────────────────────────────────────────────▲────────────────────────────────────────────────────┘
                                                   │ implements
   ┌────────────────────────────────────  app/infrastructure  ────────────────────────────────────────┐
   │  google/       GoogleOAuth, GmailGateway, MimeMessageBuilder                                      │
   │  sheets/       ExcelSheetGateway, GoogleSheetGateway, RegistrySheetGatewayFactory                 │
   │  persistence/  Database, ORM tables, SqlSendTracker, SqlFollowupRepository, NullSendTracker       │
   │  files.py      FileTemplateRepository, FileActivityLog          console.py   clock.py             │
   └────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Dependency rule:** arrows only point inward. `services` never import Gmail, Excel or SQLAlchemy;
`domain` imports nothing from the project's outer layers.

## 2. Where each SOLID principle is visible

| Principle | In this code |
|---|---|
| **S**ingle responsibility | One reason to change per class: `ThreadAnalyzer` decides what counts as a reply, `WaitDaysPolicy` decides *when*, `FollowupComposer` builds the mail, `GmailGateway` is the only class that knows Gmail, `SheetSyncService` only writes cells back. CLIs only parse arguments. |
| **O**pen / closed | New reply type → add a `ReplyRule` class. New data source (CSV…) → one gateway class + one `factory.register(...)`. New follow-up timing → a new `FollowupPolicy`. Nothing existing is edited (`tests/test_small_services.py` adds a rule without touching the analyzer). |
| **L**iskov substitution | `ExcelSheetGateway` and `GoogleSheetGateway` are interchangeable behind `SheetGateway`; `NullSendTracker` and `SqlSendTracker` behind `SendTracker`; real and fake Gmail behind `MailSender`. Services behave the same with any of them. |
| **I**nterface segregation | Small contracts: `MailSender` (send) is separate from `ThreadReader` (read), `RowReader` from `CellWriter`, `Connectable` from both. A class only depends on what it uses. |
| **D**ependency inversion | Services take abstractions through their constructors; `container.py` wires the real ones. Tests plug in fakes (`tests/fakes.py`): no Gmail, no Excel, no database needed. Even time is injected (`Clock`). |

## 3. Design patterns used (and why)

| Pattern | Where | Why |
|---|---|---|
| Dependency injection / composition root | `container.py` | one place to change wiring |
| Factory + registry | `sheets/factory.py` | pick Excel / Google by name, extend without edits |
| Strategy | `ReplyRule` classes, `FollowupPolicy` | swap business rules |
| Decorator | `RetryingSender` | adds retries to any sender |
| Proxy (fail-safe) | `persistence/safe.py` | a database error never stops sending |
| Null object | `NullSendTracker` | no `if database_enabled` scattered around |
| Repository | `SqlFollowupRepository`, `SqlSendTracker` | services see objects, not SQL |
| Adapter | `GmailGateway` converts Gmail JSON to our `ThreadMessage` | Gmail details stay in one file |
| Value objects | frozen dataclasses in `entities.py` | no loose dictionaries, typos fail early |

## 4. Folder structure

```
Gmail_Automation/
├── main.py                  send the first mails
├── followup_main.py         check replies + follow-ups
├── app/
│   ├── config/settings.py   ALL settings (replaces config.py + followup_config.py)
│   ├── domain/              entities, interfaces, errors
│   ├── services/            use cases and business rules
│   ├── infrastructure/      Gmail, sheets, database, files, console
│   ├── cli/                 command-line parsing
│   └── container.py         wiring
├── templates/               *.html mail templates (first mails AND Followup_1/2.html)
├── data/                    Cold_Mail.xlsx, resume PDFs         (git-ignored)
├── secrets/                 credentials.json, token*.json       (git-ignored)
├── logs/                    mail_log.txt                        (git-ignored)
├── sql/schema.sql           optional: all 7 tables
└── tests/                   28 tests (unit + end-to-end)
```

## 5. Moving from the old layout

1. Copy your old `data/`, `logs/` and `templates/` into this project (templates here already include the old ones + `Followup_*.html`).
2. Put `credentials.json`, `token.json`, `token_sheets.json`, `token_followup.json` into `secrets/`
   (if they are still in the project root they are found there too, so nothing breaks).
3. Keep your `.env` (`USE_DB`, `DATABASE_URL`). Same database, same tables: nothing to migrate.
4. `pip install -r requirements.txt`, then run as before.

| Old | New |
|---|---|
| `config.py`, `Followup/followup_config.py` | `app/config/settings.py` |
| `python main.py ...` | unchanged |
| `python Followup/main.py ...` | `python followup_main.py ...` (same options) |
| `Followup/templates/` | `templates/` |

## 6. Usage

```
python main.py                                        # defaults from settings
python main.py HPE Generic.html
python main.py HPE --source google_sheet --workbook Cold_Mail

python followup_main.py                               # asks which company / tab
python followup_main.py --all --dry-run               # preview, nothing is sent or written
python followup_main.py -c Rapido -c HPE
python followup_main.py -e someone@company.com
python followup_main.py --check-only                  # replies only, send nothing
python followup_main.py --template Followup_2.html --limit 1
python followup_main.py --sync-sheet --all            # copy known replies to the sheet again
```

## 7. How to extend

| I want to… | Do this |
|---|---|
| Recognise a new kind of reply | New class in `services/reply_analysis.py` (`ReplyRule`), add it to the list in `ThreadAnalyzer` |
| Read contacts from a CSV | New `CsvSheetGateway(SheetGateway)` + `factory.register("csv", ...)` in `sheets/factory.py` |
| Follow up only on weekdays | New `FollowupPolicy`, pass it in `container.py` |
| Use another mail provider | New class implementing `MailSender` / `ThreadReader`, use it in `container.py` |
| Change waiting days, template, column names | `app/config/settings.py` |

## 8. Tests

```
pip install -r requirements-dev.txt
python -m pytest -q
```
Unit tests use fakes; `tests/test_end_to_end.py` runs both command lines on real temporary Excel files
and a real SQLite database with only Gmail faked.
