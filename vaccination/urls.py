from django.urls import path

from . import views

urlpatterns = [
    path('', views.vaccination_list, name='vaccination_list'),
    path('due/', views.due_list, name='vaccination_due'),
    path('catalogue/', views.vaccine_catalogue, name='vaccine_catalogue'),
    path('patient/<int:patient_id>/', views.patient_card, name='vaccination_card'),
]
