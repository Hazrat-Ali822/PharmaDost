from django.urls import path
from . import views

app_name = 'ot'

urlpatterns = [
    path('', views.surgery_list, name='surgery_list'),
    path('new/', views.surgery_create, name='surgery_create'),
    path('<int:pk>/', views.surgery_detail, name='surgery_detail'),
    path('<int:pk>/edit/', views.surgery_edit, name='surgery_edit'),
    path('procedures/', views.procedure_list, name='procedure_list'),
    path('procedures/new/', views.procedure_create, name='procedure_create'),
    path('categories/new/', views.category_create, name='category_create'),
]
