"""Every page that shows a "Download as CSV" button must actually send a CSV.

The button is not written per page — it lives in `reports/_range_filter.html`,
which several screens include to get the period selector. That is convenient and
it is also the trap: including the partial silently *promises* an export, and
the view has to honour it separately. `/opd/payouts/` included the partial and
never answered `?export=csv`, so the link returned the HTML page with a
`text/html` content type. The browser re-rendered the same screen. Nothing
downloaded, and nothing on the page said why.

Rather than test the five known screens by name, this walks the templates for
the include and derives the list — a screen that picks up the partial tomorrow
is covered without editing this file.

    python manage.py test tests.test_exports --settings=pharma_mgmt.test_settings
"""
import re
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from saas.models import Hospital
from saas.utils import clear_current_hospital

# template path -> the url name that renders it
PAGES = {
    'opd/payout_list.html': 'payout_list',
    'reports/daybook.html': 'daybook_report',
    'reports/module_profit.html': 'module_profit_report',
    'reports/profit_report.html': 'profit_report',
    'reports/sales_report.html': 'sales_report',
}


def _templates_with_the_button():
    root = Path(settings.BASE_DIR) / 'templates'
    found = []
    for path in root.rglob('*.html'):
        text = path.read_text(encoding='utf-8', errors='ignore')
        if '_range_filter.html' in text:
            found.append(path.relative_to(root).as_posix())
    return sorted(found)


class CsvExportTest(TestCase):

    def setUp(self):
        self.h = Hospital.objects.create(
            name='Ex', slug='ex', expiry_date=date.today() + timedelta(days=900))
        User.objects.create_user(email='a@ex.com', password='pw',
                                 role='ADMIN', hospital=self.h)
        self.c = Client()
        self.assertTrue(self.c.login(email='a@ex.com', password='pw'))

    def tearDown(self):
        clear_current_hospital()

    def test_the_page_list_is_still_complete(self):
        """If this fails, a screen started including the range filter — add it to
        PAGES with its url name so the export below is checked too."""
        self.assertEqual(_templates_with_the_button(), sorted(PAGES),
                         'a template gained (or lost) the CSV button')

    def test_every_such_page_answers_export_csv_with_a_csv(self):
        for template, url_name in sorted(PAGES.items()):
            with self.subTest(page=template):
                resp = self.c.get(reverse(url_name), {'export': 'csv'})
                self.assertEqual(resp.status_code, 200, template)
                self.assertTrue(
                    resp['Content-Type'].startswith('text/csv'),
                    f'{template} shows a Download as CSV button but returned '
                    f'{resp["Content-Type"]} — the view is not handling '
                    f'?export=csv')
                self.assertIn('attachment;', resp.get('Content-Disposition', ''))

    def test_a_name_that_looks_like_a_formula_is_defused(self):
        """A medicine or patient called `=cmd|...` would otherwise execute when
        the accountant opens the file in Excel."""
        from reports.export import _safe
        self.assertEqual(_safe('=cmd|/c calc'), "'=cmd|/c calc")
        self.assertEqual(_safe('Panadol'), 'Panadol')
