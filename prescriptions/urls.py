from django.urls import path
from . import views

urlpatterns = [
    path('', views.prescription_list, name='prescription_list'),
    path('appointment/<int:appointment_id>/', views.prescription_create, name='prescription_create'),
]
