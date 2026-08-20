from django import forms
from saas.forms import TenantModelForm
from pharma_mgmt.widgets import DateInput
from .models import Medicine


class MedicineForm(TenantModelForm):
	class Meta:
		model = Medicine
		fields = [
			'name', 'generic_name', 'brand', 'manufacturer', 'category', 'barcode', 'image',
			'pack_size', 'units_per_pack', 'rack_location',
			'cost_price', 'price', 'wholesale_price', 'reorder_level',
			'quantity', 'expiry_date', 'supplier',
		]
		widgets = {
			'expiry_date': DateInput(),
		}
		help_texts = {
			'generic_name': 'Salt / formula (e.g. Paracetamol) — used to find alternatives',
			'barcode': 'Optional — scan or leave blank',
			'pack_size': 'e.g. 10x10, 60ml',
			'units_per_pack': 'Loose units per pack (for unit/strip sale)',
			'cost_price': 'What you PAID per unit — the profit report needs it. Leave blank only if you truly do not know.',
			'price': 'What you SELL it for per unit',
			'wholesale_price': 'Auto-used on wholesale bills (0 = same as retail)',
			'quantity': 'Opening stock. Recorded as a batch at the purchase price above.',
			'reorder_level': 'Alert when stock falls below this',
		}
		labels = {
			'cost_price': 'Purchase price (what you paid)',
			'price': 'Selling price (retail)',
		}

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		# Optional, and blank means "not recorded" rather than free — see
		# `clean_cost_price`. It must not be required: the offline `medicine`
		# handler replays a payload written before this field existed, and a
		# required field would reject the lot permanently.
		self.fields['cost_price'].required = False

	def clean_cost_price(self):
		from decimal import Decimal
		return self.cleaned_data.get('cost_price') or Decimal('0.00')
