from django import forms
from .models import Medicine


class MedicineForm(forms.ModelForm):
	class Meta:
		model = Medicine
		fields = [
			'name', 'generic_name', 'brand', 'manufacturer', 'category', 'barcode', 'image',
			'pack_size', 'units_per_pack', 'rack_location',
			'price', 'wholesale_price', 'reorder_level',
			'quantity', 'expiry_date', 'supplier',
		]
		widgets = {
			'expiry_date': forms.DateInput(attrs={'type': 'date'}),
		}
		help_texts = {
			'generic_name': 'Salt / formula (e.g. Paracetamol) — used to find alternatives',
			'barcode': 'Optional — scan or leave blank',
			'pack_size': 'e.g. 10x10, 60ml',
			'units_per_pack': 'Loose units per pack (for unit/strip sale)',
			'wholesale_price': 'Auto-used on wholesale bills (0 = same as retail)',
			'reorder_level': 'Alert when stock falls below this',
		}
