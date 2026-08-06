from django.urls import path

from . import views

urlpatterns = [
    path('', views.certificate_list, name='certificate_list'),
    path('birth/new/', views.birth_create, name='birth_create'),
    path('death/new/', views.death_create, name='death_create'),
    path('birth/<int:pk>/', views.birth_certificate, name='birth_certificate'),
    path('death/<int:pk>/', views.death_certificate, name='death_certificate'),
]
