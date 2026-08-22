import xml.etree.ElementTree as ET
import csv
import os
import re

def detect_category(name):
    n = name.lower()
    if any(k in n for k in ['tab', 'tablets', 'caplet']):
        return 'TABLET'
    elif any(k in n for k in ['cap', 'capsule', 'caps']):
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
    elif any(k in n for k in ['sachet', 'powder', 'sachets', 'sach']):
        return 'SACHET'
    elif any(k in n for k in ['supp', 'suppository']):
        return 'SUPPOSITORY'
    return 'OTHER'

def parse_medicen_xls():
    xls_path = 'medicen.xls'
    out_dir = 'shaheen_health_care_data'
    os.makedirs(out_dir, exist_ok=True)

    tree = ET.parse(xls_path)
    root = tree.getroot()
    ns = {'ss': 'urn:schemas-microsoft-com:office:spreadsheet'}

    medicines = []
    batches = []

    for r in root.findall('.//ss:Row', ns):
        cell_dict = {}
        col_idx = 1
        for c in r.findall('ss:Cell', ns):
            idx_attr = c.attrib.get('{urn:schemas-microsoft-com:office:spreadsheet}Index')
            if idx_attr:
                col_idx = int(idx_attr)
            data = c.find('ss:Data', ns)
            cell_dict[col_idx] = data.text if data is not None else ''
            col_idx += 1

        code = cell_dict.get(1, '').strip()
        name = cell_dict.get(2, '').strip()
        cost_str = cell_dict.get(3, '0').strip()
        min_stock_str = cell_dict.get(5, '0').strip()
        stock_str = cell_dict.get(6, '0').strip()

        if code.isdigit() and name and len(cell_dict) >= 3:
            if name in ['MIX', 'TABLET', 'SYRUP', 'INJECTION', 'DROPS', 'CREAM']:
                continue

            try:
                cost = float(cost_str or 0)
            except ValueError:
                cost = 0.0

            try:
                stock = int(float(stock_str or 0))
            except ValueError:
                stock = 0

            try:
                min_stock = int(float(min_stock_str or 0))
            except ValueError:
                min_stock = 0

            # Standard retail price is 15% markup over trade/cost price
            retail_price = round(cost * 1.15, 2) if cost > 0 else 0.0
            category = detect_category(name)

            medicines.append({
                'item_id': code,
                'barcode': code,
                'name': name,
                'pack_size': '1',
                'retail_price': retail_price,
                'tp_price': cost,
                'cost_price': cost,
                'quantity': max(0, stock),
                'rack_location': '',
                'company_name': '',
                'generic_name': '',
                'category_name': category
            })

            if stock > 0:
                batches.append({
                    'batch_id': code,
                    'barcode': code,
                    'batch_number': f"BATCH-{code}",
                    'quantity': stock,
                    'retail_price': retail_price,
                    'tp_price': cost,
                    'cost_price': cost,
                    'expiry_date': '2027-12-31'
                })

    # Write medicines.csv
    med_csv_path = os.path.join(out_dir, 'medicines.csv')
    with open(med_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'item_id', 'barcode', 'name', 'pack_size', 'retail_price',
            'tp_price', 'cost_price', 'quantity', 'rack_location',
            'company_name', 'generic_name', 'category_name'
        ])
        writer.writeheader()
        writer.writerows(medicines)

    # Write batches.csv
    batch_csv_path = os.path.join(out_dir, 'batches.csv')
    with open(batch_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'batch_id', 'barcode', 'batch_number', 'quantity',
            'retail_price', 'tp_price', 'cost_price', 'expiry_date'
        ])
        writer.writeheader()
        writer.writerows(batches)

    print(f"Exported {len(medicines)} medicines and {len(batches)} active stock batches for Shaheen Health Care to {out_dir}/")

if __name__ == '__main__':
    parse_medicen_xls()
