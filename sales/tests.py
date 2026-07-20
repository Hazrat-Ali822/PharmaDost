from django.test import TestCase
from suppliers.models import Supplier
from inventory.models import Medicine
from sales.services import create_sale
import datetime

class SaleFlowTest(TestCase):
	def setUp(self):
		self.supplier = Supplier.objects.create(name='ACME', phone='000')
		self.med = Medicine.objects.create(
			name='Paracetamol',
			brand='ACME',
			price=100,
			quantity=50,
			expiry_date=datetime.date(2030, 1, 1),
			supplier=self.supplier
		)

	def test_sale_reduces_stock(self):
		sale = create_sale(items=[{"medicine_id": self.med.id, "quantity": 5}], customer_name='John')
		self.med.refresh_from_db()
		self.assertEqual(self.med.quantity, 45)
		self.assertEqual(sale.total, 500)