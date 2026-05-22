"""
EAC tariff definitions.

RESIDENTIAL_CODE_02
    EAC residential two-rate TOU, as specified in the task brief.

COMMERCIAL_CODE_30
    EAC Monthly Low Voltage Seasonal Two-Rate Commercial and Industrial Use Tariff.
    Source: EAC PDF "Commercial and Industrial Use Tariffs", March 2020.
    Applicable to Low Voltage supplies where approved Load Entitlement exceeds
    70 kVA (100A 3-ph). A 200 kW hotel (~235 kVA at 0.85 PF) falls in this band.

    Rates are base rates at the reference fuel price of EUR 300/MT heavy fuel oil.
    EAC applies a Fuel Adjustment Clause every billing period — the coefficient is
    published on eac.com.cy and not included in the PDF. Base rates are used here;
    real 2024 bills would be higher by an amount proportional to the fuel price
    deviation from EUR 300/MT.

    Key structural difference from residential Code 02:
    - Seasonal: different peak hours in winter (16:00-23:00) vs summer (09:00-23:00)
    - Weekend/holiday rates differ from weekday rates
    - Summer weekends are nearly flat (12.08c peak vs 11.79c off-peak = 1.02:1 ratio)
      so battery arbitrage on weekends is almost worthless
    - Weekday summer ratio: 17.30c / 12.00c = 1.44:1 (vs residential 2:1)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TouTariff:
    name: str
    code: str
    source: str

    def rate_for_dt(self, local_dt: datetime) -> float:
        raise NotImplementedError

    def is_peak(self, local_dt: datetime) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class SimpleToU(TouTariff):
    """Two-rate tariff: one peak rate, one off-peak rate, same every day."""
    peak_rate: float
    offpeak_rate: float
    peak_start: int  # local hour, inclusive
    peak_end: int    # local hour, exclusive

    def rate_for_dt(self, local_dt: datetime) -> float:
        return self.peak_rate if self.is_peak(local_dt) else self.offpeak_rate

    def is_peak(self, local_dt: datetime) -> bool:
        return self.peak_start <= local_dt.hour < self.peak_end


@dataclass(frozen=True)
class Code30Tariff(TouTariff):
    """
    EAC Code 30 — seasonal, weekday/weekend split.

    Summer (June-September):
        Peak hours: 09:00-23:00 every day
        Weekday peak:    13.77 + 2.88 + 0.65 = 17.30 c/kWh
        Weekday off-peak: 8.47 + 2.88 + 0.65 = 12.00 c/kWh
        Weekend peak:     8.55 + 2.88 + 0.65 = 12.08 c/kWh
        Weekend off-peak: 8.26 + 2.88 + 0.65 = 11.79 c/kWh

    Winter (October-May):
        Peak hours: 16:00-23:00 every day
        (rates not used in our July simulation)
    """
    # Summer rates (euros, not cents)
    summer_wd_peak: float = 0.1730
    summer_wd_offpeak: float = 0.1200
    summer_we_peak: float = 0.1208
    summer_we_offpeak: float = 0.1179

    SUMMER_MONTHS = frozenset({6, 7, 8, 9})
    SUMMER_PEAK_START = 9
    SUMMER_PEAK_END = 23
    WINTER_PEAK_START = 16
    WINTER_PEAK_END = 23

    def _is_summer(self, local_dt: datetime) -> bool:
        return local_dt.month in self.SUMMER_MONTHS

    def _is_weekend(self, local_dt: datetime) -> bool:
        return local_dt.weekday() >= 5  # Saturday=5, Sunday=6

    def is_peak(self, local_dt: datetime) -> bool:
        start = self.SUMMER_PEAK_START if self._is_summer(local_dt) else self.WINTER_PEAK_START
        return start <= local_dt.hour < self.SUMMER_PEAK_END

    def rate_for_dt(self, local_dt: datetime) -> float:
        peak = self.is_peak(local_dt)
        if self._is_summer(local_dt):
            if self._is_weekend(local_dt):
                return self.summer_we_peak if peak else self.summer_we_offpeak
            return self.summer_wd_peak if peak else self.summer_wd_offpeak
        # Winter rates not modelled (outside our simulation window)
        raise ValueError(f"Code 30 winter rates not configured for {local_dt}")


RESIDENTIAL_CODE_02 = SimpleToU(
    name="Residential Two-Rate",
    code="02",
    source="EAC Code 02 (task specification)",
    peak_rate=0.30,
    offpeak_rate=0.15,
    peak_start=9,
    peak_end=23,
)

COMMERCIAL_CODE_30 = Code30Tariff(
    name="Commercial LV Seasonal Two-Rate",
    code="30",
    source="EAC PDF 'Commercial and Industrial Use Tariffs', March 2020. "
           "Base rates at EUR 300/MT fuel; subject to Fuel Adjustment Clause.",
)

ALL_TARIFFS: dict[str, TouTariff] = {
    "residential": RESIDENTIAL_CODE_02,
    "commercial": COMMERCIAL_CODE_30,
}
