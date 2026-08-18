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
    'reports/visual_analytics.html': 'visual_analytics',
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


class OneDateFormatTest(TestCase):
    """#19 — five formats for the same kind of data, two of them regularly on
    one screen.

    Most were not anybody's decision: Django localises through the active locale
    and `en` renders "Aug. 15, 2027", so every template printing a date without
    an explicit `|date:` filter got the American format for free. The fix is one
    format module (`pharma_mgmt/formats/en/formats.py`) rather than 45 template
    edits, because the 46th template added next month would be wrong again.
    """

    def test_an_unfiltered_date_renders_day_first(self):
        from datetime import date, datetime

        from django.template import Context, Template
        out = Template('{{ d }}|{{ dt }}').render(Context({
            'd': date(2027, 8, 15), 'dt': datetime(2026, 8, 18, 18, 37)}))
        self.assertEqual(out, '15/08/2027|18/08/2026 18:37')

    def test_a_day_first_date_is_accepted_when_typed(self):
        """03/04/2026 must be 3 April — the reading staff here intend."""
        from django import forms
        field = forms.DateField()
        self.assertEqual(field.clean('03/04/2026').isoformat(), '2026-04-03')

    def test_iso_is_still_accepted_from_a_date_input(self):
        from django import forms
        field = forms.DateField()
        self.assertEqual(field.clean('2026-04-03').isoformat(), '2026-04-03')

    def test_no_template_shows_an_iso_date_outside_an_input_value(self):
        """`Y-m-d` is correct for the `value` of an <input type="date">, which
        the HTML spec fixes as ISO. Anywhere else it is the defect."""
        import re
        from pathlib import Path

        from django.conf import settings
        offenders = []
        for path in Path(settings.BASE_DIR).rglob('templates/**/*.html'):
            if 'dist' in path.parts or 'build' in path.parts:
                continue
            for i, line in enumerate(path.read_text(encoding='utf-8',
                                                    errors='ignore').split('\n'), 1):
                if not re.search(r'date:.Y-m-d', line):
                    continue
                if 'type="date"' in line or 'value=' in line:
                    continue
                offenders.append(f'{path.name}:{i}')
        self.assertEqual(offenders, [],
                         'ISO dates shown to a user: ' + ', '.join(offenders))
