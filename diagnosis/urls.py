from django.urls import path

from . import views

urlpatterns = [
    path('', views.diagnosis_list, name='diagnosis_list'),
    path('catalogue/', views.code_catalogue, name='diagnosis_catalogue'),
    path('patient/<int:patient_id>/', views.patient_diagnoses, name='patient_diagnoses'),
]
