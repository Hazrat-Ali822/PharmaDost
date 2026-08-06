from django.urls import path

from . import views

urlpatterns = [
    path('', views.referral_list, name='referral_list'),
    path('new/', views.referral_create, name='referral_create'),
    path('<int:pk>/', views.referral_detail, name='referral_detail'),
    path('<int:pk>/letter/', views.referral_letter, name='referral_letter'),
]
