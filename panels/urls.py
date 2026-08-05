from django.urls import path

from . import views

urlpatterns = [
    path('', views.panel_list, name='panel_list'),
    path('add/', views.panel_create, name='panel_add'),
    path('<int:pk>/edit/', views.panel_edit, name='panel_edit'),
    path('<int:pk>/ledger/', views.panel_ledger, name='panel_ledger'),
    path('<int:pk>/payment/', views.payment_create, name='panel_payment_add'),
    path('claim/<int:pk>/update/', views.claim_update, name='panel_claim_update'),
]
