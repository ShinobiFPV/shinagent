"""
IMQ2 Google Sheets Integration
Create spreadsheets, write data, read ranges, and get shareable links.
Useful for exporting purchase history, tracking data, or any tabular output.
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)


def create_spreadsheet(title: str, share: bool = True) -> dict:
    """Create a new spreadsheet. Returns dict with id, url."""
    from integrations.google_services import get_google_service
    sheets = get_google_service("sheets")
    drive  = get_google_service("drive")

    resp = sheets.spreadsheets().create(
        body={"properties": {"title": title}}
    ).execute()

    ss_id = resp["spreadsheetId"]
    url   = resp["spreadsheetUrl"]

    if share:
        drive.permissions().create(
            fileId=ss_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

    log.info(f"Created spreadsheet '{title}' ({ss_id})")
    return {"id": ss_id, "url": url, "title": title}


def write_rows(spreadsheet_id: str, rows: list[list],
               sheet_name: str = "Sheet1",
               start_cell: str = "A1") -> int:
    """
    Write a list of rows to a spreadsheet.
    Each row is a list of values (str, int, float).
    Returns number of rows written.
    """
    from integrations.google_services import get_google_service
    sheets = get_google_service("sheets")

    range_name = f"{sheet_name}!{start_cell}"
    body = {"values": [[str(v) if v is not None else "" for v in row] for row in rows]}

    result = sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()

    updated = result.get("updatedRows", 0)
    log.info(f"Wrote {updated} rows to spreadsheet {spreadsheet_id}")
    return updated


def read_rows(spreadsheet_id: str, range_name: str = "Sheet1") -> list[list]:
    """Read rows from a spreadsheet range."""
    from integrations.google_services import get_google_service
    sheets = get_google_service("sheets")

    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name,
    ).execute()

    return result.get("values", [])


def append_rows(spreadsheet_id: str, rows: list[list],
                sheet_name: str = "Sheet1") -> int:
    """Append rows to the end of existing data."""
    from integrations.google_services import get_google_service
    sheets = get_google_service("sheets")

    body = {"values": [[str(v) if v is not None else "" for v in row] for row in rows]}
    result = sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()

    updated = result.get("updates", {}).get("updatedRows", 0)
    log.info(f"Appended {updated} rows to spreadsheet {spreadsheet_id}")
    return updated


def create_and_fill(title: str, headers: list[str],
                    rows: list[list], share: bool = True) -> dict:
    """
    High-level: create a spreadsheet, write headers + rows, return url.
    """
    ss = create_spreadsheet(title=title, share=share)
    write_rows(ss["id"], [headers] + rows)
    return ss
