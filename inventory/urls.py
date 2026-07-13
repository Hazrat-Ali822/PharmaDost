from django.urls import path
from . import views
from . import purchase_order_views as po


urlpatterns = [
path('', views.medicine_list, name='medicine_list'),
path('add/', views.medicine_create, name='medicine_add'),
path('<int:pk>/edit/', views.medicine_edit, name='medicine_edit'),
path('<int:pk>/delete/', views.medicine_delete, name='medicine_delete'),
# Purchase Orders (order stock FROM a supplier → print → receive into stock)
path('purchase-orders/', po.po_list, name='po_list'),
path('purchase-orders/new/', po.po_create, name='po_create'),
path('purchase-orders/<int:pk>/', po.po_edit, name='po_edit'),
path('purchase-orders/<int:pk>/paste/', po.po_add_paste, name='po_paste'),
path('purchase-orders/<int:pk>/add/', po.po_add_item, name='po_add'),
path('purchase-orders/<int:pk>/autofill/', po.po_autofill, name='po_autofill'),
path('purchase-orders/<int:pk>/update/', po.po_update, name='po_update'),
path('purchase-orders/<int:pk>/item/<int:item_id>/delete/', po.po_item_delete, name='po_item_delete'),
path('purchase-orders/<int:pk>/repeat/', po.po_repeat, name='po_repeat'),
path('purchase-orders/<int:pk>/print/', po.po_print, name='po_print'),
path('purchase-orders/<int:pk>/receive/', po.po_receive, name='po_receive'),
path('purchase-orders/<int:pk>/cancel/', po.po_cancel, name='po_cancel'),
path('purchases/new/', views.purchase_create, name='purchase_create'),
path('purchases/', views.purchase_list, name='purchase_list'),
path('purchases/<int:pk>/', views.purchase_detail, name='purchase_detail'),
path('adjustments/', views.adjustment_list, name='adjustment_list'),
path('adjustments/new/', views.adjustment_create, name='adjustment_create'),
path('returns/', views.preturn_list, name='preturn_list'),
path('returns/new/', views.preturn_create, name='preturn_create'),
path('returns/<int:pk>/', views.preturn_detail, name='preturn_detail'),
]