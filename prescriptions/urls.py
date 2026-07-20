from django.urls import path
from . import views

urlpatterns = [
    path('', views.prescription_list, name='prescription_list'),
    path('appointment/<int:appointment_id>/', views.prescription_create, name='prescription_create'),
    path('<int:pk>/', views.prescription_detail, name='prescription_detail'),
    path('<int:pk>/labels/', views.prescription_labels, name='prescription_labels'),
    path('<int:pk>/edit/', views.prescription_edit, name='prescription_edit'),
    path('presets/', views.preset_list, name='prescription_presets'),
    path('presets/add/', views.preset_create, name='preset_create'),
    path('presets/<int:pk>/edit/', views.preset_edit, name='preset_edit'),
    path('presets/<int:pk>/delete/', views.preset_delete, name='preset_delete'),
]
