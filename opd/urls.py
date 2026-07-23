from django.urls import path
from . import views

urlpatterns = [
    path('doctors/', views.doctor_list, name='doctor_list'),
    path('doctors/add/', views.doctor_create, name='doctor_add'),
    path('doctors/<int:pk>/edit/', views.doctor_edit, name='doctor_edit'),
    path('doctors/<int:pk>/delete/', views.doctor_delete, name='doctor_delete'),
    path('departments/', views.department_list, name='department_list'),
    path('departments/<int:pk>/delete/', views.department_delete, name='department_delete'),
    # front desk: find or register a patient, then book them in
    path('reception/', views.reception_desk, name='reception_desk'),
    path('reception/visit/', views.visit_create, name='visit_create'),
    path('board/', views.doctor_availability_board, name='doctor_availability_board'),
    path('board/<int:pk>/toggle/', views.doctor_availability_toggle, name='doctor_availability_toggle'),
    path('appointments/', views.appointment_list, name='appointment_list'),
    path('appointments/add/', views.appointment_create, name='appointment_add'),
    path('appointments/<int:pk>/slip/', views.appointment_slip, name='appointment_slip'),
    path('payouts/', views.payout_list, name='payout_list'),
    path('payouts/<int:pk>/', views.payout_doctor, name='payout_doctor'),
    path('appointments/<int:pk>/status/', views.appointment_update_status, name='appointment_update_status'),
]
