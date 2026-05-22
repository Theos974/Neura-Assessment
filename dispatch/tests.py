"""
Unit tests for the dispatch policy.

All tests call _greedy_step() directly — no database, no Django ORM.
The public run_dispatch() is covered implicitly by the seed_data command,
but the core logic lives in the pure function and that's what we test here.
"""

from django.test import TestCase

from .policy import (
    CAPACITY_KWH,
    EFF_CHARGE,
    EFF_DISCHARGE,
    MAX_POWER_KW,
    SOC_MAX,
    SOC_MIN,
    _greedy_step,
)


class GreedyStepTests(TestCase):

    def _step(self, solar=0.0, load=0.0, price=0.30, hour=12, soc=0.50):
        return _greedy_step(solar, load, price, hour, soc)

    # Solar covers load exactly — no grid, no battery, no curtailment
    def test_solar_exactly_covers_load(self):
        result = self._step(solar=100.0, load=100.0, hour=12)
        self.assertAlmostEqual(result["grid_kw"], 0.0, places=3)
        self.assertAlmostEqual(result["battery_kw"], 0.0, places=3)
        self.assertAlmostEqual(result["curtailed_kw"], 0.0, places=3)

    # Surplus solar should charge the battery
    def test_surplus_solar_charges_battery(self):
        result = self._step(solar=150.0, load=50.0, soc=0.50, hour=12)
        self.assertGreater(result["battery_kw"], 0.0)
        self.assertAlmostEqual(result["grid_kw"], 0.0, places=3)

    # No solar, load during peak hours → battery discharges
    def test_peak_load_draws_from_battery(self):
        result = self._step(solar=0.0, load=100.0, soc=0.80, hour=14)
        self.assertLess(result["battery_kw"], 0.0)
        self.assertLess(result["grid_kw"], 100.0)

    # Off-peak hours → battery charges from cheap grid, never discharges
    def test_off_peak_charges_from_grid(self):
        result = self._step(solar=0.0, load=80.0, soc=0.50, hour=2)
        # battery_kw positive = charging
        self.assertGreater(result["battery_kw"], 0.0)
        # grid supplies both the load AND the battery charging
        self.assertGreater(result["grid_kw"], 80.0)

    # Battery at SoC max should not charge further; excess solar curtailed
    def test_full_battery_curtails_solar(self):
        result = self._step(solar=150.0, load=50.0, soc=SOC_MAX, hour=12)
        self.assertAlmostEqual(result["battery_kw"], 0.0, places=3)
        self.assertGreater(result["curtailed_kw"], 0.0)

    # Battery at SoC min should not discharge
    def test_empty_battery_does_not_discharge(self):
        result = self._step(solar=0.0, load=100.0, soc=SOC_MIN, hour=14)
        self.assertAlmostEqual(result["battery_kw"], 0.0, places=3)
        self.assertAlmostEqual(result["grid_kw"], 100.0, places=3)

    # SoC must stay within [SOC_MIN, SOC_MAX] after step
    def test_soc_stays_in_bounds_after_charging(self):
        result = self._step(solar=200.0, load=0.0, soc=SOC_MAX - 0.001, hour=12)
        self.assertLessEqual(result["soc_end"], SOC_MAX + 1e-9)
        self.assertGreaterEqual(result["soc_end"], SOC_MIN - 1e-9)

    def test_soc_stays_in_bounds_after_discharging(self):
        result = self._step(solar=0.0, load=200.0, soc=SOC_MIN + 0.001, hour=14)
        self.assertGreaterEqual(result["soc_end"], SOC_MIN - 1e-9)

    # Charge power must not exceed 200 kW even with huge surplus
    def test_charge_power_capped(self):
        result = self._step(solar=500.0, load=0.0, soc=0.50, hour=12)
        self.assertLessEqual(result["battery_kw"], MAX_POWER_KW + 1e-9)

    # Discharge power must not exceed 200 kW
    def test_discharge_power_capped(self):
        result = self._step(solar=0.0, load=500.0, soc=0.90, hour=14)
        self.assertGreaterEqual(result["battery_kw"], -MAX_POWER_KW - 1e-9)

    # Grid draw must never be negative (no export)
    def test_no_grid_export(self):
        result = self._step(solar=200.0, load=0.0, soc=SOC_MAX, hour=12)
        self.assertGreaterEqual(result["grid_kw"], 0.0)

    # Off-peak + full battery → no charging possible, so actual cost == counterfactual cost
    def test_counterfactual_equals_actual_when_battery_full_off_peak(self):
        result = self._step(solar=0.0, load=50.0, soc=SOC_MAX, hour=2)
        # Battery already full, no solar → grid draw equals load, same as no-battery case
        self.assertAlmostEqual(result["cost_eur"], result["counterfactual_cost_eur"], places=3)

    # Round-trip efficiency: SoC increase from charging should reflect EFF_CHARGE
    def test_charge_efficiency_applied(self):
        soc_before = 0.50
        charge_kw  = 100.0  # well within limits
        result     = self._step(solar=charge_kw + 50.0, load=50.0, soc=soc_before, hour=12)
        expected_soc_delta = charge_kw * 0.25 * EFF_CHARGE / CAPACITY_KWH
        actual_soc_delta   = result["soc_end"] - soc_before
        self.assertAlmostEqual(actual_soc_delta, expected_soc_delta, places=4)
