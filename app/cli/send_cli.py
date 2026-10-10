"""
app/cli/send_cli.py

Command line for SENDING the first mails.   (python main.py ...)

Usage:
    python main.py
    python main.py HPE
    python main.py HPE Generic.html
    python main.py --sheet-name HPE --email-template Generic.html
    python main.py HPE --source google_sheet
    python main.py HPE --source google_sheet --workbook Cold_Mail
"""

import argparse
from typing import List, Optional

from app.container import Container


def parse_arguments(argv: Optional[List[str]], container: Container):

    parser = argparse.ArgumentParser(description="Send Gmail messages using Excel / Google Sheet data.")

    parser.add_argument("sheet_name", nargs="?", help="Sheet tab to process. Uses the default in settings if omitted.")
    parser.add_argument("email_template", nargs="?", help="Template file from the templates folder.")
    parser.add_argument("-s", "--sheet-name", dest="sheet_name_option", help="Sheet tab to process.")
    parser.add_argument("-t", "--email-template", dest="email_template_option", help="Template file from the templates folder.")
    parser.add_argument("--source", choices=["excel", "google_sheet"],
                        help="Where to read contacts from. Uses the setting if omitted.")
    parser.add_argument("-w", "--workbook",
                        help="Excel file name (in data/) or Google Spreadsheet name. Uses the setting if omitted.")

    args = parser.parse_args(argv)
    sending = container.settings.sending

    tabs = args.sheet_name_option or args.sheet_name or list(sending.default_sheets)
    template = args.email_template_option or args.email_template or sending.default_template

    if isinstance(tabs, str):
        tabs = [tabs]

    return tabs, template, args.source, args.workbook


def main(argv: Optional[List[str]] = None, container: Optional[Container] = None) -> None:

    container = container or Container()
    out = container.reporter

    out.info("=" * 70)
    out.info(" Gmail Bulk Email Automation ")
    out.info("=" * 70)

    tabs, template, source, workbook = parse_arguments(argv, container)
    location = container.sheet_gateways.default_location(source, workbook)

    out.info(f"\nReading Sheet : {tabs}")
    out.info(f"Email Template: {template}")

    service = container.campaign_service()

    out.info("\nAuthenticating Gmail...")
    container.sending_gmail.connect()
    out.info("Authentication Successful.\n")

    service.run(location, tabs, template)
