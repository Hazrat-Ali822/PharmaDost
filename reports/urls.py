from django.urls import path
from .views import sales_report, profit_report, inventory_report, daybook_report, visual_analytics


urlpatterns = [
    path('sales/', sales_report, name='sales_report'),
    path('profit/', profit_report, name='profit_report'),
    path('daybook/', daybook_report, name='daybook_report'),
    path('inventory/', inventory_report, name='inventory_report'),
    path('analytics/', visual_analytics, name='visual_analytics'),
]
