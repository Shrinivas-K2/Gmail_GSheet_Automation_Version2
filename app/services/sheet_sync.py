"""
sheet_sync.py

Writes values (Reply_Status, Followup_Status ...) back into the ORIGINAL Excel
file / Google Sheet a mail was sent from.

Rows are found by e-mail address (not only the row number), so it still
works if the sheet was sorted or rows were inserted after sending.
Queue writes with queue(), then write them all with flush().
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from app.domain.entities import SentMailRecord, SheetLocation, SheetSchema
from app.domain.interfaces import Reporter, SheetGatewayFactory


@dataclass
class SyncReport:
    written: int = 0
    unchanged: int = 0
    problems: List[str] = field(default_factory=list)


class SheetSyncService:

    def __init__(self, gateways: SheetGatewayFactory, schema: SheetSchema, reporter: Reporter,
                 enabled: bool = True):
        self._gateways = gateways
        self._schema = schema
        self._reporter = reporter
        self.enabled = enabled
        # (location, tab) -> [(record, column, value)]
        self._pending: Dict[Tuple[SheetLocation, str], List[tuple]] = defaultdict(list)

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def queue(self, record: SentMailRecord, column: str, value: str) -> None:
        if not self.enabled or not column:
            return
        self._pending[(record.location, record.sheet_tab)].append((record, column, value))

    def flush(self) -> SyncReport:

        report = SyncReport()

        for (location, tab), items in self._pending.items():

            gateway = None

            try:
                gateway = self._gateways.create(location)
                gateway.open_tab(tab)

                self._reporter.info(f"  Sheet: {gateway.describe()}  /  tab '{tab}'")

                index = defaultdict(list)       # e-mail (lower-case) -> [row numbers]
                by_number = {}                  # row number -> row
                for row in gateway.read_rows():
                    index[self._schema.email_of(row).lower()].append(row.number)
                    by_number[row.number] = row

                for record, column, value in items:

                    numbers = index.get(record.to_email.lower(), [])

                    if not numbers:
                        report.problems.append(
                            f"{record.to_email} not found in {location.workbook_name} / "
                            f"tab '{tab}' (is the Email column / tab name still the same?)")
                        continue

                    # prefer the row number saved at send time if it still matches
                    number = (record.sheet_row_number
                              if record.sheet_row_number in numbers else numbers[0])

                    current = by_number[number].text(column)
                    if current and current == str(value).strip():
                        report.unchanged += 1          # already correct
                        continue

                    gateway.write_cell(number, column, value)
                    report.written += 1
                    self._reporter.info(f"    row {number}: {record.to_email}  ->  {column} = {value}")

                gateway.save()

            except Exception as e:
                report.problems.append(f"{location.workbook_name} / {tab}: {e}")

            finally:
                if gateway is not None:
                    try:
                        gateway.close()
                    except Exception:
                        pass

        self._pending.clear()
        return report
