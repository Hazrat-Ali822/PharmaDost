from django.urls import path
from . import views

urlpatterns = [
    path('', views.patient_list, name='patient_list'),
    path('index.json', views.patient_index, name='patient_index'),
    path('add/', views.patient_create, name='patient_add'),
    path('<int:pk>/', views.patient_detail, name='patient_detail'),
    path('<int:pk>/edit/', views.patient_edit, name='patient_edit'),
    path('<int:pk>/delete/', views.patient_delete, name='patient_delete'),
    path('<int:pk>/record/add/', views.record_add, name='patient_record_add'),
    path('<int:pk>/photo/add/', views.document_add, name='patient_document_add'),
    path('photo/<int:pk>/file/', views.document_file, name='patient_document_file'),
    path('photo/<int:pk>/delete/', views.document_delete, name='patient_document_delete'),
]
