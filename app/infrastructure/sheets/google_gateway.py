"""
sheets/google_gateway.py

SheetGateway for a Google Spreadsheet (Sheets API v4, OAuth login).
Every cell is written to Google immediately (so a crash never loses the
"Sent" marks).
"""

from typing import Dict, List, Optional

from googleapiclient.discovery import build

from app.domain.entities import SheetLocation, SheetRow
from app.domain.interfaces import SheetGateway
from app.infrastructure.google.oauth import GoogleOAuth


def _column_letter(number: int) -> str:
    """1 -> A, 2 -> B, 27 -> AA ..."""
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _quote(text: str) -> str:
    """Escape a single quote for A1 ranges and Drive queries."""
    return text.replace("'", "''")


class GoogleSheetGateway(SheetGateway):

    def __init__(self, oauth: GoogleOAuth, spreadsheet_name: str,
                 spreadsheet_id: Optional[str] = None):
        self._oauth = oauth
        self._name = spreadsheet_name
        self._id = spreadsheet_id or None
        self._sheets = None
        self._tab: Optional[str] = None
        self._values: List[list] = []
        self._header_map: Dict[str, int] = {}

    @property
    def location(self) -> SheetLocation:
        return SheetLocation("google_sheet", self._name, self._id)

    def describe(self) -> str:
        return f"Google Spreadsheet '{self._name}'"

    # ---- connection
    def _api(self):
        if self._sheets is None:
            self._sheets = build("sheets", "v4", credentials=self._oauth.credentials())
        return self._sheets

    def _resolve_id(self) -> str:
        """Find the spreadsheet id from its NAME (Drive API) unless it was given."""

        if self._id:
            return self._id

        drive = build("drive", "v3", credentials=self._oauth.credentials())
        query = (f"name = '{_quote(self._name)}' "
                 "and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false")
        files = drive.files().list(q=query, fields="files(id, name)").execute().get("files", [])

        if not files:
            raise Exception(f"Google Spreadsheet '{self._name}' not found.")
        if len(files) > 1:
            raise Exception(f"{len(files)} spreadsheets are named '{self._name}'. "
                            f"Set google_spreadsheet_id in settings.")

        self._id = files[0]["id"]
        return self._id

    # ---- RowReader
    def open_tab(self, tab: str) -> None:

        self._resolve_id()

        meta = self._api().spreadsheets().get(
            spreadsheetId=self._id, fields="sheets.properties.title").execute()

        if tab not in [s["properties"]["title"] for s in meta["sheets"]]:
            raise Exception(f"Sheet '{tab}' not found.")

        self._tab = tab
        self._values = self._api().spreadsheets().values().get(
            spreadsheetId=self._id, range=f"'{_quote(tab)}'").execute().get("values", [])

        header_row = self._values[0] if self._values else []
        self._header_map = {str(v).strip(): c for c, v in enumerate(header_row, start=1)
                            if str(v).strip()}

    def read_rows(self) -> List[SheetRow]:

        rows = []

        for number, cells in enumerate(self._values[1:], start=2):

            values = {}
            for header, column in self._header_map.items():
                # Google drops trailing empty cells, so the list can be short
                value = cells[column - 1] if column - 1 < len(cells) else None
                if value is not None and str(value).strip() == "":
                    value = None                       # empty cell -> None, like Excel
                values[header] = value

            if all(v is None for v in values.values()):
                continue

            rows.append(SheetRow(number, values))

        return rows

    # ---- CellWriter
    def write_cell(self, row_number: int, column: str, value) -> None:

        if column not in self._header_map:
            new_col = max(self._header_map.values(), default=0) + 1
            self._put(1, new_col, column)
            self._header_map[column] = new_col

        self._put(row_number, self._header_map[column], value)

    def _put(self, row: int, column: int, value) -> None:
        cell = f"'{_quote(self._tab)}'!{_column_letter(column)}{row}"
        self._api().spreadsheets().values().update(
            spreadsheetId=self._id, range=cell,
            valueInputOption="RAW", body={"values": [[value]]}).execute()

    def save(self) -> None:
        pass            # nothing to do: every cell is already written

    def close(self) -> None:
        self._sheets = None
