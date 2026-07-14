from django.urls import path
from . import views

urlpatterns = [
    path('', views.prescription_list, name='prescription_list'),
    path('appointment/<int:appointment_id>/', views.prescription_create, name='prescription_create'),
    path('<int:pk>/', views.prescription_detail, name='prescription_detail'),
    path('<int:pk>/edit/', views.prescription_edit, name='prescription_edit'),
]
