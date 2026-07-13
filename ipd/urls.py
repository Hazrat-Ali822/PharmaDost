from django.urls import path
from . import views

app_name = 'ipd'

urlpatterns = [
    path('', views.admission_list, name='admission_list'),
    path('new/', views.admission_create, name='admission_create'),
    path('<int:pk>/', views.admission_detail, name='admission_detail'),
    path('<int:pk>/round/', views.doctor_round_add, name='doctor_round_add'),
    path('<int:pk>/discharge/', views.admission_discharge, name='admission_discharge'),
    path('wards/', views.ward_bed_list, name='ward_bed_list'),
    path('wards/new/', views.ward_create, name='ward_create'),
    path('beds/new/', views.bed_create, name='bed_create'),
]
