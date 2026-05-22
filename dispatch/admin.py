from django.contrib import admin

from .models import DispatchInterval, EnergyInterval


@admin.register(EnergyInterval)
class EnergyIntervalAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "solar_kw", "load_kw", "grid_price_eur_per_kwh")
    list_filter = ("timestamp",)


@admin.register(DispatchInterval)
class DispatchIntervalAdmin(admin.ModelAdmin):
    list_display = ("interval", "battery_kw", "soc_pct", "grid_kw", "curtailed_kw", "cost_eur")
    list_filter = ("interval__timestamp",)
