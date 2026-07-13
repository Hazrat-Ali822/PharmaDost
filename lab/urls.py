# lab/urls.py
from django.urls import path
from . import views

app_name = "lab"

urlpatterns = [
    path("tests/", views.test_catalog, name="test_catalog"),
    path("orders/", views.order_list, name="order_list"),
    path("orders/new/", views.order_create, name="order_create"),
    path("orders/<int:order_id>/", views.order_detail, name="order_detail"),
    path("orders/<int:order_id>/edit-results/", views.order_results_edit, name="order_results_edit"),
    path("orders/<int:order_id>/complete/", views.order_mark_completed, name="order_mark_completed"),
    path("orders/<int:order_id>/report/", views.order_report, name="order_report"),
]
