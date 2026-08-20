"""No dropdown in this product may offer another hospital's rows.

This is the rule the owner asked for after finding the demo tenant's suppliers
in a real customer's "Add medicine" form, and it is enforced here rather than
remembered: the test walks **every** ModelForm in the project, builds it with
one hospital bound, and fails if any choice field can see a row belonging to
another hospital.

Two reasons it has to be a sweep and not a list:

* The leak is invisible in the form's own source. A plain `forms.ModelForm`
  builds each foreign key's queryset from the default manager **at import**,
  before any request, when `TenantManager` is not strict and therefore returns
  everything — and that one queryset is reused for the life of the worker. The
  form looks correct; the timing is wrong. See `saas/forms.py`.
* It is a **write** path, not just a display bug. A `ModelChoiceField`
  validates the posted id against its own queryset, so an unscoped dropdown
  will happily accept another hospital's row on submit.

    python manage.py test tests.test_tenant_forms --settings=pharma_mgmt.test_settings
"""
import importlib
import inspect
from datetime import date, timedelta

from django import forms as djforms
from django.apps import apps
from django.test import TestCase

from accounts.models import User
from saas.models import Hospital
from saas.utils import (clear_current_hospital, set_current_hospital,
                        set_tenant_strict)

# Apps whose models are deliberately platform-wide, not per-hospital.
PLATFORM_APPS = {'admin', 'auth', 'contenttypes', 'sessions', 'saas'}


def _future():
    return date.today() + timedelta(days=365)


def _project_model_forms():
    """(label, class) for every ModelForm defined in a project `forms.py`."""
    out = []
    for app_config in apps.get_app_configs():
        if app_config.label in PLATFORM_APPS:
            continue
        try:
            mod = importlib.import_module(f'{app_config.name}.forms')
        except ImportError:
            continue
        for name, obj in vars(mod).items():
            if (inspect.isclass(obj)
                    and issubclass(obj, djforms.BaseModelForm)
                    and obj.__module__ == mod.__name__
                    and getattr(getattr(obj, '_meta', None), 'model', None) is not None):
                out.append((f'{app_config.label}.{name}', obj))
    return sorted(out)


def _tenant_models():
    return {m for m in apps.get_models()
            if any(f.name == 'hospital' for f in m._meta.fields)}


class NoFormOffersAnotherHospitalsRowsTest(TestCase):
    """Build every ModelForm as one hospital and look for the other's rows."""

    @classmethod
    def setUpTestData(cls):
        cls.mine = Hospital.objects.create(name='Shaheen Health Care', slug='shc',
                                           expiry_date=_future())
        cls.theirs = Hospital.objects.create(name='Sehatyar Demo Hospital', slug='demo',
                                             expiry_date=_future())
        cls.admin = User.objects.create_user(email='a@shc.test', password='pw')
        cls.admin.role, cls.admin.hospital = 'ADMIN', cls.mine
        cls.admin.save()

    def _seed(self, hospital):
        """One row of each model a dropdown in this product actually points at.

        Deliberately hand-written rather than generated: a generic factory would
        skip whatever it could not build, and the models it skipped would be the
        ones nobody notices leaking.
        """
        from bloodbank.models import BloodDonor
        from consent.models import ConsentTemplate
        from customers.models import Customer
        from imaging.models import ScanType
        from inventory.models import Medicine
        from lab.models import LabTest, TestCategory
        from opd.models import Department, Doctor
        from ot.models import SurgeryCategory, SurgeryProcedure
        from panels.models import Panel
        from patients.models import Patient
        from suppliers.models import Supplier

        tag = hospital.slug
        set_current_hospital(hospital)
        try:
            dept = Department.objects.create(name=f'{tag} Medicine', hospital=hospital)
            cat = TestCategory.objects.create(name=f'{tag} Haematology', hospital=hospital)
            surg_cat = SurgeryCategory.objects.create(name=f'{tag} General', hospital=hospital)
            return {
                'supplier': Supplier.objects.create(name=f'{tag} Distributors',
                                                    phone='03001234567', hospital=hospital),
                'patient': Patient.objects.create(full_name=f'{tag} Patient',
                                                  gender='M', hospital=hospital),
                'doctor': Doctor.objects.create(full_name=f'{tag} Doctor',
                                                department=dept, hospital=hospital),
                'department': dept,
                'medicine': Medicine.objects.create(name=f'{tag} Panadol', price=10,
                                                    expiry_date=_future(), hospital=hospital),
                'customer': Customer.objects.create(name=f'{tag} Customer',
                                                    phone='03007654321', hospital=hospital),
                'panel': Panel.objects.create(name=f'{tag} Sehat Card', hospital=hospital),
                'labtest': LabTest.objects.create(name=f'{tag} CBC', category=cat,
                                                  price=500, hospital=hospital),
                'category': cat,
                'scantype': ScanType.objects.create(name=f'{tag} Chest X-Ray',
                                                    price=800, hospital=hospital),
                'procedure': SurgeryProcedure.objects.create(name=f'{tag} Appendectomy',
                                                             category=surg_cat,
                                                             hospital=hospital),
                'surgcat': surg_cat,
                'donor': BloodDonor.objects.create(full_name=f'{tag} Donor',
                                                   blood_group='A+', hospital=hospital),
                'consent': ConsentTemplate.objects.create(title=f'{tag} Surgery consent',
                                                          body='x', hospital=hospital),
            }
        finally:
            clear_current_hospital()

    def test_no_dropdown_can_see_the_other_hospital(self):
        self._seed(self.mine)
        theirs = self._seed(self.theirs)
        their_pks = {(type(o), o.pk) for o in theirs.values()}
        tenant_models = _tenant_models()

        forms = _project_model_forms()
        self.assertGreater(len(forms), 50, 'the sweep stopped finding forms')

        leaks, unbuildable = [], []
        set_current_hospital(self.mine)
        set_tenant_strict(True)
        try:
            for label, cls in forms:
                form = None
                for kwargs in ({}, {'user': self.admin}):
                    try:
                        form = cls(**kwargs)
                        break
                    except Exception:
                        continue
                if form is None:
                    unbuildable.append(label)
                    continue
                for fname, field in form.fields.items():
                    qs = getattr(field, 'queryset', None)
                    if qs is None or qs.model not in tenant_models:
                        continue
                    for obj in qs:
                        if (type(obj), obj.pk) in their_pks:
                            leaks.append(f'{label}.{fname} offers {obj!r} '
                                         f'from {self.theirs.name}')
                            break
        finally:
            set_tenant_strict(False)
            clear_current_hospital()

        self.assertEqual(leaks, [], 'cross-tenant dropdowns:\n  ' + '\n  '.join(leaks))
        # A form nobody can build is a form nobody is checking. Keeping the count
        # visible means a new required kwarg cannot quietly remove a form from
        # the sweep.
        self.assertLessEqual(len(unbuildable), 2,
                             'forms the sweep could not build: ' + ', '.join(unbuildable))

    def test_a_hospital_less_user_sees_nothing_rather_than_everything(self):
        """The fail-closed half. A signed-in user with no hospital must match
        only hospital-less rows — the historical `if hospital:` bug returned
        every tenant's."""
        self._seed(self.mine)
        self._seed(self.theirs)
        tenant_models = _tenant_models()

        set_tenant_strict(True)                 # a request, but no hospital bound
        try:
            for label, cls in _project_model_forms():
                form = None
                for kwargs in ({}, {'user': self.admin}):
                    try:
                        form = cls(**kwargs)
                        break
                    except Exception:
                        continue
                if form is None:
                    continue
                for fname, field in form.fields.items():
                    qs = getattr(field, 'queryset', None)
                    if qs is None or qs.model not in tenant_models:
                        continue
                    with self.subTest(form=label, field=fname):
                        self.assertFalse(
                            qs.exclude(hospital__isnull=True).exists(),
                            f'{label}.{fname} shows hospital-owned rows to a '
                            f'user who belongs to no hospital')
        finally:
            set_tenant_strict(False)


class TheMedicineFormWasTheOneThatShowedTest(TestCase):
    """The reported case, kept as its own named test.

    `/medicines/add/` listed "Demo United Distributors" — the demo tenant's
    supplier — to Shaheen Health Care's admin. `Supplier` had a `hospital`
    column and a `TenantManager` the whole time; the dropdown was built at
    import, before any of that could apply.
    """

    def setUp(self):
        from suppliers.models import Supplier
        self.mine = Hospital.objects.create(name='Shaheen Health Care', slug='shc',
                                            expiry_date=_future())
        self.theirs = Hospital.objects.create(name='Sehatyar Demo Hospital', slug='demo',
                                              expiry_date=_future())
        Supplier.objects.create(name='Shaheen Pharma', phone='03001234567',
                                hospital=self.mine)
        Supplier.objects.create(name='Demo United Distributors', phone='03007654321',
                                hospital=self.theirs)
        u = User.objects.create_user(email='a@shc.test', password='pw')
        u.role, u.hospital = 'ADMIN', self.mine
        u.save()
        self.client.force_login(u)

    def test_the_add_medicine_page(self):
        body = self.client.get('/medicines/add/').content.decode()
        self.assertIn('Shaheen Pharma', body)
        self.assertNotIn('Demo United Distributors', body)

    def test_it_will_not_accept_the_other_hospitals_supplier_on_submit(self):
        from suppliers.models import Supplier
        from inventory.forms import MedicineForm

        theirs = Supplier.objects.get(name='Demo United Distributors')
        set_current_hospital(self.mine)
        set_tenant_strict(True)
        try:
            form = MedicineForm(data={
                'name': 'Augmentin', 'units_per_pack': '1', 'price': '250',
                'wholesale_price': '0', 'reorder_level': '10', 'quantity': '20',
                'expiry_date': _future().isoformat(), 'supplier': str(theirs.pk),
            })
            self.assertFalse(form.is_valid())
            self.assertIn('supplier', form.errors)
        finally:
            set_tenant_strict(False)
            clear_current_hospital()
