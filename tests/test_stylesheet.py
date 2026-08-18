"""app.css: one version everywhere, and the layout traps that keep recurring.

Two things are checked here, and both had been "a note in CLAUDE.md", which is
to say a person remembering.

**One version.** `app.css` is linked from six templates, each with its own `?v=`
cache-buster, and editing the stylesheet means bumping all six. Missing one is
worse than missing all of them: that page keeps the *old* stylesheet out of the
browser cache while every other page has the new one, so a layout fix appears to
work everywhere except the screen somebody is looking at — and it reads as a CSS
bug rather than a stale file.

**No negative margin under a field.** Every text input is `width: 100%` with a
border, so a help paragraph given a negative top margin to close the gap slides
up *into* that border and the border draws a line through the first line of the
sentence. On screen it looks like a stray horizontal rule; in the template it is
invisible. That is why it shipped twice.

    python manage.py test tests.test_stylesheet --settings=pharma_mgmt.test_settings
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


def _css():
    return (Path(settings.BASE_DIR) / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')


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


class FormActionsTest(SimpleTestCase):

    def test_form_actions_is_styled_at_all(self):
        """It is used in ~40 templates and was styled in none of them, so every
        submit button sat flush against whatever was above it."""
        css = _css()
        self.assertIn('.form-actions', css)
        self.assertRegex(css, r'\.form-actions\s*\{[^}]*margin-top')

    def test_form_actions_buttons_are_not_allowed_to_shrink(self):
        """`flex: 1` is a zero basis; `.btn` is nowrap + overflow hidden, so a
        squeezed button silently loses the end of its label."""
        css = _css()
        block = css[css.index('.form-actions > .btn'):]
        block = block[:block.index('}')]
        self.assertNotIn('flex: 1 1 0', block)
        self.assertIn('flex: 0 0 auto', block)


class NegativeMarginUnderAFieldTest(SimpleTestCase):
    """A help paragraph must not be pulled up into the field above it.

    The input's border ends up drawn straight through the text. Use the
    `.field-help` class; this fails the next one written by hand.
    """

    # A tag that opens with a negative top margin, after a field. Intervening
    # *closing* tags are allowed through: the help paragraph often sits after the
    # </div> that ends a grid of labels, which is the same collision — and is
    # exactly the case the first, stricter version of this pattern walked past.
    AFTER_FIELD = re.compile(
        r'(</label>|</select>|</textarea>|<input\b[^>]*>)'
        r'(?:\s*</[a-z]+>)*\s*'
        r'<[a-z]+[^>]*style="[^"]*margin(?:-top)?\s*:\s*-',
        re.IGNORECASE)

    def test_no_template_pulls_help_text_into_the_field_above_it(self):
        offenders = []
        for path in _templates():
            text = path.read_text(encoding='utf-8')
            for match in self.AFTER_FIELD.finditer(text):
                line = text[:match.start()].count(chr(10)) + 1
                offenders.append(f'{path.relative_to(settings.BASE_DIR)}:{line}')
        self.assertEqual(
            offenders, [],
            'negative top margin directly after a form field — it collides with '
            'the input border and draws a line through the text. Use the '
            '.field-help class instead: ' + ', '.join(offenders))

    def test_the_class_it_should_use_exists(self):
        self.assertIn('.field-help', _css())
