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
    path('<int:pk>/summary/', views.discharge_summary, name='discharge_summary'),
    path('wards/', views.ward_bed_list, name='ward_bed_list'),
    path('wards/new/', views.ward_create, name='ward_create'),
    path('beds/new/', views.bed_create, name='bed_create'),
    path('beds/<int:pk>/edit/', views.bed_edit, name='bed_edit'),
    path('beds/<int:pk>/delete/', views.bed_delete, name='bed_delete'),
    # Nursing / ward management
    path('roster/', views.duty_roster, name='duty_roster'),
    path('roster/add/', views.roster_add, name='roster_add'),
    path('roster/<int:pk>/remove/', views.roster_remove, name='roster_remove'),
    path('allocation/', views.patient_allocation, name='patient_allocation'),
    path('my-duties/', views.my_duties, name='my_duties'),
    path('board/', views.nursing_board, name='nursing_board'),
    path('handover/', views.handover_board, name='handover_board'),
    path('handover/<int:pk>/ack/', views.handover_ack, name='handover_ack'),
    path('census/', views.ward_census_view, name='ward_census'),
    path('<int:pk>/vitals/', views.vitals_add, name='vitals_add'),
    path('<int:pk>/fluid/', views.fluid_add, name='fluid_add'),
    path('<int:pk>/note/', views.nursing_note_add, name='nursing_note_add'),
    path('<int:pk>/care-task/', views.care_task_add, name='care_task_add'),
    path('<int:pk>/handover/', views.handover_add, name='handover_add'),
]
