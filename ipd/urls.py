from django.urls import path
from . import views

app_name = 'ipd'

urlpatterns = [
    path('', views.admission_list, name='admission_list'),
    path('requests/', views.admission_request_list, name='admission_request_list'),
    path('requests/<int:pk>/cancel/', views.admission_request_cancel, name='admission_request_cancel'),
    path('advise/<int:patient_id>/', views.admission_advise, name='admission_advise'),
    path('new/', views.admission_create, name='admission_create'),
    path('<int:pk>/', views.admission_detail, name='admission_detail'),
    path('<int:pk>/round/', views.doctor_round_add, name='doctor_round_add'),
    path('<int:pk>/medication/', views.medication_log_add, name='medication_log_add'),
    path('<int:pk>/discharge/', views.admission_discharge, name='admission_discharge'),
    path('wards/', views.ward_bed_list, name='ward_bed_list'),
    path('wards/new/', views.ward_create, name='ward_create'),
    path('beds/new/', views.bed_create, name='bed_create'),
    path('beds/<int:pk>/edit/', views.bed_edit, name='bed_edit'),
    path('beds/<int:pk>/delete/', views.bed_delete, name='bed_delete'),
]
