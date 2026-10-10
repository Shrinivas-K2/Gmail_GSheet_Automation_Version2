"""
app/cli/followup_cli.py

Command line for REPLY CHECKING + FOLLOW-UPS.   (python followup_main.py ...)

Examples
--------
    python followup_main.py                           # asks which company / tab, then runs
    python followup_main.py --all                     # every sent mail, no question
    python followup_main.py -c Rapido                 # one company / sheet tab
    python followup_main.py -c Rapido -c HPE          # several
    python followup_main.py -e ravi@rapido.com        # one person
    python followup_main.py --check-only              # only check replies, send nothing
    python followup_main.py --dry-run                 # show what WOULD happen, change nothing
    python followup_main.py --template Followup_2.html
    python followup_main.py --sync-sheet --all        # re-write Reply_Status for known replies
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import List, Optional

from app.container import Container
from app.domain.entities import MailFilter
from app.domain.errors import DatabaseUnavailableError
from app.services.followup_service import FollowupOptions


def _split(values: List[str]) -> tuple:
    """['a,b', 'c'] -> ('a', 'b', 'c')"""
    return tuple(x.strip() for v in values for x in v.split(",") if x.strip())


def parse_arguments(argv: Optional[List[str]]):

    p = argparse.ArgumentParser(description="Check replies and send follow-up mails.")

    p.add_argument("-c", "--company", action="append", default=[],
                   help="Company or sheet-tab name (repeat or use commas for several)")
    p.add_argument("-e", "--email", action="append", default=[],
                   help="Only this recipient e-mail (repeat or use commas)")
    p.add_argument("--run-id", type=int, help="Only mails sent in this run id (runs table)")
    p.add_argument("--since", help="Only mails sent on/after this date (YYYY-MM-DD)")
    p.add_argument("--all", action="store_true", help="All sent mails, do not ask which company")

    p.add_argument("--days", type=int, help="Wait this many days before every follow-up (overrides settings)")
    p.add_argument("--max-followups", type=int, help="Never send more than this many follow-ups per mail")
    p.add_argument("--template", help="Follow-up template file (overrides settings)")
    p.add_argument("--limit", type=int, help="Send at most this many follow-ups in this run")

    p.add_argument("--check-only", action="store_true", help="Check replies, send nothing")
    p.add_argument("--dry-run", action="store_true", help="Show what would happen. Nothing is sent or written.")
    p.add_argument("--sync-sheet", action="store_true",
                   help="Only copy known replies/bounces from the database to the sheet")
    p.add_argument("--no-sheet-update", action="store_true", help="Do not write to the Excel / Google Sheet")
    p.add_argument("-y", "--yes", action="store_true", help="Do not ask before sending")

    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None, container: Optional[Container] = None,
         interactive: Optional[bool] = None) -> None:

    args = parse_arguments(argv)
    container = container or Container()
    out = container.reporter
    interactive = sys.stdin.isatty() if interactive is None else interactive

    out.info("=" * 70)
    out.info(" Gmail Follow-up Automation ")
    out.info("=" * 70)

    mail_filter = MailFilter(
        companies=_split(args.company),
        emails=_split(args.email),
        run_id=args.run_id,
        since=(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
               if args.since else None),
    )

    service = container.followup_service(
        days=args.days, max_followups=args.max_followups,
        update_sheets=not (args.no_sheet_update or args.dry_run))

    options = FollowupOptions(
        mail_filter=mail_filter,
        ask_scope=interactive and not args.all,
        interactive=interactive,
        check_only=args.check_only,
        dry_run=args.dry_run,
        sync_only=args.sync_sheet,
        assume_yes=args.yes,
        template=args.template,
        limit=args.limit,
    )

    try:
        service.run(options)
    except DatabaseUnavailableError as e:
        out.error(f"Cannot connect to the database: {e}")
        out.error("Check DATABASE_URL in your .env file.")
