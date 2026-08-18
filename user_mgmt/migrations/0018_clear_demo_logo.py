"""Take the uploaded logo off the public demo tenant.

`/demo/` signs every visitor in as an ADMIN of that hospital, so before
`saas.utils.is_demo_hospital` locked the settings screen anybody could upload a
logo for everybody who came afterwards — and somebody did: a 1 MB photograph of
a pair of trainers, which is what `sehatyar.online/demo/login/` showed. The lock
stops the next write; it cannot undo the one already stored, and the row was
only invisible for as long as `/media/` was returning 404.

Data only, no schema — see the DDL-after-DML rule in CLAUDE.md.

The **file** in MEDIA_ROOT is deliberately left where it is. A migration that
deletes files cannot be reversed, and a stray upload costs a megabyte of disk;
being wrong about which row referenced it would cost a tenant their logo.
"""
from django.db import migrations

# saas.utils.DEMO_SLUG. Written out rather than imported: a migration must keep
# describing the database as it was when it ran, not follow a constant that
# later changes.
DEMO_SLUG = 'demo'


def clear_demo_logo(apps, schema_editor):
    SiteSettings = apps.get_model('user_mgmt', 'SiteSettings')
    (SiteSettings.objects
     .filter(hospital__slug=DEMO_SLUG)
     .exclude(logo_image='')
     .exclude(logo_image=None)
     .update(logo_image=None))


class Migration(migrations.Migration):

    dependencies = [
        ('user_mgmt', '0017_alter_sitesettings_accent_color_and_more'),
    ]

    operations = [
        migrations.RunPython(clear_demo_logo, migrations.RunPython.noop),
    ]
