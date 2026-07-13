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
