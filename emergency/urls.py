from django.urls import path

from . import views

urlpatterns = [
    path('', views.emergency_board, name='emergency_board'),
    path('new/', views.emergency_intake, name='emergency_intake'),
    path('<int:pk>/', views.emergency_detail, name='emergency_detail'),
]
