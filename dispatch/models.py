from django.db import models


class EnergyInterval(models.Model):
    """One 15-minute slot of raw input data: solar, load, and grid price."""

    timestamp = models.DateTimeField(unique=True, db_index=True)
    solar_kw = models.FloatField(help_text="PV output (kW)")
    load_kw = models.FloatField(help_text="Hotel demand (kW)")
    grid_price_eur_per_kwh = models.FloatField(help_text="TOU grid price (€/kWh)")

    class Meta:
        ordering = ["timestamp"]

    def __str__(self) -> str:
        return f"{self.timestamp:%Y-%m-%d %H:%M} | solar={self.solar_kw:.1f} load={self.load_kw:.1f}"


class DispatchInterval(models.Model):
    """Result of running the BTM dispatch policy over one EnergyInterval."""

    interval = models.OneToOneField(
        EnergyInterval, on_delete=models.CASCADE, related_name="dispatch"
    )
    # positive = charging, negative = discharging
    battery_kw = models.FloatField(help_text="Battery power (kW); +charge/-discharge")
    soc_pct = models.FloatField(help_text="State of charge at end of interval (%)")
    grid_kw = models.FloatField(help_text="Net grid draw (kW); always >= 0 (no export)")
    curtailed_kw = models.FloatField(help_text="Solar curtailed (kW)")
    cost_eur = models.FloatField(help_text="Grid spend this interval (€)")
    counterfactual_cost_eur = models.FloatField(
        help_text="Grid spend without battery this interval (€)"
    )

    class Meta:
        ordering = ["interval__timestamp"]

    def __str__(self) -> str:
        return (
            f"{self.interval.timestamp:%Y-%m-%d %H:%M} | "
            f"batt={self.battery_kw:+.1f} soc={self.soc_pct:.1f}%"
        )
