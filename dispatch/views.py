from __future__ import annotations

import plotly.graph_objects as go
from django.shortcuts import render
from django.utils import timezone as tz

from .models import DispatchInterval, EnergyInterval


def weekly_report(request):
    intervals = (
        DispatchInterval.objects
        .select_related("interval")
        .order_by("interval__timestamp")
    )

    if not intervals.exists():
        return render(request, "dispatch/weekly_report.html", {"no_data": True})

    # Aggregate totals
    total_cost        = sum(d.cost_eur for d in intervals)
    total_cf_cost     = sum(d.counterfactual_cost_eur for d in intervals)
    saving_eur        = total_cf_cost - total_cost
    saving_pct        = (saving_eur / total_cf_cost * 100) if total_cf_cost else 0

    total_solar       = sum(d.interval.solar_kw * 0.25 for d in intervals)  # kWh
    total_curtailed   = sum(d.curtailed_kw * 0.25 for d in intervals)
    solar_used_onsite = total_solar - total_curtailed
    self_consumption  = (solar_used_onsite / total_solar * 100) if total_solar else 0

    kwh_charged    = sum(max(0.0, d.battery_kw) * 0.25 for d in intervals)
    kwh_discharged = sum(abs(min(0.0, d.battery_kw)) * 0.25 for d in intervals)

    # Chart data
    timestamps = [tz.localtime(d.interval.timestamp).strftime("%a %d %b %H:%M")
                  for d in intervals]
    soc_values    = [d.soc_pct for d in intervals]
    solar_values  = [d.interval.solar_kw for d in intervals]
    load_values   = [d.interval.load_kw for d in intervals]
    battery_values = [d.battery_kw for d in intervals]
    grid_values   = [d.grid_kw for d in intervals]

    _layout_base = dict(
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(family="system-ui, -apple-system, sans-serif", color="#1e293b", size=12),
        xaxis=dict(showgrid=False, tickangle=-45, tickfont=dict(size=10)),
        margin=dict(t=20, b=80, l=50, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        height=320,
        hovermode="x unified",
    )

    soc_fig = go.Figure()
    soc_fig.add_trace(go.Scatter(
        x=timestamps, y=soc_values,
        mode="lines", name="State of Charge",
        line=dict(color="#2563eb", width=2),
        fill="tozeroy", fillcolor="rgba(37,99,235,0.07)",
    ))
    soc_fig.add_hline(y=10, line_dash="dot", line_color="#ef4444", line_width=1,
                      annotation_text="10% floor", annotation_font_size=11)
    soc_fig.add_hline(y=95, line_dash="dot", line_color="#d97706", line_width=1,
                      annotation_text="95% ceiling", annotation_font_size=11)
    soc_fig.update_layout(
        **_layout_base,
        yaxis=dict(title="SoC (%)", range=[0, 105], gridcolor="#f1f5f9"),
    )

    dispatch_fig = go.Figure()
    dispatch_fig.add_trace(go.Scatter(
        x=timestamps, y=solar_values, mode="lines", name="Solar (kW)",
        line=dict(color="#f59e0b", width=1.5),
    ))
    dispatch_fig.add_trace(go.Scatter(
        x=timestamps, y=load_values, mode="lines", name="Load (kW)",
        line=dict(color="#64748b", width=1.5),
    ))
    dispatch_fig.add_trace(go.Scatter(
        x=timestamps, y=grid_values, mode="lines", name="Grid draw (kW)",
        line=dict(color="#ef4444", width=1.5),
    ))
    dispatch_fig.add_trace(go.Scatter(
        x=timestamps, y=battery_values, mode="lines", name="Battery (kW +charge/−discharge)",
        line=dict(color="#2563eb", width=1.5, dash="dot"),
    ))
    dispatch_fig.update_layout(
        **_layout_base,
        yaxis=dict(title="Power (kW)", gridcolor="#f1f5f9"),
    )

    context = {
        "no_data": False,
        "week_label": "22–28 July 2024",
        "total_cost": round(total_cost, 2),
        "total_cf_cost": round(total_cf_cost, 2),
        "saving_eur": round(saving_eur, 2),
        "saving_pct": round(saving_pct, 1),
        "annual_saving": round(saving_eur * 52, 0),
        "kwh_charged": round(kwh_charged, 1),
        "kwh_discharged": round(kwh_discharged, 1),
        "self_consumption": round(self_consumption, 1),
        "total_solar_kwh": round(total_solar, 1),
        "total_curtailed_kwh": round(total_curtailed, 1),
        # Pass raw JSON strings — Django would mangle Python dicts into invalid JS otherwise
        "soc_chart_json": soc_fig.to_json(),
        "dispatch_chart_json": dispatch_fig.to_json(),
    }
    return render(request, "dispatch/weekly_report.html", context)