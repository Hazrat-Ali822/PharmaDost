from django.urls import path
from . import views

urlpatterns = [
    path('', views.invoice_list, name='invoice_list'),
    path('create/', views.invoice_create, name='invoice_create'),
    path('create/<int:appointment_id>/', views.invoice_create, name='invoice_create_for_appointment'),
    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/new/', views.expense_create, name='expense_create'),
    path('cash-closing/', views.cash_closing_list, name='cash_closing_list'),
    path('cash-closing/new/', views.cash_closing_new, name='cash_closing_new'),
    path('patients/', views.patient_billing_list, name='patient_billing_list'),
    path('patient/<int:pk>/', views.patient_bill, name='patient_bill'),
    path('patient/<int:pk>/print/', views.patient_bill_print, name='patient_bill_print'),
    path('<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('<int:pk>/mark-paid/', views.invoice_mark_paid, name='invoice_mark_paid'),
]
