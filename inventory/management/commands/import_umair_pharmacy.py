import os
import csv
from decimal import Decimal
from datetime import datetime, date
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from saas.models import Hospital
from inventory.models import Medicine, StockBatch
from suppliers.models import Supplier
from customers.models import Customer


class Command(BaseCommand):
    help = "Import Umair Pharmacy stock, batches, suppliers, and customers from extracted CSV data"

    def add_arguments(self, parser):
        parser.add_argument(
            '--tenant-slug',
            type=str,
            default='umair-pharmacy',
            help='Slug of the tenant (Hospital) in SaaS database (default: umair-pharmacy)'
        )
        parser.add_argument(
            '--tenant-name',
            type=str,
            default='Umair Pharmacy',
            help='Name of tenant if creating (default: Umair Pharmacy)'
        )
        parser.add_argument(
            '--data-dir',
            type=str,
            default='umair_pharmacy_data',
            help='Directory containing medicines.csv, batches.csv, etc.'
        )
        parser.add_argument(
            '--clear-existing',
            action='store_true',
            help='Wipe existing medicines/batches for this tenant before import'
        )

    def detect_category(self, name, category_name):
        n = (name + " " + category_name).lower()
        if any(k in n for k in ['tab', 'tablets', 'caplet']):
            return 'TABLET'
        elif any(k in n for k in ['cap', 'capsule']):
            return 'CAPSULE'
        elif any(k in n for k in ['syp', 'syrup', 'susp', 'suspension', 'liquid', 'elixir']):
            return 'SYRUP'
        elif any(k in n for k in ['inj', 'injection', 'infusion', 'vial', 'amp', 'ampoule', 'drip']):
            return 'INJECTION'
        elif any(k in n for k in ['drop', 'eye drop', 'ear drop', 'nasal drop']):
            return 'DROPS'
        elif any(k in n for k in ['cream', 'oin', 'ointment', 'gel', 'lotion', 'balm']):
            return 'CREAM'
        elif any(k in n for k in ['inhaler', 'rotacap', 'inhalation', 'spray']):
            return 'INHALER'
        elif any(k in n for k in ['sachet', 'powder', 'sachets']):
            return 'SACHET'
        elif any(k in n for k in ['supp', 'suppository']):
            return 'SUPPOSITORY'
        return 'OTHER'

    def parse_decimal(self, val, default=Decimal('0.00')):
        try:
            if not val or val.strip() == '':
                return default
            return Decimal(str(float(val)))
        except (ValueError, TypeError):
            return default

    def parse_date(self, val):
        if not val or val.strip() == '':
            return timezone.localdate() + timezone.timedelta(days=365)
        try:
            # Format YYYY-MM-DD
            return datetime.strptime(val.strip()[:10], '%Y-%m-%d').date()
        except ValueError:
            try:
                # Format MM/DD/YYYY
                return datetime.strptime(val.strip(), '%m/%d/%Y').date()
            except ValueError:
                return timezone.localdate() + timezone.timedelta(days=365)

    @transaction.atomic
    def handle(self, *args, **options):
        tenant_slug = options['tenant_slug'].strip().lower()
        tenant_name = options['tenant_name'].strip()
        data_dir = options['data_dir']
        clear_existing = options['clear_existing']

        self.stdout.write(self.style.SUCCESS(f"Starting import for tenant: '{tenant_slug}' ({tenant_name})"))

        # 1. Resolve or Create Hospital / Tenant
        tenant = Hospital.objects.filter(slug=tenant_slug).first()
        if not tenant:
            tenant = Hospital.objects.filter(name__iexact=tenant_name).first()

        if not tenant:
            tenant = Hospital.objects.create(
                name=tenant_name,
                slug=tenant_slug,
                is_active=True,
                expiry_date=date(2035, 12, 31),
                monthly_price=Decimal('0.00'),
                enabled_modules=["inventory", "sales", "billing", "customers", "suppliers", "prescriptions"]
            )
            self.stdout.write(self.style.SUCCESS(f"Created new Tenant (Hospital): {tenant.name} (id={tenant.id})"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Using existing Tenant: {tenant.name} (id={tenant.id}, slug={tenant.slug})"))

        if clear_existing:
            self.stdout.write("Clearing existing medicines and batches for this tenant...")
            StockBatch.objects.filter(hospital=tenant).delete()
            Medicine.objects.filter(hospital=tenant).delete()

        # File paths
        med_file = os.path.join(data_dir, 'medicines.csv')
        batch_file = os.path.join(data_dir, 'batches.csv')
        supp_file = os.path.join(data_dir, 'suppliers.csv')
        cust_file = os.path.join(data_dir, 'customers.csv')

        # 2. Import Suppliers
        supplier_obj = None
        if os.path.exists(supp_file):
            with open(supp_file, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    s_name = row.get('name', '').strip()
                    if s_name:
                        supplier_obj, _ = Supplier.objects.get_or_create(
                            name=s_name,
                            hospital=tenant,
                            defaults={
                                'phone': row.get('phone', '').strip(),
                                'address': row.get('address', '').strip(),
                                'balance': self.parse_decimal(row.get('balance', '0'))
                            }
                        )
            self.stdout.write(self.style.SUCCESS("Suppliers processed."))

        # 3. Import Customers
        if os.path.exists(cust_file):
            with open(cust_file, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    c_name = row.get('name', '').strip()
                    if c_name:
                        Customer.objects.get_or_create(
                            name=c_name,
                            hospital=tenant,
                            defaults={
                                'phone': row.get('phone', '').strip(),
                                'area': row.get('address', '').strip(),
                                'balance': self.parse_decimal(row.get('balance', '0')),
                                'type': Customer.RETAIL
                            }
                        )
            self.stdout.write(self.style.SUCCESS("Customers processed."))

        # 4. Import Medicines
        barcode_to_medicine = {}
        name_to_medicine = {}
        medicines_created = 0
        medicines_updated = 0

        if os.path.exists(med_file):
            with open(med_file, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = row.get('name', '').strip()
                    if not name:
                        continue

                    barcode = row.get('barcode', '').strip()
                    pack_size = row.get('pack_size', '').strip()
                    retail_price = self.parse_decimal(row.get('retail_price', '0'))
                    cost_price = self.parse_decimal(row.get('cost_price', '0'))
                    tp_price = self.parse_decimal(row.get('tp_price', '0'))
                    quantity = int(float(row.get('quantity', 0) or 0))
                    rack_location = row.get('rack_location', '').strip()
                    company_name = row.get('company_name', '').strip()
                    generic_name = row.get('generic_name', '').strip()
                    category_name = row.get('category_name', '').strip()

                    cat_choice = self.detect_category(name, category_name)
                    brand = company_name if company_name and company_name != 'UNKNOWN' else ''
                    mfg = company_name if company_name and company_name != 'UNKNOWN' else ''

                    med, created = Medicine.objects.get_or_create(
                        name=name,
                        brand=brand,
                        hospital=tenant,
                        defaults={
                            'generic_name': generic_name if generic_name != 'ABC' else '',
                            'manufacturer': mfg,
                            'category': cat_choice,
                            'barcode': barcode,
                            'pack_size': pack_size,
                            'rack_location': rack_location,
                            'price': retail_price,
                            'cost_price': cost_price if cost_price > 0 else tp_price,
                            'wholesale_price': tp_price,
                            'quantity': max(0, quantity),
                            'expiry_date': timezone.localdate() + timezone.timedelta(days=365),
                            'supplier': supplier_obj,
                            'is_active': True
                        }
                    )

                    if created:
                        medicines_created += 1
                    else:
                        medicines_updated += 1

                    if barcode:
                        barcode_to_medicine[barcode] = med
                    name_to_medicine[name.lower()] = med

            self.stdout.write(self.style.SUCCESS(f"Medicines imported: {medicines_created} created, {medicines_updated} already existed."))
        else:
            self.stdout.write(self.style.ERROR(f"Medicines file not found: {med_file}"))

        # 5. Import Batches (Synchronized with Current Physical Stock)
        StockBatch.objects.filter(hospital=tenant).delete()
        batches_created = 0
        med_assigned_stock = {}

        if os.path.exists(batch_file):
            with open(batch_file, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    b_code = row.get('barcode', '').strip()
                    batch_num = row.get('batch_number', '').strip()
                    b_cost = self.parse_decimal(row.get('cost_price', '0'))
                    b_tp = self.parse_decimal(row.get('tp_price', '0'))
                    exp_date = self.parse_date(row.get('expiry_date', ''))

                    # Find corresponding medicine
                    med = barcode_to_medicine.get(b_code) or Medicine.objects.filter(barcode=b_code, hospital=tenant).first()

                    if med:
                        already_assigned = med_assigned_stock.get(med.id, 0)
                        remaining_med_qty = max(0, med.quantity - already_assigned)
                        
                        # Assign up to remaining current stock
                        raw_b_qty = int(float(row.get('quantity', 0) or 0))
                        assign_qty = max(0, min(raw_b_qty, remaining_med_qty))
                        if assign_qty == 0 and remaining_med_qty > 0 and raw_b_qty >= 0:
                            assign_qty = remaining_med_qty

                        cost = b_cost if b_cost > 0 else (b_tp if b_tp > 0 else med.cost_price)
                        StockBatch.objects.create(
                            medicine=med,
                            batch_number=batch_num or f"BATCH-{b_code}",
                            quantity=max(0, assign_qty),
                            cost_price=cost,
                            expiry_date=exp_date,
                            supplier=supplier_obj,
                            hospital=tenant
                        )
                        med_assigned_stock[med.id] = already_assigned + assign_qty
                        batches_created += 1

            # Ensure all medicines with quantity > 0 have at least one batch covering their stock
            for med in Medicine.objects.filter(hospital=tenant, quantity__gt=0):
                assigned = med_assigned_stock.get(med.id, 0)
                if assigned < med.quantity:
                    diff = max(0, med.quantity - assigned)
                    StockBatch.objects.create(
                        medicine=med,
                        batch_number=f"BATCH-{med.barcode or med.id}",
                        quantity=diff,
                        cost_price=med.cost_price,
                        expiry_date=med.expiry_date,
                        supplier=supplier_obj,
                        hospital=tenant
                    )
                    batches_created += 1

            self.stdout.write(self.style.SUCCESS(f"Batches imported: {batches_created} batches synchronized with physical stock."))
        else:
            self.stdout.write(self.style.WARNING(f"Batches file not found: {batch_file}"))

        # 6. Import Historical Bills & Sales
        sales_file = os.path.join(data_dir, 'sales.csv')
        sale_items_file = os.path.join(data_dir, 'sale_items.csv')

        if os.path.exists(sales_file):
            self.stdout.write("Importing Sales Bills (Invoices)...")
            from sales.models import Sale, SaleItem
            Sale.objects.filter(hospital=tenant).delete()

            # Build Sale objects
            sales_to_create = []
            inv_map = {} # invoice_no -> Sale object

            with open(sales_file, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    inv_no = row.get('invoice_no', '').strip()
                    if not inv_no:
                        continue
                    s_date = row.get('sale_date', '').strip()
                    s_time = row.get('sale_time', '').strip()
                    subtotal = self.parse_decimal(row.get('subtotal', '0'))
                    discount = self.parse_decimal(row.get('discount', '0'))
                    total = self.parse_decimal(row.get('total', '0'))
                    paid = self.parse_decimal(row.get('paid', '0'))
                    cust_name = row.get('customer_name', '').strip()

                    # Parse datetime
                    dt_str = f"{s_date} {s_time}".strip()
                    created_at = timezone.now()
                    try:
                        created_at = timezone.make_aware(datetime.strptime(dt_str, '%Y-%m-%d %I:%M:%S %p'))
                    except Exception:
                        try:
                            created_at = timezone.make_aware(datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S'))
                        except Exception:
                            try:
                                created_at = timezone.make_aware(datetime.strptime(s_date, '%Y-%m-%d'))
                            except Exception:
                                pass

                    sale_obj = Sale(
                        sale_type=Sale.RETAIL,
                        customer_name=cust_name or 'Walk-in Customer',
                        subtotal=subtotal,
                        discount=discount,
                        total=total,
                        paid=paid,
                        payment_method='CASH',
                        created_at=created_at,
                        hospital=tenant
                    )
                    sales_to_create.append(sale_obj)
                    inv_map[inv_no] = sale_obj

            # Bulk create sales in batches
            batch_size = 1000
            for i in range(0, len(sales_to_create), batch_size):
                Sale.objects.bulk_create(sales_to_create[i:i + batch_size])
            self.stdout.write(self.style.SUCCESS(f"Created {len(sales_to_create)} Sale Bills."))

            # Import Sale Items
            if os.path.exists(sale_items_file):
                self.stdout.write("Importing Bill Items...")
                # Reload created sales to get their DB IDs
                # We can map by created_at and total or store sequentially
                db_sales = list(Sale.objects.filter(hospital=tenant).order_by('id'))
                # Map original inv_no to created db sale by sequential index
                inv_keys = list(inv_map.keys())
                seq_inv_to_sale = {}
                for idx, k in enumerate(inv_keys):
                    if idx < len(db_sales):
                        seq_inv_to_sale[k] = db_sales[idx]

                # Fallback general medicine if item not found
                fallback_med, _ = Medicine.objects.get_or_create(
                    name="General Item",
                    brand="General",
                    hospital=tenant,
                    defaults={
                        'price': Decimal('100.00'),
                        'cost_price': Decimal('80.00'),
                        'expiry_date': timezone.localdate() + timezone.timedelta(days=365)
                    }
                )

                items_to_create = []
                with open(sale_items_file, mode='r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        inv_no = row.get('invoice_no', '').strip()
                        sale = seq_inv_to_sale.get(inv_no)
                        if not sale:
                            continue

                        barcode = row.get('barcode', '').strip()
                        item_name = row.get('item_name', '').strip()
                        qty = max(1, int(float(row.get('quantity', 1) or 1)))
                        unit_price = self.parse_decimal(row.get('unit_price', '0'))
                        discount = self.parse_decimal(row.get('discount', '0'))
                        cost_price = self.parse_decimal(row.get('cost_price', '0'))

                        med = barcode_to_medicine.get(barcode) or name_to_medicine.get(item_name.lower()) or fallback_med

                        item_obj = SaleItem(
                            sale=sale,
                            medicine=med,
                            unit_price=unit_price if unit_price > 0 else med.price,
                            quantity=qty,
                            discount=discount,
                            cost_price=cost_price if cost_price > 0 else med.cost_price
                        )
                        items_to_create.append(item_obj)

                        if len(items_to_create) >= 2000:
                            SaleItem.objects.bulk_create(items_to_create)
                            items_to_create = []

                if items_to_create:
                    SaleItem.objects.bulk_create(items_to_create)
                self.stdout.write(self.style.SUCCESS(f"Bill items imported successfully!"))

        self.stdout.write(self.style.SUCCESS(
            f"\n[DONE] Umair Pharmacy data import complete for '{tenant.name}' on sehatyar.online!"
        ))

