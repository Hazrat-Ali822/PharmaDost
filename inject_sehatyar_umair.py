"""
Stand-alone Runner Script to Inject Umair Pharmacy Data into Sehatyar.online
Usage on Jabra Host / Server / Local:
    python inject_sehatyar_umair.py
"""

import os
import sys
import django

# Setup Django Environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pharma_mgmt.settings')
django.setup()

from django.core.management import call_command

if __name__ == '__main__':
    print("==========================================================")
    print("  Injecting Umair Pharmacy Data into Sehatyar.online      ")
    print("==========================================================")
    
    tenant_slug = "umair-pharmacy"
    tenant_name = "Umair Pharmacy"
    
    call_command(
        'import_umair_pharmacy',
        tenant_slug=tenant_slug,
        tenant_name=tenant_name,
        data_dir='umair_pharmacy_data'
    )
    print("\nInjection finished successfully!")
