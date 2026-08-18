"""No sidebar heading ever appears with nothing under it.

"Clinical" used to be a single heading over 26 links. It is now seven — Patients,
OPD, Diagnostics, Ward (IPD), Emergency, Theatre, Mother & Child — and splitting
it multiplies the one failure this file exists to catch.

Every clinical link is feature-gated, so **a group has to be gated on the union
of its own contents**. Gate it on less and a tenant gets a heading with nothing
under it; gate it on more and links the user is entitled to disappear. Neither
shows up unless you happen to be signed in as the affected tenant with the
affected role, which is why it shipped once already: "Price List" was gated on
`catalog` (a CORE feature, on for everybody) while both links inside it were
`nav.lab` / `nav.imaging`, so a pharmacy-only shop got a section title over
nothing.

The check here is deliberately structural rather than a list of expected
headings: it renders the real sidebar across module packages and roles and
asserts that every `.nav-group` present contains at least one link. A new group
added tomorrow is covered without touching this file.

    python manage.py test tests.test_nav_groups --settings=pharma_mgmt.test_settings
"""
import re
from datetime import date, timedelta

from django.test import Client, TestCase

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital

GROUP_OPEN = '<div class="nav-group">'
TITLE = re.compile(r'<div class="nav-group-title">(.*?)</div>', re.S)


def _future():
    return date.today() + timedelta(days=365)


def _groups(html):
    """[(title, link_count)] for every sidebar group in the rendered page.

    Groups are siblings, so slicing from one opening tag to the next is enough
    and avoids trying to match nested </div>s with a regex.
    """
    chunks = html.split(GROUP_OPEN)[1:]
    out = []
    for i, chunk in enumerate(chunks):
        body = chunk.split(GROUP_OPEN)[0]
        if '</aside>' in body:
            body = body.split('</aside>')[0]
        title = TITLE.search(body)
        out.append(((title.group(1).strip() if title else f'<untitled #{i}>'),
                    body.count('<a ')))
    return out


class NoEmptyNavGroupTest(TestCase):

    # The packages a real customer actually buys, plus the two extremes.
    PACKAGES = {
        'everything': [],      # empty = every module on
        'pharmacy only': ['pharmacy'],
        'pharmacy package': ['pharmacy', 'reports', 'finance'],
        'small clinic': ['opd', 'lab'],
        'clinic with beds': ['opd', 'ipd', 'lab', 'imaging'],
        'hospital no theatre': ['opd', 'ipd', 'lab', 'imaging', 'emergency',
                                'maternity', 'vaccination'],
        'diagnostics only': ['lab', 'imaging'],
        'bloodbank only': ['bloodbank'],
        'maternity only': ['maternity'],
        'ambulance only': ['ambulance'],
    }
    ROLES = ['ADMIN', 'DOCTOR', 'NURSE', 'RECEPTIONIST', 'LABTECH',
             'SONOGRAPHER', 'PHARMACIST', 'ACCOUNTANT']

    def tearDown(self):
        clear_current_hospital()

    def _render(self, modules, role, n):
        h = Hospital.objects.create(name=f'H{n}', slug=f'h-{n}',
                                    expiry_date=_future(),
                                    enabled_modules=modules)
        User.objects.create_user(email=f'u{n}@t.com', password='pw',
                                 role=role, hospital=h)
        c = Client()
        self.assertTrue(c.login(email=f'u{n}@t.com', password='pw'))
        r = c.get('/dashboard/', follow=True)
        return r.content.decode()

    def test_no_group_is_rendered_with_zero_links(self):
        n = 0
        for package, modules in self.PACKAGES.items():
            for role in self.ROLES:
                n += 1
                html = self._render(modules, role, n)
                for title, links in _groups(html):
                    self.assertGreater(
                        links, 0,
                        f'sidebar group "{title}" rendered with no links for a '
                        f'{role} on the "{package}" package — the heading is '
                        f'gated on less than its contents')

    def test_a_pharmacy_only_shop_sees_no_clinical_heading(self):
        html = self._render(['pharmacy'], 'PHARMACIST', 900)
        titles = [t for t, _ in _groups(html)]
        for clinical in ('Patients', 'OPD', 'Diagnostics', 'Ward (IPD)',
                         'Emergency', 'Theatre', 'Mother & Child'):
            self.assertNotIn(clinical, titles)

    def test_the_clinical_links_are_split_up_not_in_one_heap(self):
        """The whole point. One heading over 26 links is a list, not a menu —
        and the groups are collapsible accordions, so a single giant group
        defeats the only mechanism there is for shortening the sidebar."""
        html = self._render([], 'ADMIN', 901)
        groups = dict(_groups(html))
        for expected in ('Patients', 'OPD', 'Diagnostics', 'Ward (IPD)',
                         'Emergency', 'Theatre', 'Mother &amp; Child'):
            self.assertIn(expected, groups, f'missing group: {expected}')
        self.assertNotIn('Clinical', groups)
        biggest = max(groups.values())
        self.assertLess(biggest, 14,
                        'a sidebar group has grown past what anyone scans; '
                        'split it the way Clinical was split')

    def test_a_nurse_gets_the_ward_group(self):
        html = self._render(['ipd'], 'NURSE', 902)
        self.assertIn('Ward (IPD)', dict(_groups(html)))

    def test_theatre_is_hidden_from_doctors_heading_and_all(self):
        """Doctors advise surgery rather than schedule it, so the links inside
        are hidden from them — and the heading has to go with them."""
        html = self._render(['ot'], 'DOCTOR', 903)
        self.assertNotIn('Theatre', dict(_groups(html)))

    def test_lab_staff_get_diagnostics_without_the_patients_heading(self):
        """Lab and radiology reach a patient through the order they are working
        on, so the Patients link is hidden from them. With nothing else in that
        group installed, the heading must go too."""
        html = self._render(['lab'], 'LABTECH', 904)
        titles = dict(_groups(html))
        self.assertIn('Diagnostics', titles)
        self.assertNotIn('Patients', titles)
