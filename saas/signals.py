from django.db.backends.signals import connection_created
from django.db.models.signals import pre_save
from django.dispatch import receiver
from saas.utils import get_current_hospital

@receiver(pre_save)
def auto_assign_hospital(sender, instance, **kwargs):
    # Check if the model has a 'hospital' field and it is not currently set
    if hasattr(instance, 'hospital') and not instance.hospital:
        hospital = get_current_hospital()
        if hospital:
            instance.hospital = hospital


@receiver(connection_created)
def _tune_sqlite(sender, connection, **kwargs):
    """The production host (JabraHost / desktop build) runs on SQLite. WAL lets
    readers and a writer work concurrently instead of blocking the whole file,
    and NORMAL sync + a busy timeout cut the per-request cost on a small shared
    host. Harmless on Postgres (skipped) and on the in-memory test DB (WAL is a
    no-op there)."""
    if connection.vendor != 'sqlite':
        return
    cursor = connection.cursor()
    cursor.execute('PRAGMA journal_mode=WAL;')
    cursor.execute('PRAGMA synchronous=NORMAL;')
    cursor.execute('PRAGMA busy_timeout=5000;')
    cursor.execute('PRAGMA temp_store=MEMORY;')
