from django.urls import path
from .views import sale_create, sale_list, sale_detail, sale_return
from . import wholesale_views as wv

urlpatterns = [
    path('new/', sale_create, name='sale_create'),
    path('list/', sale_list, name='sale_list'),          # <-- named so {% url 'sale_list' %} works

    # Wholesale order desk (bulk entry -> order sheet -> convert to bill)
    path('wholesale/', wv.order_list, name='wholesale_order_list'),
    path('wholesale/new/', wv.order_create, name='wholesale_order_create'),
    path('wholesale/<int:pk>/', wv.order_edit, name='wholesale_order_edit'),
    path('wholesale/<int:pk>/paste/', wv.order_add_paste, name='wholesale_order_paste'),
    path('wholesale/<int:pk>/add/', wv.order_add_item, name='wholesale_order_add'),
    path('wholesale/<int:pk>/update/', wv.order_item_update, name='wholesale_order_update'),
    path('wholesale/<int:pk>/item/<int:item_id>/delete/', wv.order_item_delete, name='wholesale_order_item_delete'),
    path('wholesale/<int:pk>/repeat/', wv.order_repeat, name='wholesale_order_repeat'),
    path('wholesale/<int:pk>/print/', wv.order_print, name='wholesale_order_print'),
    path('wholesale/<int:pk>/convert/', wv.order_convert, name='wholesale_order_convert'),
    path('wholesale/<int:pk>/cancel/', wv.order_cancel, name='wholesale_order_cancel'),

    path('<int:pk>/', sale_detail, name='sale_detail'),  # <-- printable detail
    path('<int:pk>/return/', sale_return, name='sale_return'),
]
