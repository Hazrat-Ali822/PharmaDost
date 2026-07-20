from django.urls import path
from . import views

app_name = 'ot'

urlpatterns = [
    path('', views.surgery_list, name='surgery_list'),
    path('requests/', views.surgery_request_list, name='surgery_request_list'),
    path('requests/<int:pk>/cancel/', views.surgery_request_cancel, name='surgery_request_cancel'),
    path('advise/<int:patient_id>/', views.surgery_advise, name='surgery_advise'),
    path('new/', views.surgery_create, name='surgery_create'),
    path('<int:pk>/', views.surgery_detail, name='surgery_detail'),
    path('<int:pk>/edit/', views.surgery_edit, name='surgery_edit'),
    path('procedures/', views.procedure_list, name='procedure_list'),
    path('procedures/new/', views.procedure_create, name='procedure_create'),
    path('categories/new/', views.category_create, name='category_create'),
]
