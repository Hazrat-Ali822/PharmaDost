from django.urls import path
from . import views

urlpatterns = [
    path("", views.customer_list, name="customer_list"),
    path("add/", views.customer_create, name="customer_add"),
    path("<int:pk>/edit/", views.customer_edit, name="customer_edit"),
    path("<int:pk>/ledger/", views.customer_ledger, name="customer_ledger"),
    path("<int:pk>/payment/", views.payment_create, name="customer_payment_add"),
]
