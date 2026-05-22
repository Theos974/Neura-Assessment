"""
Standalone script to fetch one week of hourly PV output from renewables.ninja
and write it to data/solar_limassol_week.csv.

Usage:
    NINJA_TOKEN=your_token python data/fetch_solar.py

Get a free token at https://www.renewables.ninja (register, then copy from your profile).
The seed_data management command uses the same logic internally; this script is provided
so you can inspect or pre-cache the raw hourly data.
"""

import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

WEEK_START = datetime(2024, 7, 22, tzinfo=timezone.utc)
WEEK_END   = datetime(2024, 7, 28, tzinfo=timezone.utc)

PARAMS = {
    "lat": 34.6786,
    "lon": 33.0413,
    "date_from": WEEK_START.strftime("%Y-%m-%d"),
    "date_to": WEEK_END.strftime("%Y-%m-%d"),
    "dataset": "merra2",
    "capacity": 200,
    "system_loss": 10,
    "tracking": 0,
    "tilt": 30,
    "azim": 180,
    "format": "json",
    "local_time": False,
}


def main():
    token = os.getenv("NINJA_TOKEN")
    if not token:
        print("Set NINJA_TOKEN env var first.")
        sys.exit(1)

    print(f"Fetching from renewables.ninja ({PARAMS['date_from']} to {PARAMS['date_to']})...")
    resp = requests.get(
        "https://www.renewables.ninja/api/data/pv",
        headers={"Authorization": f"Token {token}"},
        params=PARAMS,
        timeout=30,
    )
    resp.raise_for_status()

    data = resp.json()["data"]
    out_path = Path(__file__).parent / "solar_limassol_week.csv"

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_utc", "solar_kw"])
        for ts_str, values in data.items():
            writer.writerow([ts_str, round(float(values["electricity"]), 3)])

    print(f"Written {len(data)} rows to {out_path}")


if __name__ == "__main__":
    main()
