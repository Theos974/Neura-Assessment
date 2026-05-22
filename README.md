# Neura Take-Home: BTM Battery Dispatch — Limassol Hotel

A Django service that simulates a behind-the-meter battery at a 4-star Limassol hotel,
reports weekly grid savings, and serves a dispatch chart at `/reports/weekly/`.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # edit if you want a real secret key
python manage.py migrate
python manage.py seed_data
python manage.py runserver
```

Then open http://127.0.0.1:8000/reports/weekly/

## Data sources

### Solar (`solar_kw`)
Source: [renewables.ninja](https://www.renewables.ninja) — MERRA-2 satellite weather data,
Limassol lat/lon (34.6786, 33.0413), 200 kWp system, 30° tilt, south-facing, 10% system loss.

If `NINJA_TOKEN` is set in `.env`, `seed_data` fetches directly from the API.
Otherwise it falls back to a synthetic clear-sky model (see below).

To pre-fetch and cache the raw hourly CSV:
```bash
NINJA_TOKEN=your_token python data/fetch_solar.py
```

**Resampling:** renewables.ninja returns hourly values. We resample to 15-min via linear
interpolation between adjacent hours. This gives a smooth ramp at sunrise/sunset rather
than a staircase. The interpolated values are stored in the database.

### Load (`load_kw`)
No clean public 15-min dataset exists for a Cyprus hotel. Synthetic profile built from:

- **Shape:** occupancy-driven 24h profile with a midday-to-afternoon cooling spike
  (Cyprus July ambient ~40°C, HVAC is the dominant load). Shape cross-checked against
  the DOE/OpenEI commercial large hotel reference building.
- **Daily pattern:**
  - Night 00:00–06:00: ~60–65 kW base (fridges, pumps, servers, lighting)
  - Morning 06:00–10:00: ramp to ~110–120 kW (kitchens, HVAC start)
  - Afternoon peak 12:00–16:00: up to 200 kW (full HVAC, pools, restaurant)
  - Evening 18:00–22:00: ~130–155 kW (dinner service)
- **Weekly pattern:** Friday highest occupancy (multiplier 1.0), Monday lowest (0.92)
- **Scaling:** base profile set so the weekly peak (Friday ~14:00) hits exactly 200 kW,
  matching the hotel's stated peak draw in the spec.

### Grid price (`grid_price_eur_per_kwh`)
Stylised 2-rate TOU modelled on EAC residential Code 02:
- Day 09:00–23:00 Cyprus local time: **€0.30/kWh**
- Night 23:00–09:00 Cyprus local time: **€0.15/kWh**

All price comparisons use the same tariff for both the with-battery and no-battery cases.

## Dispatch algorithm

Greedy BTM policy, run at every 15-min step:

1. Solar covers load first (always free)
2. Surplus solar charges battery (up to 95% SoC, 200 kW max)
3. Unmet load during peak hours (09:00–23:00) draws from battery (down to 10% SoC, 200 kW max)
4. Remaining unmet load pulled from grid
5. Solar that battery can't absorb is curtailed — no grid export (Cyprus commercial net-metering restriction)

**Why no off-peak discharge:** overnight grid is €0.15/kWh. Discharging the battery at night saves
half as much per kWh as discharging during the day rate. The better strategy is to preserve state
of charge for the expensive daytime period.

**Round-trip efficiency:** 88% split symmetrically — charge efficiency √0.88 ≈ 93.8%,
discharge efficiency √0.88 ≈ 93.8%. Applied in the SoC maths so the limits are never violated.

**Battery spec:** 400 kWh capacity, 200 kW max charge/discharge power.

## Representative week

**22–28 July 2024** — chosen as a typical hot summer week in Cyprus.
Peak solar: ~156 kW AC (Monday). Peak load: ~200 kW (Friday afternoon).

## Tests

```bash
python manage.py test dispatch
```

Tests cover the `_greedy_step()` pure function: SoC bounds, power caps, off-peak behaviour,
curtailment, no grid export, and efficiency accounting.

## What I'd build next

1. **LP solver for optimal dispatch.** The greedy policy is fast and defensible for a demo
   but leaves money on the table. A simple linear programme (PuLP or scipy) with a 24h
   lookahead could plan charging around known tariff transitions rather than reacting step-by-step.

2. **Real EAC commercial tariff.** The current TOU is stylised residential Code 02. EAC publish
   commercial tariffs (Code 08 / Code 38) as PDFs at eac.com.cy — parsing those and re-running
   the dispatch would give a real savings figure to show financiers.

3. **Live data ingestion.** Replace the seed command with a scheduled task that pulls from a
   smart meter API (or a SCADA feed) every 15 minutes, stores fresh `EnergyInterval` rows,
   and re-runs dispatch automatically.

4. **What-if form.** A small Django form to vary battery size and PV capacity and see how weekly
   saving shifts — useful for investment conversations.

5. **Forecasting.** The greedy policy reacts to the present. A day-ahead solar and load forecast
   (even a simple one from Open-Meteo) would let us pre-position the battery before the expensive
   afternoon period begins.

## How I used AI

Claude Code (claude-sonnet-4-6) was used throughout via the VSCode extension. It wrote the
initial scaffolding and most of the boilerplate, which I reviewed and corrected where the logic
was wrong (e.g. the peak-hour check needed to use `localtime()` not raw UTC hours). The dispatch
algorithm and efficiency maths I worked through myself and then had Claude translate into code.
The tests I wrote to verify the edge cases I cared about, with Claude filling in the assertions.
