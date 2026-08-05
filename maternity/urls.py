from django.urls import path

from . import views

urlpatterns = [
    path('', views.maternity_list, name='maternity_list'),
    path('register/', views.pregnancy_register, name='maternity_register'),
    path('pregnancy/<int:pk>/', views.pregnancy_detail, name='maternity_pregnancy'),
    path('delivery/new/', views.delivery_record, name='maternity_delivery_new'),
    path('pregnancy/<int:pregnancy_id>/delivery/', views.delivery_record, name='maternity_delivery'),
    path('births/', views.birth_register, name='maternity_birth_register'),
]
