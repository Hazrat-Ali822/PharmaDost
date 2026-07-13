from django.urls import path
from . import views

urlpatterns = [
    path('', views.supplier_list, name='supplier_list'),
    path('add/', views.supplier_create, name='supplier_add'),
    path('<int:pk>/edit/', views.supplier_edit, name='supplier_edit'),
    path('<int:pk>/delete/', views.supplier_delete, name='supplier_delete'),
    path('<int:pk>/ledger/', views.supplier_ledger, name='supplier_ledger'),
    path('<int:pk>/payment/', views.supplier_payment_add, name='supplier_payment_add'),
]
