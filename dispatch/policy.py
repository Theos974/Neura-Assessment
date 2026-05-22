"""
Greedy behind-the-meter dispatch policy.

Priority each 15-min slot:
  1. Solar covers load
  2. Surplus solar charges battery (SoC cap 95%, power cap 200 kW)
  3. Off-peak hours (23:00-09:00): charge remainder of battery from cheap grid
  4. Peak hours (09:00-23:00): discharge battery to cover unmet load
  5. Remaining unmet load drawn from grid
  6. Solar that can't be absorbed is curtailed (no grid export allowed)

The background section of the spec explicitly describes both modes:
"Charge when energy is cheap or free — surplus rooftop solar at midday,
off-peak grid overnight. Discharge when energy is expensive."

With a 200 kWp array serving a 200 kW peak hotel, the load almost always
exceeds solar output — there is almost no surplus solar to capture. The
off-peak grid charging (buy at €0.15, sell back as avoided cost at €0.30)
is where the real arbitrage happens.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone as tz

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from .models import EnergyInterval

CAPACITY_KWH: float = 400.0
MAX_POWER_KW: float = 200.0
SOC_MIN: float = 0.10
SOC_MAX: float = 0.95

# 88% round-trip split symmetrically: each direction loses sqrt(0.12) ≈ 6.2%
_RTE: float = 0.88
EFF_CHARGE: float = math.sqrt(_RTE)
EFF_DISCHARGE: float = math.sqrt(_RTE)

DT: float = 0.25  # hours per 15-min interval
PEAK_START: int = 9
PEAK_END: int = 23


@transaction.atomic
def run_dispatch(intervals: QuerySet[EnergyInterval], initial_soc: float = 0.50) -> None:
    """
    Run greedy dispatch over *intervals* (must be time-ordered).
    Writes one DispatchInterval row per input row, replacing any existing results.
    """
    from .models import DispatchInterval

    soc: float = initial_soc
    rows: list[DispatchInterval] = []

    for ei in intervals.order_by("timestamp"):
        local_hour = tz.localtime(ei.timestamp).hour
        result = _greedy_step(ei.solar_kw, ei.load_kw, ei.grid_price_eur_per_kwh,
                              local_hour, soc)
        soc = result["soc_end"]
        rows.append(DispatchInterval(
            interval=ei,
            battery_kw=result["battery_kw"],
            soc_pct=soc * 100.0,
            grid_kw=result["grid_kw"],
            curtailed_kw=result["curtailed_kw"],
            cost_eur=result["cost_eur"],
            counterfactual_cost_eur=result["counterfactual_cost_eur"],
        ))

    interval_ids = [ei.id for ei in intervals]
    DispatchInterval.objects.filter(interval_id__in=interval_ids).delete()
    DispatchInterval.objects.bulk_create(rows)


def _greedy_step(
    solar_kw: float,
    load_kw: float,
    price: float,
    hour: int,
    soc: float,
) -> dict[str, float]:
    """Single 15-min dispatch step. Pure function — no DB access."""

    solar_to_load = min(solar_kw, load_kw)
    surplus_solar = solar_kw - solar_to_load
    unmet_load = load_kw - solar_to_load

    battery_kw: float = 0.0
    curtailed_kw: float = 0.0

    if surplus_solar > 0 and soc < SOC_MAX:
        soc_headroom_kwh = (SOC_MAX - soc) * CAPACITY_KWH
        max_charge_by_soc = soc_headroom_kwh / (EFF_CHARGE * DT)
        charge_kw = min(surplus_solar, MAX_POWER_KW, max_charge_by_soc)
        charge_kw = max(0.0, charge_kw)

        battery_kw = charge_kw
        soc += charge_kw * DT * EFF_CHARGE / CAPACITY_KWH
        curtailed_kw = surplus_solar - charge_kw
    elif surplus_solar > 0:
        curtailed_kw = surplus_solar

    is_peak = PEAK_START <= hour < PEAK_END

    # Off-peak: charge from grid at cheap rate (€0.15/kWh) to fill battery for peak use
    if not is_peak and soc < SOC_MAX:
        soc_headroom_kwh = (SOC_MAX - soc) * CAPACITY_KWH
        max_charge_by_soc = soc_headroom_kwh / (EFF_CHARGE * DT)
        grid_charge_kw = min(MAX_POWER_KW, max_charge_by_soc)
        grid_charge_kw = max(0.0, grid_charge_kw)
        battery_kw += grid_charge_kw
        soc += grid_charge_kw * DT * EFF_CHARGE / CAPACITY_KWH
        unmet_load += grid_charge_kw  # grid supplies this charging power

    # Peak: discharge to cover load rather than pulling from expensive grid
    if unmet_load > 0 and soc > SOC_MIN and is_peak:
        soc_available_kwh = (soc - SOC_MIN) * CAPACITY_KWH
        max_discharge_by_soc = soc_available_kwh * EFF_DISCHARGE / DT
        discharge_kw = min(unmet_load, MAX_POWER_KW, max_discharge_by_soc)
        discharge_kw = max(0.0, discharge_kw)

        battery_kw -= discharge_kw
        soc -= discharge_kw * DT / (EFF_DISCHARGE * CAPACITY_KWH)
        unmet_load -= discharge_kw

    # Clamp after floating-point drift
    soc = max(SOC_MIN, min(SOC_MAX, soc))

    grid_kw = max(0.0, unmet_load)
    cost_eur = grid_kw * DT * price

    cf_grid_kw = max(0.0, load_kw - solar_kw)
    counterfactual_cost_eur = cf_grid_kw * DT * price

    return {
        "battery_kw": battery_kw,
        "soc_end": soc,
        "grid_kw": grid_kw,
        "curtailed_kw": curtailed_kw,
        "cost_eur": cost_eur,
        "counterfactual_cost_eur": counterfactual_cost_eur,
    }