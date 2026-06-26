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
SHEET_NAME       = "2026 Rize Chem Man Imports"
SHEET_ID         = 4884496338866052  # Hardcoded — point directly at existing sheet

CHEMMAN_BASE  = "https://login.chem-man.com"
LOGIN_URL     = f"{CHEMMAN_BASE}/xhr/StoreUser.xhrLogin"
REPORT_URL    = f"{CHEMMAN_BASE}/reportPostedFieldApplications.php"

SS_BASE    = "https://api.smartsheet.com/2.0"
SS_HEADERS = {
    "Authorization": f"Bearer {SMARTSHEET_TOKEN}",
    "Content-Type": "application/json"
}

# ── Columns to pull from CSV (CSV header → Smartsheet column name) ────────────
# Column names match the existing 2026 Rize Chem Man Imports sheet exactly
COLUMN_MAP = {
    "Load Nbr":                                      "Load Nbr",
    "Location/Site Nbr":                             "Location Site Nbr",
    "Location Applied Acres":                        "Location Applied Acres",
    "Location Latitude/Longitude":                   "Event Latitude and Longitude",
    "Location Pest":                                 "Location Pest",
    "Location Crop":                                 "Location Crop",
    "Chemical / Charge Nickname":                    "Chemical / Charge Nickname",
    "Chemical / Charge Description":                 "Chemical / Charge Description",
    "Chemical / Charge EPA #":                       "Chemical / Charge EPA #",
    "Chemical / Charge Applied Rate":                "Chemical / Charge Applied Rate",
    "Chemical / Charge Applied Unit":                "Chemical / Charge Applied Unit",
    "Chemical / Charge Total Applied":               "Chemical / Charge Total Applied",
    "Chemical / Charge Total Applied in Base Units": "Chemical / Charge Total Applied in Base Units",
    "Chemical / Charge Total Applied Base Unit":     "Chemical / Charge Total Applied Base Unit",
    "Chemical / Charge Pest":                        "Chemical / Charge Pest",
    "Chemical / Charge Cost Unit":                   "Chemical / Charge Cost Unit",
    "Applicator First Name":                         "Applicator First Name",
    "Applicator Last Name":                          "Applicator Last Name",
    "Applicator Vehicle ID":                         "Applicator Vehicle ID",
    "Applicator Vehicle Description":                "Applicator Vehicle Description",
    "Application Date":                              "Application Date",
    "Application Start Time":                        "Start Time Imported",
    "Application End Time":                          "End Time Imported",
    "Temperature Start":                             "Temperature Start",
    "Temperature End":                               "Temperature End",
    "Wind MPH Start":                                "Wind MPH Start",
    "Wind MPH End":                                  "Wind MPH End",
    "Status":                                        "Status",
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
    return rows


def build_dedup_key(row):
    load   = row.get("Load Nbr", "").strip()
    site   = row.get("Location/Site Nbr", "").strip()
    chem   = row.get("Chemical / Charge Nickname", "").strip()
    return f"{load}|{site}|{chem}"


# ── Smartsheet Helpers ────────────────────────────────────────────────────────
def get_or_create_sheet():
    """Use the hardcoded sheet ID directly."""
    print(f"✓ Using sheet: '{SHEET_NAME}' (id={SHEET_ID})")
    return SHEET_ID, get_column_id_map(SHEET_ID)


def get_column_id_map(sheet_id):
    """Return {column_title: column_id} for the sheet."""
    resp = requests.get(f"{SS_BASE}/sheets/{sheet_id}", headers=SS_HEADERS)
    if not resp.ok:
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
        # Add dedup key
        cells.append({"columnId": dedup_col_id, "value": key})

        if cells:
            new_rows.append({"toBottom": True, "cells": cells})
            existing_keys.add(key)  # prevent dupes within this batch

    print(f"✓ {len(new_rows)} new rows to insert, {skipped} already exist")

    if not new_rows:
        print("Nothing to do — Smartsheet is up to date.")
        return

    # Insert in batches of 500
    for i in range(0, len(new_rows), 500):
        batch = new_rows[i:i + 500]
        resp = requests.post(f"{SS_BASE}/sheets/{sheet_id}/rows", headers=SS_HEADERS, json=batch)
        resp.raise_for_status()
    print(f"  Inserted all {len(new_rows)} rows")

    print(f"\n✓ Done! {len(new_rows)} new rows added to '{SHEET_NAME}'")


if __name__ == "__main__":
    main()
