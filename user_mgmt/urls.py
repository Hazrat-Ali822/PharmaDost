from django.urls import path
from . import views


app_name = 'user_mgmt'
urlpatterns = [
  path('dashboard/', views.dashboard_router, name='post_login_redirect'),
  path('dashboard/admin/', views.admin_dashboard, name='admin_dashboard'),
  path('dashboard/manager/', views.manager_dashboard, name='manager_dashboard'),
  path('dashboard/pharmacist/', views.pharmacist_dashboard, name='pharmacist_dashboard'),
  path('dashboard/lab/', views.lab_dashboard, name='lab_dashboard'),
  path('dashboard/sonographer/', views.sonographer_dashboard, name='sonographer_dashboard'),
  path('settings/', views.site_settings, name='site_settings'),
  path('backup/', views.backup_download, name='backup_download'),
  path('users/', views.user_list, name='user_list'),
  path('users/new/', views.user_create, name='user_create'),
  path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
]