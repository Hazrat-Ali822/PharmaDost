from django.urls import path
from . import views

app_name = 'saas'

urlpatterns = [
    path('', views.saas_dashboard, name='dashboard'),
    path('hospital/new/', views.hospital_create, name='hospital_create'),
    path('hospital/<int:pk>/', views.hospital_detail, name='hospital_detail'),
    path('hospital/<int:pk>/edit/', views.hospital_edit, name='hospital_edit'),
    path('hospital/<int:pk>/renew/', views.hospital_renew, name='hospital_renew'),
    path('hospital/<int:pk>/desktop-license/', views.hospital_desktop_license, name='hospital_desktop_license'),
    path('hospital/<int:pk>/delete/', views.hospital_delete, name='hospital_delete'),
    path('payment/new/', views.payment_create, name='payment_create'),
    path('payment/<int:pk>/invoice/', views.payment_invoice, name='payment_invoice'),
    path('expense/new/', views.expense_create, name='expense_create'),
    path('desktop-license/', views.desktop_license, name='desktop_license'),
    path('backups/', views.backup_list, name='backup_list'),
    path('backups/<int:pk>/download/', views.backup_download_file, name='backup_download_file'),
    path('backup/upload/', views.backup_upload, name='backup_upload'),
]
