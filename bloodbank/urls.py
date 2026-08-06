from django.urls import path

from . import views

urlpatterns = [
    path('', views.bloodbank_dashboard, name='bloodbank_dashboard'),
    path('donors/', views.donor_list, name='bloodbank_donors'),
    path('units/', views.unit_list, name='bloodbank_units'),
    path('units/add/', views.unit_add, name='bloodbank_unit_add'),
    path('units/<int:pk>/discard/', views.unit_discard, name='bloodbank_unit_discard'),
    path('issue/', views.issue_create, name='bloodbank_issue'),
]
