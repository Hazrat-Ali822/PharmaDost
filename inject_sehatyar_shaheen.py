"""
Stand-alone Runner Script to:
1. Wipe / Delete all existing medicines & stock from "Shaheen Health Care" tenant.
2. Inject the complete clean dataset into "Shaheen Health Care" tenant on Sehatyar.online.

Usage on Jabra Host / Server / Local:
    python inject_sehatyar_shaheen.py
"""

import os
import sys
import django

# Setup Django Environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pharma_mgmt.settings')
django.setup()

from django.core.management import call_command
from saas.models import Hospital

if __name__ == '__main__':
    print("==========================================================")
    print("  Injecting Dataset into: Shaheen Health Care             ")
    print("==========================================================")
    
    # 1. Find existing Shaheen Health Care tenant or create if not present
    tenant = (
        Hospital.objects.filter(name__icontains='shaheen').first() or
        Hospital.objects.filter(slug__icontains='shaheen').first()
    )
    
    if tenant:
        tenant_slug = tenant.slug
        tenant_name = tenant.name
        print(f"Found existing tenant: '{tenant_name}' (slug: '{tenant_slug}', ID: {tenant.id})")
    else:
        tenant_slug = "shaheen-health-care"
        tenant_name = "Shaheen Health Care"
        print(f"Tenant not found, will create: '{tenant_name}' (slug: '{tenant_slug}')")

    print("\n[STEP 1] Clearing all previous medicines and stock for Shaheen Health Care...")
    print("[STEP 2] Injecting 2,446 Medicines, 2,276 Batches, and Sales Data...")
    
    call_command(
        'import_umair_pharmacy',
        tenant_slug=tenant_slug,
        tenant_name=tenant_name,
        data_dir='umair_pharmacy_data',
        clear_existing=True
    )
    
    print("\n==========================================================")
    print("  SUCCESS! Shaheen Health Care data refreshed and injected!")
    print("==========================================================")
