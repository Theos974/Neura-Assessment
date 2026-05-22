"""
Populate the database with one representative week of energy data and run dispatch.

Solar:   fetched from renewables.ninja if NINJA_TOKEN env var is set,
         otherwise falls back to a synthetic clear-sky model (documented below).
         Either way resampled hourly -> 15-min via linear interpolation.
Load:    synthetic profile for a 4-star Cyprus hotel in July (see _build_load_profile).
Price:   stylised EAC 2-rate TOU — day 09:00-23:00 €0.30/kWh, night €0.15/kWh.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

import requests
from django.core.management.base import BaseCommand

from dispatch.models import EnergyInterval
from dispatch.policy import run_dispatch

WEEK_START_UTC = datetime(2024, 7, 22, 0, 0, tzinfo=timezone.utc)
WEEK_END_UTC   = datetime(2024, 7, 29, 0, 0, tzinfo=timezone.utc)  # exclusive

LIMASSOL_LAT    = 34.6786
LIMASSOL_LON    = 33.0413
SYSTEM_KWP      = 200.0
CYPRUS_UTC_OFFSET = 3  # EEST in summer

PRICE_DAY   = 0.30  # €/kWh  09:00-23:00 local
PRICE_NIGHT = 0.15  # €/kWh  23:00-09:00 local


class Command(BaseCommand):
    help = "Seed one week of energy data and run the dispatch simulation."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                            help="Delete existing data before seeding.")

    def handle(self, *args, **options):
        if options["force"]:
            EnergyInterval.objects.all().delete()
            self.stdout.write("Cleared existing data.")

        if EnergyInterval.objects.exists():
            self.stdout.write("Data already present. Use --force to reseed.")
            return

        self.stdout.write("Fetching/generating solar data...")
        hourly_solar = _get_solar_hourly()

        self.stdout.write("Building load profile...")
        hourly_load = _build_load_profile()

        self.stdout.write("Writing 15-min intervals to database...")
        intervals = _build_intervals(hourly_solar, hourly_load)
        EnergyInterval.objects.bulk_create(intervals)
        self.stdout.write(f"  Created {len(intervals)} EnergyInterval rows.")

        self.stdout.write("Running dispatch simulation...")
        run_dispatch(EnergyInterval.objects.all())
        self.stdout.write(self.style.SUCCESS("Done. Visit /reports/weekly/ to see results."))


def _get_solar_hourly() -> list[float]:
    token = os.getenv("NINJA_TOKEN")
    if token:
        try:
            return _fetch_ninja(token)
        except Exception as exc:
            print(f"  renewables.ninja fetch failed ({exc}), using synthetic model.")
    else:
        print("  NINJA_TOKEN not set — using synthetic clear-sky model.")
    return _synthetic_solar_hourly()


def _fetch_ninja(token: str) -> list[float]:
    """Fetch hourly AC output (kW) from renewables.ninja for the representative week."""
    resp = requests.get(
        "https://www.renewables.ninja/api/data/pv",
        headers={"Authorization": f"Token {token}"},
        params={
            "lat": LIMASSOL_LAT,
            "lon": LIMASSOL_LON,
            "date_from": WEEK_START_UTC.strftime("%Y-%m-%d"),
            "date_to": (WEEK_END_UTC - timedelta(days=1)).strftime("%Y-%m-%d"),
            "dataset": "merra2",
            "capacity": SYSTEM_KWP,
            "system_loss": 10,
            "tracking": 0,
            "tilt": 30,
            "azim": 180,
            "format": "json",
            "local_time": False,
        },
        timeout=30,
    )
    resp.raise_for_status()
    values = [float(v["electricity"]) for v in resp.json()["data"].values()]
    if len(values) != 168:
        raise ValueError(f"Expected 168 hourly values, got {len(values)}")
    return values


def _synthetic_solar_hourly() -> list[float]:
    """
    Clear-sky model for Limassol, July.

    Assumptions:
    - 200 kWp, south-facing, 30° tilt
    - Performance ratio 0.78: accounts for inverter losses, temperature derating
      (~10% above STC in Cyprus July heat), and ~5% soiling on an uncleaned array
    - Effective peak AC output: 200 * 0.68 = 136 kW
      PR 0.68 accounts for: inverter losses (~4%), temperature derating (~8% in Cyprus July
      heat), soiling/dust (~4%), wiring/mismatch (~4%). Gives ~6.4 kWh/kWp/day,
      consistent with renewables.ninja MERRA-2 output for Limassol July.
    - Day shape: sin curve between local sunrise (05:20) and sunset (20:20)
    - Solar noon at 12:50 local (Cyprus sits slightly east of the UTC+3 meridian)
    - Slight day-to-day variation via a factor array (dust mid-week, haze Saturday)
    """
    PEAK_KW       = 136.0
    SUNRISE_LOCAL = 5.33   # 05:20
    SUNSET_LOCAL  = 20.33  # 20:20
    DAY_FACTOR    = [1.00, 0.99, 0.98, 0.97, 0.98, 0.96, 0.99]  # Mon-Sun

    values: list[float] = []
    for h in range(168):
        local_hour = (h % 24 + CYPRUS_UTC_OFFSET) % 24
        day        = h // 24
        if local_hour < SUNRISE_LOCAL or local_hour > SUNSET_LOCAL:
            values.append(0.0)
        else:
            angle  = math.pi * (local_hour - SUNRISE_LOCAL) / (SUNSET_LOCAL - SUNRISE_LOCAL)
            output = PEAK_KW * math.sin(angle) * DAY_FACTOR[day]
            values.append(max(0.0, output))
    return values


def _build_load_profile() -> list[float]:
    """
    Synthetic 168-hour hotel load profile for July.

    Shape rationale (DOE/OpenEI large hotel reference used as sanity check):
    - Base ~65 kW overnight: fridges, pool pumps, servers, corridor lighting
    - Morning ramp from 06:00 as kitchens and HVAC start
    - Afternoon peak 12:00-16:00: cooling dominates in Cyprus July (40°C outside);
      a full 200-room hotel running HVAC + pools + restaurant peaks near 200 kW
    - Evening 18:00-22:00: dinner service, then guests settle
    - Weekend (Fri/Sat) occupancy ~10% higher
    - Entire profile scaled so the weekly peak (Friday afternoon) hits exactly 200 kW
    """
    BASE_SHAPE = [
        65, 62, 60, 58, 58, 65,
        80, 93, 110, 123,
        138, 155,
        174, 190, 200, 195,
        180, 165,
        152, 155, 158,
        144, 128,
        98,
    ]
    DAY_MULT = [0.92, 0.93, 0.95, 0.97, 1.00, 0.99, 0.96]

    values: list[float] = []
    for day in range(7):
        for utc_hour in range(24):
            local_hour = (utc_hour + CYPRUS_UTC_OFFSET) % 24
            values.append(BASE_SHAPE[local_hour] * DAY_MULT[day])
    return values


def _build_intervals(
    hourly_solar: list[float],
    hourly_load: list[float],
) -> list[EnergyInterval]:
    """
    Resample hourly vectors to 15-min via linear interpolation and attach TOU prices.

    Linear interpolation (vs. forward-fill) gives a smoother ramp at sunrise/sunset
    and avoids a staircase artefact in the dispatch charts.
    """
    rows: list[EnergyInterval] = []
    total_hours = len(hourly_solar)

    for h in range(total_hours):
        s_now  = hourly_solar[h]
        s_next = hourly_solar[(h + 1) % total_hours]
        l_now  = hourly_load[h]
        l_next = hourly_load[(h + 1) % total_hours]

        for q in range(4):
            frac   = q / 4.0
            solar  = max(0.0, s_now + frac * (s_next - s_now))
            load   = max(0.0, l_now + frac * (l_next - l_now))
            ts_utc = WEEK_START_UTC + timedelta(hours=h, minutes=q * 15)
            local_hour = (ts_utc.hour + CYPRUS_UTC_OFFSET) % 24
            price  = PRICE_DAY if 9 <= local_hour < 23 else PRICE_NIGHT

            rows.append(EnergyInterval(
                timestamp=ts_utc,
                solar_kw=round(solar, 3),
                load_kw=round(load, 3),
                grid_price_eur_per_kwh=price,
            ))

    return rows
