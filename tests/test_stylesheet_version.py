"""Every template links the same version of app.css.

`app.css` is linked from six templates, each with its own `?v=` cache-buster,
and editing the stylesheet means bumping all six. Miss one and the failure is
particular nasty: that page keeps the *old* stylesheet out of the browser cache
while every other page has the new one, so a layout fix appears to work
everywhere except the one screen somebody is looking at — and it looks like a
CSS bug rather than a stale file.

Nothing enforces this but the note in CLAUDE.md, which is to say a person
remembering. This is the same check, done by a machine.

    python manage.py test tests.test_stylesheet_version --settings=pharma_mgmt.test_settings
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

LINK = re.compile(r"app\.css['\"]?\s*%\}\?v=([0-9.]+)")


def _templates():
    root = Path(settings.BASE_DIR)
    for pattern in ('templates/**/*.html', '*/templates/**/*.html'):
        for path in root.glob(pattern):
            if '.venv' in path.parts or 'site-packages' in path.parts:
                continue
            yield path


class StylesheetVersionTest(SimpleTestCase):

    def test_every_link_to_app_css_carries_the_same_version(self):
        found = {}
        for path in _templates():
            for version in LINK.findall(path.read_text(encoding='utf-8')):
                found.setdefault(version, []).append(
                    str(path.relative_to(settings.BASE_DIR)))

        self.assertTrue(found, 'no app.css links found — has the link changed shape?')
        self.assertEqual(
            len(found), 1,
            'app.css is linked at more than one version, so one page will be '
            'served a stale stylesheet from the browser cache: '
            + '; '.join(f'v={v} in {", ".join(files)}' for v, files in sorted(found.items())))

    def test_the_stylesheet_actually_exists_at_that_path(self):
        self.assertTrue((Path(settings.BASE_DIR) / 'static' / 'css' / 'app.css').exists())

    def test_form_actions_is_styled_at_all(self):
        """It is used in ~40 templates and was styled in none of them, so every
        submit button sat flush against whatever was above it."""
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        self.assertIn('.form-actions', css)
        self.assertRegex(css, r'\.form-actions\s*\{[^}]*margin-top')

    def test_form_actions_buttons_are_not_allowed_to_shrink(self):
        """`flex: 1` is a zero basis; `.btn` is nowrap + overflow hidden, so a
        squeezed button silently loses the end of its label."""
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        block = css[css.index('.form-actions > .btn'):]
        block = block[:block.index('}')]
        self.assertNotIn('flex: 1 1 0', block)
        self.assertIn('flex: 0 0 auto', block)
