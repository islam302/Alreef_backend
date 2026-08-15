from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.users.models import Address, CustomerProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_customer_profile(sender, instance, created, **kwargs):
    if created:
        CustomerProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=Address)
def enforce_single_default_address(sender, instance, **kwargs):
    if instance.is_default:
        Address.objects.filter(user=instance.user, is_default=True).exclude(pk=instance.pk).update(is_default=False)
