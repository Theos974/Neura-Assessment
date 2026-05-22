from django.urls import path

from . import views

urlpatterns = [
    path("weekly/", views.weekly_report, name="weekly_report"),
]
