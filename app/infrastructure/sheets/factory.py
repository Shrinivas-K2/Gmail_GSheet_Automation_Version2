"""
sheets/factory.py

Creates the right SheetGateway for a SheetLocation  (Factory pattern).

Each data source is REGISTERED here. Adding a new one (CSV, Airtable ...)
means writing one new gateway class and one register() call - the services
do not change (Open/Closed principle).
"""

import os
from typing import Callable, Dict, Optional, Tuple

from app.config.settings import PathSettings, SourceSettings
from app.domain.entities import SheetLocation
from app.domain.interfaces import SheetGateway, SheetGatewayFactory
from app.infrastructure.google.oauth import GoogleOAuth
from app.infrastructure.sheets.excel_gateway import ExcelSheetGateway
from app.infrastructure.sheets.google_gateway import GoogleSheetGateway

Builder = Callable[[SheetLocation], SheetGateway]
DefaultLocation = Callable[[Optional[str]], SheetLocation]


class RegistrySheetGatewayFactory(SheetGatewayFactory):

    def __init__(self, default_kind: str):
        self._default_kind = default_kind
        self._builders: Dict[str, Tuple[Builder, DefaultLocation]] = {}

    def register(self, kind: str, builder: Builder, default_location: DefaultLocation) -> None:
        self._builders[kind] = (builder, default_location)

    def create(self, location: SheetLocation) -> SheetGateway:
        return self._entry(location.kind)[0](location)

    def default_location(self, kind: Optional[str] = None,
                         workbook: Optional[str] = None) -> SheetLocation:
        return self._entry(kind or self._default_kind)[1](workbook)

    def _entry(self, kind: str):
        if kind not in self._builders:
            raise ValueError(f"Unknown data source '{kind}'. Use one of: {', '.join(self._builders)}")
        return self._builders[kind]


def build_sheet_factory(paths: PathSettings, source: SourceSettings,
                        sheets_oauth: GoogleOAuth) -> RegistrySheetGatewayFactory:
    """Registers the two data sources the project ships with."""

    factory = RegistrySheetGatewayFactory(source.kind)

    # ------------------------------------------------ Excel
    def excel_location(workbook: Optional[str]) -> SheetLocation:
        name = workbook or os.path.basename(paths.excel_file)
        if not name.lower().endswith((".xlsx", ".xlsm")):
            name += ".xlsx"
        return SheetLocation("excel", name)

    def excel_builder(location: SheetLocation) -> SheetGateway:
        name = location.workbook_name

        if os.path.isabs(name):
            path = name
        elif name == os.path.basename(paths.excel_file):
            path = paths.excel_file                      # the configured file
        else:
            path = os.path.join(paths.data_dir, name)

        return ExcelSheetGateway(path, location)

    factory.register("excel", excel_builder, excel_location)

    # ------------------------------------------------ Google Sheets
    def google_location(workbook: Optional[str]) -> SheetLocation:
        if workbook:
            return SheetLocation("google_sheet", workbook, None)
        return SheetLocation("google_sheet", source.google_spreadsheet_name,
                             source.google_spreadsheet_id or None)

    def google_builder(location: SheetLocation) -> SheetGateway:
        return GoogleSheetGateway(sheets_oauth, location.workbook_name, location.spreadsheet_id)

    factory.register("google_sheet", google_builder, google_location)

    return factory
