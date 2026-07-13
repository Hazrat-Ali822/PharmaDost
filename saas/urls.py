from django.urls import path
from . import views

app_name = 'saas'

urlpatterns = [
    path('', views.saas_dashboard, name='dashboard'),
    path('hospital/new/', views.hospital_create, name='hospital_create'),
    path('hospital/<int:pk>/edit/', views.hospital_edit, name='hospital_edit'),
    path('payment/new/', views.payment_create, name='payment_create'),
    path('expense/new/', views.expense_create, name='expense_create'),
]
