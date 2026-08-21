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
    # The status dropdown on /opd/appointments/ posts here. The view existed and
    # the JS called it, but the route was never registered — so changing an
    # appointment's status from the list has always answered 404 and shown
    # "Failed to update status. Please try again."
    path('appointments/<int:pk>/status/', views.appointment_update_status,
         name='appointment_update_status'),
    path('payouts/', views.payout_list, name='payout_list'),
    path('payouts/<int:pk>/', views.payout_doctor, name='payout_doctor'),
    path('tv/', views.opd_tv_display, name='opd_tv_display'),
    path('tv/api/', views.opd_tv_api, name='opd_tv_api'),
    path('track/<uuid:track_token>/', views.patient_token_track, name='patient_token_track'),
]
