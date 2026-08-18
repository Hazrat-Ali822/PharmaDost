from django.urls import path

from . import views, views_biometric

urlpatterns = [
    path('', views.staff_list, name='hr_staff_list'),
    path('staff/add/', views.employee_add, name='hr_employee_add'),
    path('staff/<int:user_id>/edit/', views.profile_edit, name='hr_profile_edit'),
    path('attendance/', views.attendance_day, name='hr_attendance'),
    path('attendance/summary/', views.attendance_summary, name='hr_attendance_summary'),
    path('leave/', views.leave_list, name='hr_leave_list'),
    path('leave/new/', views.leave_create, name='hr_leave_create'),
    path('leave/<int:pk>/<str:decision>/', views.leave_decide, name='hr_leave_decide'),
    path('shifts/', views.shift_list, name='hr_shift_list'),
    path('shifts/<int:pk>/delete/', views.shift_delete, name='hr_shift_delete'),
    # Attendance machine. The endpoint the device itself talks to is NOT here —
    # the firmware hard-codes /iclock/, so it is registered at the site root.
    path('devices/', views_biometric.device_list, name='hr_biometric_devices'),
    path('devices/enrolment/', views_biometric.enrolment_map, name='hr_biometric_enrolment'),
    path('devices/build/', views_biometric.build_attendance, name='hr_biometric_build'),
    path('devices/punches/', views_biometric.punch_list, name='hr_biometric_punches'),
    path('salary/', views.salary_list, name='hr_salary_list'),
    path('salary/new/', views.salary_create, name='hr_salary_create'),
    path('salary/<int:pk>/slip/', views.salary_slip, name='hr_salary_slip'),
]
