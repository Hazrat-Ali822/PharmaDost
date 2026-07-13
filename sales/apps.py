from django.apps import AppConfig

class SalesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sales'
    # All stock/price logic lives in sales.services (create_sale / return_sale).
    # Signals were removed to keep a single source of truth for stock movement.
