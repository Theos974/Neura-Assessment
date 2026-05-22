# Neura Take-Home: BTM Battery Dispatch — Limassol Hotel

A Django service that simulates a behind-the-meter (BTM) battery at a 4-star Limassol hotel,
computes weekly grid savings against a no-battery counterfactual, and serves two views:

- `/reports/weekly/` — weekly performance report with energy charts, tariff comparison, and dispatch table  
- `/reports/whatif/` — what-if scenario tool to vary battery / PV sizing and compare savings in memory

**Stretch goals completed:** real EAC commercial tariff (Code 30) pulled from the official PDF and wired into the tariff comparison card; what-if form with battery capacity, power, and PV sliders.

---

## Quick start

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_data
python manage.py runserver
```

Open http://127.0.0.1:8000/reports/weekly/

No `.env` needed — Django's `SECRET_KEY` has a safe default for local use. If you want to pull
live solar data from renewables.ninja, set `NINJA_TOKEN=your_token` in `.env`.

---

## What's built

### Weekly report — `/reports/weekly/`

- **KPI cards:** weekly savings (€ and % vs no-battery), grid spend + baseline, solar
  self-consumption %, battery charged / discharged kWh
- **Energy flow chart:** solar, load, grid draw, and battery power over the week (Plotly, 15-min resolution)
- **State-of-charge chart:** SoC % with 10% / 95% operating band markers
- **Dispatch timeline:** per-day stacked bar showing share of intervals in grid-charge / solar-charge / discharge / idle
- **Savings by day:** daily bar chart (€ saved vs no-battery)
- **Tariff comparison:** same dispatch re-priced under EAC Code 02 (residential) and Code 30 (commercial LV), side by side
- **Dispatch intervals table:** filterable by date range (header controls) and hour range (section controls); shows the most recent 50 matching rows

### What-if scenario — `/reports/whatif/`

- Vary battery capacity (kWh), battery power (kW), and PV size (kWp) via number inputs + synced range sliders
- Dispatch re-runs in memory against the same representative week — no DB writes
- Shows current-system vs scenario savings side by side, with a delta badge (€ and %)
- Grouped bar chart: current vs scenario savings by day
- Scenario SoC curve

---

## Data

### Solar (`solar_kw`)

Source: [renewables.ninja](https://www.renewables.ninja) — MERRA-2 satellite reanalysis,
Limassol (34.6786°N, 33.0413°E), 200 kWp, 30° tilt, south-facing, 10% system loss, week of 22–28 Jul 2024.

renewables.ninja returns hourly AC output. Resampled to 15-min via **linear interpolation** between
adjacent hours, giving a smooth sunrise/sunset ramp. If `NINJA_TOKEN` is set, `seed_data` hits the
API directly; otherwise it falls back to a synthetic clear-sky model that reproduces a realistic
Cyprus July solar curve (peak ~156 kW AC on a clear Monday).

To pre-fetch and cache the raw CSV:
```bash
NINJA_TOKEN=your_token python data/fetch_solar.py
```

### Load (`load_kw`)

No clean public 15-min Cyprus hotel dataset exists. Synthetic profile built from first principles:

| Period | Load | Reasoning |
|--------|------|-----------|
| 00:00–06:00 | ~60–65 kW | Base: fridges, pumps, servers, lighting |
| 06:00–10:00 | ramp to ~110–120 kW | Kitchen startup, HVAC comes online |
| 12:00–16:00 | up to 200 kW | Full HVAC, pools, restaurant (Cyprus July peaks ~40°C) |
| 18:00–22:00 | ~130–155 kW | Dinner service, evening occupancy |

- **Weekly pattern:** Friday highest (×1.0), Monday lowest (×0.92) — cross-checked against
  DOE/OpenEI large hotel reference building shape
- **Scaling:** profile is normalised so the Friday afternoon peak is exactly 200 kW, matching
  the hotel's stated peak draw in the spec

**Assumption flagged:** the actual load shape will differ from reality. The key invariant
preserved is that peak load equals PV capacity (200 kW) so solar alone never fully covers
afternoon demand — giving the battery a real job to do.

### Grid price (`grid_price_eur_per_kwh`)

EAC residential **Code 02** (stylised 2-rate TOU):

- Day 09:00–23:00 Cyprus local time: **€0.30/kWh**
- Night 23:00–09:00 Cyprus local time: **€0.15/kWh**

Tariff applied consistently to both the with-battery cost and the no-battery counterfactual,
so savings reflect only the dispatch decision, not a favourable baseline trick.

### Representative week

**22–28 July 2024.** Chosen as a typical hot Cyprus summer week — high solar yield, high HVAC
load, and a stable 2:1 day/night tariff ratio that makes the arbitrage straightforward to interpret.

**Timezone note:** the database stores UTC. All display, dispatch decisions, and tariff lookups use
`Europe/Nicosia` (UTC+3 in summer). The seeded week runs UTC 2024-07-22 00:00 to 2024-07-28 23:45,
which is local 22 Jul 03:00–29 Jul 02:45 — 12 intervals spill into local 29 Jul. The views
filter on the 7 distinct local dates (Jul 22–28) so those overflow intervals never appear in
charts or the baseline savings figure.

---

## Dispatch algorithm

Greedy BTM policy, evaluated at each 15-min step (`dispatch/policy.py`):

1. Solar covers load first (always free)
2. Surplus solar charges the battery (up to 95% SoC, capped at 200 kW)
3. Unmet load during peak hours (09:00–23:00 local) draws from battery (down to 10% SoC, capped at 200 kW)
4. Remaining unmet load is pulled from the grid
5. Solar surplus the battery cannot absorb is curtailed — no grid export (Cyprus commercial net-metering is restricted)

**No off-peak discharge:** overnight grid is €0.15/kWh. Discharging at night saves only half as
much as discharging in the peak window. The better strategy is to hold charge for the expensive
afternoon period.

**Round-trip efficiency:** 88% is given in the spec. The modelling choice is how to split it: I applied losses symmetrically — charge η = discharge η = √0.88 ≈ 93.8% — rather than front-loading losses on one side. Every kWh stored costs ~6.2% on the way in and every kWh recovered loses another ~6.2% on the way out. The symmetric split is the standard assumption for LFP cells and keeps the SoC arithmetic clean, but it's not the only valid model — front-loading losses on discharge would make discharging slightly less attractive at the margin and could shift a few boundary intervals from discharge to idle.

**Why there's no solar-charge segment in the dispatch timeline:** at 136 kW average peak solar
and ~150–200 kW hotel load, solar never fully covers the building — there is no surplus to
push into the battery. All charging comes from the grid during the cheap overnight window.

---

## Tariff comparison

### EAC Code 02 (residential, deployed system)

Flat 2-rate TOU, same every day:

| Window | Rate |
|--------|------|
| Day 09:00–23:00 | €0.30/kWh |
| Night 23:00–09:00 | €0.15/kWh |

Weekly savings: **≈ €285** (≈ 22% reduction vs no-battery baseline).

### EAC Code 30 (commercial LV, stretch goal)

Rates extracted from the official EAC PDF (`Commercial and Industrial Use.pdf`):

| Period | Peak | Off-peak |
|--------|------|----------|
| Summer weekday (Jun–Sep) | €0.1730/kWh | €0.1200/kWh |
| Summer weekend | €0.1208/kWh | €0.1179/kWh |

**Why Code 30 savings are lower:** the weekday peak/off-peak ratio is only 1.44:1 (vs 2:1 for
Code 02). The battery arbitrages the spread between cheap overnight charging and expensive
daytime discharging — a smaller spread means less value per kWh cycled. Weekend rates are
nearly flat (1.02:1 ratio), yielding almost no weekend savings. A hotel on Code 30 would
see roughly half the weekly savings compared to Code 02.

---

## What-if sensitivity — findings

Running the scenario tool across the parameter space reveals:

| Parameter | Effect |
|-----------|--------|
| Battery capacity (kWh) | **Primary lever.** Doubling to 800 kWh doubles weekly savings (~€570). Halving to 200 kWh halves them. |
| Battery power (kW) | Flat above ~100 kW. At 200 kW, the battery fully charges in ~1.8h of the 10h off-peak window — power is never the bottleneck. Below 100 kW savings fall because the battery can no longer fully cycle within the available window. |
| PV size (kWp) | **No effect on absolute savings** with the current greedy policy. The battery fills from the grid overnight and is already at 95% SoC when solar peaks at midday — surplus is curtailed. More PV does not help until a forecast-aware dispatch can skip the overnight grid charge when tomorrow's solar will suffice. |

This is an honest finding, not a limitation to hide — it's the natural next optimisation step.

---

## Architecture

```
dispatch/
  models.py        EnergyInterval, DispatchInterval
  policy.py        _greedy_step() pure function + dispatch_scenario() for in-memory what-if
  tariffs.py       TouTariff, SimpleToU (Code 02), Code30Tariff (Code 30) — no view imports
  views.py         weekly_report(), whatif_report() — call policy/tariffs, never embed logic
  seed_data        Management command — seeds EnergyInterval, runs dispatch, writes DispatchInterval
```

Dispatch lives in `policy.py`, tariff logic in `tariffs.py`. Views only aggregate and render.
`dispatch_scenario()` takes a plain list of `EnergyInterval` objects and returns plain dicts,
so the what-if re-run has no Django ORM dependency and can be called from a test directly.

---

## Tests

```bash
python manage.py test dispatch
```

Covers `_greedy_step()` pure function: SoC bounds, power caps, off-peak hold, curtailment,
no grid export, and round-trip efficiency accounting.

---

## What I'd build next

1. **Forecast-aware dispatch.** The greedy policy leaves PV value on the table. A day-ahead solar
   forecast (Open-Meteo is free) would let us skip the overnight grid charge when tomorrow's solar
   will fill the battery — unlocking the self-consumption gains that currently show as zero.

2. **LP solver for optimal dispatch.** A 24h linear programme (PuLP, HiGHS, or scipy) with a
   rolling horizon would find the globally optimal charge/discharge schedule given known tariff
   transitions. Likely to outperform the greedy policy by 5–15% on weeks with variable cloud cover.

3. **Live data ingestion.** Replace `seed_data` with a scheduled task polling a smart-meter API
   (or SCADA feed) every 15 minutes. Fresh `EnergyInterval` rows, dispatch re-run automatically,
   report always current.

4. **Multi-site / multi-tariff.** The `tariffs.py` abstraction already supports multiple tariffs.
   Adding a site selector and per-site tariff assignment would generalise the service to Neura's
   full customer portfolio.

5. **Battery degradation model.** LFP cells degrade with cycles. Factoring a simple cycle-counting
   model into the economics would give a more accurate 10-year NPV projection for investment cases.

---

## How I used AI

Claude Code (claude-sonnet-4-6, VSCode extension) was used 
**Where it helped directly:**

- Scaffolded the Django project structure, models, and management command boilerplate in minutes
- Translated the dispatch algorithm constraints into working Python once I had the logic clear
- Wrote the Plotly chart configuration and CSS layout from a rough sketch
- Produced the `dispatch_scenario()` in-memory re-run function from the existing `_greedy_step()` signature

**Where I had to steer it:**

- The initial tariff lookup used raw UTC hours rather than `localtime()` — caught by comparing
  numbers against manual calculation, then fixed
- The `strftime("%a")` bucketing for per-day savings caused Monday Jul 22 and the UTC overflow
  Monday Jul 29 to merge into one bar — I diagnosed from the chart anomaly and rewrote the
  grouping to use `local_dt.date()` as the key
- The `last_date` boundary was initially set to the local date of the final UTC row (Jul 29),
  leaking 12 off-peak charging intervals into the savings figure and chart. I traced the numeric
  discrepancy between the weekly report and the what-if baseline, identified the UTC+3 spillover
  as the root cause, and fixed it by computing the 7th distinct local date instead
- The `whatif_report` baseline was still using all 672 unfiltered rows even after the weekly
  report was fixed — caught by comparing the two €-saved figures and fixing the date filter
  consistently across both views

**Honest assessment:** Claude is faster than I am at boilerplate and chart config. It is slower
than I am at noticing when numbers don't add up or conflicts that don't make sense. The loop that found the UTC/local date bug was:
inspect output → form hypothesis → read code → verify with arithmetic → tell Claude exactly
what to change. That loop ran several times.

---

## Loom (≤ 3 min)

**One thing in the data that surprised me**

The commercial tariff (Code 30) saves less than the residential one (Code 02) — which is
counterintuitive. You'd expect a business on a commercial tariff to have more battery value,
not less. The issue is the arbitrage spread: Code 02 has a 2:1 peak/off-peak ratio (€0.30 vs
€0.15), so the battery earns €0.15 per kWh cycled. Code 30 summer weekday ratio is only 1.44:1
(€0.1730 vs €0.1200), and weekends are nearly flat at 1.02:1 — essentially no arbitrage value
on Saturday and Sunday. The battery does the same work under both tariffs; it just earns less
per cycle under the commercial rate structure.

**One assumption I made and why**

Round-trip efficiency is 88% — given in the spec. The modelling decision is how to split it.
I applied losses symmetrically: √0.88 ≈ 93.8% on charge, 93.8% on discharge, rather than
front-loading losses on one side. The symmetric split is the standard assumption for LFP cells
and keeps the SoC arithmetic clean. The alternative — heavier losses on discharge — would make
discharging slightly less attractive at the margin and could shift a few boundary intervals. I
went with symmetric because it's the most defensible default, but it's a modelling choice, not
a physical fact.

**What I'd build next with another day**

Forecast-aware dispatch. The what-if tool shows that doubling the PV array produces zero extra
savings — the battery fills from the grid overnight and is already full when solar peaks, so
surplus curtails. The fix is a day-ahead solar forecast (Open-Meteo is free): if tomorrow looks
sunny, skip the overnight grid charge and let solar fill the battery instead. That single change
unlocks self-consumption gains that currently sit at zero and is the highest-value improvement
per engineering hour.
