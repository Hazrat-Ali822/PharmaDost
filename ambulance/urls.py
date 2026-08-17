from django.urls import path

from . import views

app_name = 'ambulance'

urlpatterns = [
    path('', views.dispatch_board, name='dispatch_board'),
    path('trips/', views.trip_list, name='trip_list'),
    path('trips/new/', views.trip_create, name='trip_create'),
    path('trips/<int:pk>/', views.trip_detail, name='trip_detail'),
    path('trips/<int:pk>/complete/', views.trip_complete, name='trip_complete'),
    path('trips/<int:pk>/cancel/', views.trip_cancel, name='trip_cancel'),
    path('fleet/', views.fleet_list, name='fleet_list'),
    path('fleet/new/', views.ambulance_form, name='ambulance_create'),
    path('fleet/<int:pk>/edit/', views.ambulance_form, name='ambulance_edit'),
    path('drivers/new/', views.driver_form, name='driver_create'),
    path('drivers/<int:pk>/edit/', views.driver_form, name='driver_edit'),
]
