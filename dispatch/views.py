from __future__ import annotations

from collections import defaultdict
from datetime import date as dt_date

import plotly.graph_objects as go
from django.shortcuts import render
from django.utils import timezone as tz

from .models import DispatchInterval, EnergyInterval
from .policy import dispatch_scenario
from .tariffs import COMMERCIAL_CODE_30, RESIDENTIAL_CODE_02, TouTariff


def weekly_report(request):
    qs = (
        DispatchInterval.objects
        .select_related("interval")
        .order_by("interval__timestamp")
    )
    if not qs.exists():
        return render(request, "dispatch/weekly_report.html", {"no_data": True})

    all_rows = list(qs)

    # Use distinct local dates to define the week boundaries. The last UTC row
    # falls on local Jul 29 (UTC+3 offset), so taking the raw last-row date would
    # give Jul 29 as the default upper bound and leak those off-peak charging
    # intervals into the chart. Taking the 7th distinct date (index 6) gives Jul 28.
    all_local_dates = sorted({tz.localtime(d.interval.timestamp).date() for d in all_rows})
    first_date = all_local_dates[0]
    last_date  = all_local_dates[min(6, len(all_local_dates) - 1)]

    try:
        from_date = dt_date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        from_date = first_date
    try:
        to_date = dt_date.fromisoformat(request.GET.get("to", ""))
    except ValueError:
        to_date = last_date

    from_date = max(from_date, first_date)
    to_date   = min(to_date,   last_date)

    rows = [
        d for d in all_rows
        if from_date <= tz.localtime(d.interval.timestamp).date() <= to_date
    ] or all_rows

    # Primary financials
    total_cost    = sum(d.cost_eur for d in rows)
    total_cf_cost = sum(d.counterfactual_cost_eur for d in rows)
    saving_eur    = total_cf_cost - total_cost
    saving_pct    = (saving_eur / total_cf_cost * 100) if total_cf_cost else 0

    # Energy metrics
    total_solar      = sum(d.interval.solar_kw * 0.25 for d in rows)
    total_curtailed  = sum(d.curtailed_kw * 0.25 for d in rows)
    self_consumption = ((total_solar - total_curtailed) / total_solar * 100) if total_solar else 0
    kwh_charged      = sum(max(0.0, d.battery_kw) * 0.25 for d in rows)
    kwh_discharged   = sum(abs(min(0.0, d.battery_kw)) * 0.25 for d in rows)

    # Commercial tariff comparison
    comm = _tariff_costs(rows, COMMERCIAL_CODE_30)

    # Per-day savings and dispatch timeline (grouped by local date)
    _date_savings: dict = defaultdict(float)
    _date_tl: dict      = defaultdict(
        lambda: {"grid_charge": 0, "solar_charge": 0, "discharge": 0, "idle": 0, "total": 0}
    )
    for d in rows:
        local_dt = tz.localtime(d.interval.timestamp)
        key = local_dt.date()
        _date_savings[key] += d.counterfactual_cost_eur - d.cost_eur
        tl = _date_tl[key]
        tl["total"] += 1
        if d.battery_kw > 0.1:
            tl["solar_charge" if d.interval.solar_kw > d.interval.load_kw else "grid_charge"] += 1
        elif d.battery_kw < -0.1:
            tl["discharge"] += 1
        else:
            tl["idle"] += 1

    sorted_dates = [d for d in sorted(_date_savings) if from_date <= d <= to_date]
    daily_timeline = []
    for date in sorted_dates:
        tl = _date_tl[date]
        n  = tl["total"] or 1
        daily_timeline.append({
            "day":             date.strftime("%a %d"),
            "grid_charge_pct": round(tl["grid_charge"]  / n * 100, 1),
            "solar_pct":       round(tl["solar_charge"] / n * 100, 1),
            "discharge_pct":   round(tl["discharge"]    / n * 100, 1),
            "idle_pct":        round(tl["idle"]         / n * 100, 1),
        })

    # Dispatch intervals table — filterable by local hour range
    try:
        hour_from = max(0, min(23, int(request.GET.get("hour_from", 0))))
    except (TypeError, ValueError):
        hour_from = 0
    try:
        hour_to = max(0, min(23, int(request.GET.get("hour_to", 23))))
    except (TypeError, ValueError):
        hour_to = 23

    table_rows = [
        d for d in rows
        if hour_from <= tz.localtime(d.interval.timestamp).hour <= hour_to
    ]
    recent_intervals = []
    for d in table_rows[-50:]:
        local_dt = tz.localtime(d.interval.timestamp)
        bkw      = d.battery_kw
        action   = "charge" if bkw > 0.1 else ("discharge" if bkw < -0.1 else "idle")
        recent_intervals.append({
            "time":    local_dt.strftime("%d %b %H:%M"),
            "action":  action,
            "solar":   round(d.interval.solar_kw, 1),
            "load":    round(d.interval.load_kw, 1),
            "battery": round(bkw, 1),
            "soc":     round(d.soc_pct, 1),
            "grid":    round(d.grid_kw, 1),
            "price":   round(d.interval.grid_price_eur_per_kwh, 2),
        })

    # Plotly charts
    timestamps     = [tz.localtime(d.interval.timestamp).strftime("%a %d %b %H:%M") for d in rows]
    _layout_base   = _chart_layout()

    soc_fig = go.Figure()
    soc_fig.add_trace(go.Scatter(
        x=timestamps, y=[d.soc_pct for d in rows],
        mode="lines", name="SoC",
        line=dict(color="#b5740b", width=1.5),
        fill="tozeroy", fillcolor="rgba(181,116,11,0.10)",
    ))
    soc_fig.add_hline(y=10, line_dash="dot", line_color="#b03a3a", line_width=1,
                      annotation_text="10% floor", annotation_font_size=10)
    soc_fig.add_hline(y=95, line_dash="dot", line_color="#b5740b", line_width=1,
                      annotation_text="95% ceiling", annotation_font_size=10)
    soc_fig.update_layout(**_layout_base, yaxis=dict(title="SoC (%)", range=[0, 105], gridcolor="#f0f0ee"))

    dispatch_fig = go.Figure()
    dispatch_fig.add_trace(go.Scatter(
        x=timestamps, y=[d.interval.solar_kw for d in rows], mode="lines", name="Solar",
        line=dict(color="#2f7d54", width=1.5), fill="tozeroy", fillcolor="rgba(47,125,84,0.08)",
    ))
    dispatch_fig.add_trace(go.Scatter(
        x=timestamps, y=[d.interval.load_kw for d in rows], mode="lines", name="Load",
        line=dict(color="#1a1a1a", width=1.5, dash="dot"),
    ))
    dispatch_fig.add_trace(go.Scatter(
        x=timestamps, y=[d.grid_kw for d in rows], mode="lines", name="Grid",
        line=dict(color="#b03a3a", width=1.5),
    ))
    dispatch_fig.add_trace(go.Scatter(
        x=timestamps, y=[d.battery_kw for d in rows], mode="lines", name="Battery",
        line=dict(color="#1f5fbf", width=1.5, dash="dot"),
    ))
    dispatch_fig.update_layout(**_layout_base, yaxis=dict(title="Power (kW)", gridcolor="#f0f0ee"))

    savings_fig = go.Figure()
    savings_fig.add_trace(go.Bar(
        x=[d.strftime("%a %d") for d in sorted_dates],
        y=[round(_date_savings[d], 2) for d in sorted_dates],
        marker_color="#2f7d54", marker_line_width=0,
    ))
    savings_fig.update_layout(
        paper_bgcolor="white", plot_bgcolor="white",
        font=dict(family="system-ui, -apple-system, sans-serif", color="#6b6b6b", size=11),
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="#f0f0ee", tickprefix="€"),
        margin=dict(t=10, b=30, l=48, r=10),
        autosize=True, showlegend=False,
    )

    context = {
        "no_data": False,
        "active_page": "overview",
        "week_label": f"{from_date.strftime('%d %b').lstrip('0')}–{to_date.strftime('%d %b %Y').lstrip('0')}",
        "from_date":  str(from_date),
        "to_date":    str(to_date),
        "first_date": str(first_date),
        "last_date":  str(last_date),
        "total_cost":       round(total_cost, 2),
        "total_cf_cost":    round(total_cf_cost, 2),
        "saving_eur":       round(saving_eur, 2),
        "saving_pct":       round(saving_pct, 1),
        "annual_saving":    round(saving_eur * 52),
        "kwh_charged":      round(kwh_charged, 1),
        "kwh_discharged":   round(kwh_discharged, 1),
        "self_consumption": round(self_consumption, 1),
        "total_solar_kwh":  round(total_solar, 1),
        "comm_saving_eur":      round(comm["saving"], 2),
        "comm_saving_pct":      round(comm["saving_pct"], 1),
        "comm_annual_saving":   round(comm["saving"] * 52),
        "comm_tariff_source":   COMMERCIAL_CODE_30.source,
        "comm_wd_peak_rate":    COMMERCIAL_CODE_30.summer_wd_peak,
        "comm_wd_offpeak_rate": COMMERCIAL_CODE_30.summer_wd_offpeak,
        "comm_we_peak_rate":    COMMERCIAL_CODE_30.summer_we_peak,
        "comm_we_offpeak_rate": COMMERCIAL_CODE_30.summer_we_offpeak,
        "daily_timeline":    daily_timeline,
        "recent_intervals":  recent_intervals,
        "hour_from":         hour_from,
        "hour_to":           hour_to,
        "hour_options":      list(range(24)),
        "soc_chart_json":      soc_fig.to_json(),
        "dispatch_chart_json": dispatch_fig.to_json(),
        "savings_chart_json":  savings_fig.to_json(),
    }
    return render(request, "dispatch/weekly_report.html", context)


def whatif_report(request):
    energy_intervals = list(EnergyInterval.objects.order_by("timestamp"))
    if not energy_intervals:
        return render(request, "dispatch/whatif.html", {"no_data": True})

    # Parse and clamp form params
    def _float(key, default, lo, hi):
        try:
            return max(lo, min(hi, float(request.GET.get(key, default))))
        except (TypeError, ValueError):
            return float(default)

    battery_kwh  = _float("battery_kwh",  400,   50, 4000)
    battery_kw   = _float("battery_kw",   200,   10, 2000)
    pv_kwp       = _float("pv_kwp",       200,   50, 2000)

    pv_scale = pv_kwp / 200.0

    # Apply same 7-day local-date window used by weekly_report so the baseline
    # is consistent (excludes the 12 UTC+3 overflow intervals on local Jul 29).
    all_local_dates = sorted({tz.localtime(ei.timestamp).date() for ei in energy_intervals})
    wi_first = all_local_dates[0]
    wi_last  = all_local_dates[min(6, len(all_local_dates) - 1)]

    energy_intervals = [
        ei for ei in energy_intervals
        if wi_first <= tz.localtime(ei.timestamp).date() <= wi_last
    ]

    # Baseline from stored dispatch
    baseline_rows = list(
        DispatchInterval.objects.select_related("interval").order_by("interval__timestamp")
    )
    baseline_rows = [
        d for d in baseline_rows
        if wi_first <= tz.localtime(d.interval.timestamp).date() <= wi_last
    ]
    baseline_cost   = sum(d.cost_eur for d in baseline_rows)
    baseline_cf     = sum(d.counterfactual_cost_eur for d in baseline_rows)
    baseline_saving = baseline_cf - baseline_cost

    # Scenario computed in memory
    steps = dispatch_scenario(energy_intervals, battery_kwh, battery_kw, pv_scale)

    scenario_cost    = sum(s["cost_eur"] for s in steps)
    scenario_cf_cost = sum(s["counterfactual_cost_eur"] for s in steps)
    scenario_saving  = scenario_cf_cost - scenario_cost
    scenario_saving_pct = (scenario_saving / scenario_cf_cost * 100) if scenario_cf_cost else 0

    total_solar_sc   = sum(s["solar_kw"] * 0.25 for s in steps)
    total_curtail_sc = sum(s["curtailed_kw"] * 0.25 for s in steps)
    sc_self_cons     = ((total_solar_sc - total_curtail_sc) / total_solar_sc * 100) if total_solar_sc else 0
    sc_kwh_charged   = sum(max(0.0, s["battery_kw"]) * 0.25 for s in steps)
    sc_kwh_discharged= sum(abs(min(0.0, s["battery_kw"])) * 0.25 for s in steps)

    saving_delta     = scenario_saving - baseline_saving
    saving_delta_pct = (saving_delta / baseline_saving * 100) if baseline_saving else 0

    # Per-day savings for comparison chart
    base_daily: dict = defaultdict(float)
    scen_daily: dict = defaultdict(float)
    for d in baseline_rows:
        base_daily[tz.localtime(d.interval.timestamp).date()] += (
            d.counterfactual_cost_eur - d.cost_eur
        )
    for s, ei in zip(steps, energy_intervals):
        scen_daily[tz.localtime(ei.timestamp).date()] += (
            s["counterfactual_cost_eur"] - s["cost_eur"]
        )

    sorted_dates = [d for d in sorted(base_daily) if wi_first <= d <= wi_last]
    date_labels  = [d.strftime("%a %d") for d in sorted_dates]

    comp_fig = go.Figure()
    comp_fig.add_trace(go.Bar(
        name="Current system",
        x=date_labels,
        y=[round(base_daily.get(d, 0), 2) for d in sorted_dates],
        marker_color="#1f5fbf", marker_line_width=0,
    ))
    comp_fig.add_trace(go.Bar(
        name="Scenario",
        x=date_labels,
        y=[round(scen_daily.get(d, 0), 2) for d in sorted_dates],
        marker_color="#2f7d54", marker_line_width=0,
    ))
    comp_fig.update_layout(
        paper_bgcolor="white", plot_bgcolor="white",
        font=dict(family="system-ui, -apple-system, sans-serif", color="#6b6b6b", size=11),
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="#f0f0ee", tickprefix="€"),
        margin=dict(t=10, b=30, l=48, r=10),
        autosize=True, barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(size=11)),
    )

    timestamps = [tz.localtime(ei.timestamp).strftime("%a %d %b %H:%M") for ei in energy_intervals]
    soc_fig = go.Figure()
    soc_fig.add_trace(go.Scatter(
        x=timestamps, y=[s["soc_pct"] for s in steps],
        mode="lines", name="SoC (scenario)",
        line=dict(color="#2f7d54", width=1.5),
        fill="tozeroy", fillcolor="rgba(47,125,84,0.08)",
    ))
    soc_fig.add_hline(y=10, line_dash="dot", line_color="#b03a3a", line_width=1,
                      annotation_text="10% floor", annotation_font_size=10)
    soc_fig.add_hline(y=95, line_dash="dot", line_color="#b5740b", line_width=1,
                      annotation_text="95% ceiling", annotation_font_size=10)
    soc_fig.update_layout(
        **_chart_layout(),
        yaxis=dict(title="SoC (%)", range=[0, 105], gridcolor="#f0f0ee"),
    )

    context = {
        "no_data": False,
        "active_page": "whatif",
        "battery_kwh":  int(battery_kwh),
        "battery_kw":   int(battery_kw),
        "pv_kwp":       int(pv_kwp),
        "baseline_saving":  round(baseline_saving, 2),
        "baseline_annual":  round(baseline_saving * 52),
        "scenario_saving":      round(scenario_saving, 2),
        "scenario_saving_pct":  round(scenario_saving_pct, 1),
        "scenario_annual":      round(scenario_saving * 52),
        "sc_self_cons":         round(sc_self_cons, 1),
        "sc_kwh_charged":       round(sc_kwh_charged, 1),
        "sc_kwh_discharged":    round(sc_kwh_discharged, 1),
        "sc_total_solar":       round(total_solar_sc, 1),
        "saving_delta":         round(saving_delta, 2),
        "saving_delta_pct":     round(abs(saving_delta_pct), 1),
        "saving_delta_positive": saving_delta >= 0,
        "comp_chart_json": comp_fig.to_json(),
        "soc_chart_json":  soc_fig.to_json(),
    }
    return render(request, "dispatch/whatif.html", context)


def _tariff_costs(rows: list[DispatchInterval], tariff: TouTariff) -> dict:
    """Recompute weekly costs for *rows* using a different tariff, same dispatch."""
    cost    = 0.0
    cf_cost = 0.0
    for d in rows:
        local_dt = tz.localtime(d.interval.timestamp)
        price    = tariff.rate_for_dt(local_dt)
        cost    += d.grid_kw * 0.25 * price
        cf_cost += max(0.0, d.interval.load_kw - d.interval.solar_kw) * 0.25 * price
    saving = cf_cost - cost
    return {
        "cost": cost,
        "cf_cost": cf_cost,
        "saving": saving,
        "saving_pct": (saving / cf_cost * 100) if cf_cost else 0,
    }


def _chart_layout() -> dict:
    return dict(
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(family="system-ui, -apple-system, sans-serif", color="#6b6b6b", size=11),
        xaxis=dict(showgrid=False, tickangle=-45, tickfont=dict(size=10)),
        margin=dict(t=10, b=60, l=48, r=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(size=11)),
        autosize=True,
        hovermode="x unified",
    )
