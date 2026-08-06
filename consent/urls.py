from django.urls import path

from . import views

urlpatterns = [
    path('', views.consent_list, name='consent_list'),
    path('new/', views.consent_create, name='consent_create'),
    path('templates/', views.template_list, name='consent_templates'),
    path('<int:pk>/print/', views.consent_print, name='consent_print'),
]
