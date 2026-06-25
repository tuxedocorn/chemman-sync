"""
Chem-Man → Smartsheet Spray Application Sync
Pulls posted field applications from Chem-Man and pushes to Smartsheet.
"""

import os
import csv
import io
import time
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
DAYS_BACK = 7

CHEMMAN_STORE   = os.getenv("CHEMMAN_STORE")       # RizeDrone
CHEMMAN_USER    = os.getenv("CHEMMAN_USER")         # rizedrone
CHEMMAN_PASS    = os.getenv("CHEMMAN_PASS")

SMARTSHEET_TOKEN = os.getenv("SMARTSHEET_TOKEN")
SHEET_NAME       = "2026 Chem-Man Github Import"

CHEMMAN_BASE  = "https://login.chem-man.com"
LOGIN_URL     = f"{CHEMMAN_BASE}/xhr/StoreUser.xhrLogin"
REPORT_URL    = f"{CHEMMAN_BASE}/reportPostedFieldApplications.php"

SS_BASE    = "https://api.smartsheet.com/2.0"
SS_HEADERS = {
    "Authorization": f"Bearer {SMARTSHEET_TOKEN}",
    "Content-Type": "application/json"
}

# ── Columns to pull from CSV (CSV header → Smartsheet column name) ────────────
COLUMN_MAP = {
    "Load Nbr":                             "Load Nbr",
    "Transaction Date":                     "Transaction Date",
    "Location":                             "Location ID",
    "Location Description":                 "Location Description",
    "Location Applied Acres":               "Applied Acres",
    "Location Crop":                        "Crop",
    "Chemical / Charge Nickname":           "Chemical Nickname",
    "Chemical / Charge Description":        "Chemical Description",
    "Chemical / Charge Applied Rate":       "Applied Rate",
    "Chemical / Charge Applied Unit":       "Applied Unit",
    "Chemical / Charge Total Applied":      "Total Applied",
    "Chemical / Charge Total Applied in Base Units": "Total Applied (Base)",
    "Chemical / Charge Total Applied Base Unit":     "Total Applied Base Unit",
    "Applicator First Name":                "Applicator First",
    "Applicator Last Name":                 "Applicator Last",
    "Applicator Vehicle Description":       "Vehicle",
    "Application Date":                     "Application Date",
    "Application Start Time":               "Start Time",
    "Application End Time":                 "End Time",
    "Temperature Start":                    "Temp Start",
    "Temperature End":                      "Temp End",
    "Wind MPH Start":                       "Wind MPH Start",
    "Wind MPH End":                         "Wind MPH End",
    "Wind Direction Start":                 "Wind Dir Start",
    "Wind Direction End":                   "Wind Dir End",
    "Humidity Start":                       "Humidity Start",
    "Humidity End":                         "Humidity End",
    "Status":                               "Status",
}

DEDUP_COL = "Dedup Key"

COLUMN_DEFS = [{"title": name, "type": "TEXT_NUMBER", "primary": False} for name in COLUMN_MAP.values()]
COLUMN_DEFS[0]["primary"] = True
COLUMN_DEFS.append({"title": DEDUP_COL, "type": "TEXT_NUMBER"})


# ── Chem-Man Auth + Download ──────────────────────────────────────────────────
def get_chemman_csv():
    """Log into Chem-Man, download the last DAYS_BACK days as CSV, return parsed rows."""
    session = requests.Session()

    # Login
    login_payload = {
        "metadata": '{"screen":{"width":1920,"height":1080},"viewport":{"width":1920,"height":1080}}',
        "storelogin": CHEMMAN_STORE,
        "username":   CHEMMAN_USER,
        "password":   CHEMMAN_PASS,
    }
    resp = session.post(LOGIN_URL, data=login_payload)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("valid") and not result.get("success") and result.get("result") != "success":
        raise Exception(f"Chem-Man login failed: {result}")
    print("✓ Chem-Man auth successful")

    # Build date range
    date_to   = datetime.today()
    date_from = date_to - timedelta(days=DAYS_BACK)
    fmt = "%Y-%m-%d"

    params = {
        "download": "csv",
        "go": "report",
        "posted": "1",
        "customerId": "",
        "growerId": "",
        "stateSearch": "",
        "countySearch": "",
        "customerTypeAnyOrAll": "all",
        "locationId": "",
        "cropId": "",
        "pestId": "",
        "applicatorId": "",
        "chemicalChargeId": "",
        "chemicalChargeCategoryAnyOrAll": "all",
        "vehicleId": "",
        "airportStripId": "",
        "consultantId": "",
        "groundCrewMemberId": "",
        "order": "dateTransaction",
        "dateTransactionFrom": date_from.strftime(fmt),
        "dateTransactionTo":   date_to.strftime(fmt),
        "dateAppliedFrom": "",
        "timeAppliedFrom": "",
        "invoiceNumberFrom": "",
        "dateAppliedTo": "",
        "timeAppliedTo": "",
        "invoiceNumberTo": "",
    }

    resp = session.get(REPORT_URL, params=params)
    resp.raise_for_status()

    # Parse CSV
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    # Strip surrounding quotes from keys (Chem-Man wraps some headers in quotes)
    rows = [{k.strip().strip('"'): v for k, v in row.items()} for row in rows]
    print(f"✓ Fetched {len(rows)} rows from Chem-Man ({date_from.strftime(fmt)} → {date_to.strftime(fmt)})")
    if rows:
        print(f"  First row keys: {list(rows[0].keys())[:6]}")
        print(f"  First row sample: {dict(list(rows[0].items())[:6])}")
    return rows


def build_dedup_key(row):
    load   = row.get("Load Nbr", "").strip()
    loc    = row.get("Location", "").strip()
    chem   = row.get("Chemical / Charge Nickname", "").strip()
    return f"{load}|{loc}|{chem}"


# ── Smartsheet Helpers ────────────────────────────────────────────────────────
def get_or_create_sheet():
    """Find or create the target sheet, return (sheet_id, column_map)."""
    resp = requests.get(f"{SS_BASE}/sheets", headers=SS_HEADERS)
    resp.raise_for_status()
    all_sheets = resp.json().get("data", [])
    print(f"  All sheets visible to token: {[(s['name'], s['id']) for s in all_sheets]}")
    for sheet in all_sheets:
        if sheet["name"] == SHEET_NAME:
            sheet_id = sheet["id"]
            print(f"✓ Found existing sheet: '{SHEET_NAME}' (id={sheet_id})")
            time.sleep(2)
            return sheet_id, get_column_id_map(sheet_id)

    payload = {"name": SHEET_NAME, "columns": COLUMN_DEFS}
    resp = requests.post(f"{SS_BASE}/sheets", headers=SS_HEADERS, json=payload)
    resp.raise_for_status()
    sheet_id = resp.json()["result"]["id"]
    print(f"✓ Created new sheet: '{SHEET_NAME}' (id={sheet_id})")
    time.sleep(5)
    return sheet_id, get_column_id_map(sheet_id)


def get_column_id_map(sheet_id):
    """Return {column_title: column_id} for the sheet."""
    resp = requests.get(f"{SS_BASE}/sheets/{sheet_id}", headers=SS_HEADERS)
    if not resp.ok:
        print(f"  ERROR fetching sheet {sheet_id}: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    data = resp.json()
    if "columns" not in data:
        raise Exception(f"No columns in sheet response: {data}")
    return {col["title"]: col["id"] for col in data["columns"]}


def get_existing_dedup_keys(sheet_id, dedup_col_id):
    """Return a set of all dedup key values already in the sheet."""
    keys = set()
    resp = requests.get(f"{SS_BASE}/sheets/{sheet_id}", headers=SS_HEADERS)
    resp.raise_for_status()
    for row in resp.json().get("rows", []):
        for cell in row.get("cells", []):
            if cell.get("columnId") == dedup_col_id and cell.get("value"):
                keys.add(str(cell["value"]))
    print(f"✓ {len(keys)} existing rows in Smartsheet (dedup check)")
    return keys


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("Chem-Man → Smartsheet Sync")
    print(f"Pulling last {DAYS_BACK} days of spray applications")
    print("=" * 50)

    csv_rows = get_chemman_csv()
    sheet_id, col_id_map = get_or_create_sheet()
    dedup_col_id = col_id_map[DEDUP_COL]
    existing_keys = get_existing_dedup_keys(sheet_id, dedup_col_id)

    new_rows = []
    skipped = 0

    for row in csv_rows:
        key = build_dedup_key(row)
        print(f"  Dedup key: '{key}'")
        if key in existing_keys:
            skipped += 1
            continue

        cells = []
        for csv_col, ss_col in COLUMN_MAP.items():
            value = row.get(csv_col, "").strip()
            # Find column ID with whitespace-tolerant lookup
            col_id = col_id_map.get(ss_col) or col_id_map.get(ss_col.strip())
            if col_id and value:
                cells.append({"columnId": col_id, "value": value, "strict": False})
        # Debug: print first row's cells
        if not new_rows:
            print(f"  First row ALL cells: {cells}")

        # Add dedup key
        cells.append({"columnId": dedup_col_id, "value": key})

        if cells:
            new_rows.append({"toBottom": True, "cells": cells})
            existing_keys.add(key)  # prevent dupes within this batch

    print(f"✓ {len(new_rows)} new rows to insert, {skipped} already exist")

    if new_rows:
        print(f"  Sample row cells: {new_rows[0]['cells'][:3]}")
    if not new_rows:
        print("Nothing to do — Smartsheet is up to date.")
        return

    # Insert rows one at a time to avoid sibling/parent issues
    inserted = 0
    for row in new_rows:
        resp = requests.post(f"{SS_BASE}/sheets/{sheet_id}/rows", headers=SS_HEADERS, json={"rows": [row]})
        resp.raise_for_status()
        result = resp.json()
        row_num = result.get("result", {}).get("rowNumber")
        sibling = result.get("result", {}).get("siblingId")
        if sibling:
            print(f"  WARNING: row {row_num} has siblingId {sibling} — may be nested!")
        inserted += 1
        if inserted % 10 == 0:
            print(f"  Inserted {inserted}/{len(new_rows)} rows")
    print(f"  Inserted all {inserted} rows")

    print(f"\n✓ Done! {inserted} new rows added to '{SHEET_NAME}'")


if __name__ == "__main__":
    main()
