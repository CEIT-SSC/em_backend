from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import PackItem


PACKABLE_MODELS = {
    'events.presentation',
    'events.solocompetition',
    'shop.product',
}


@receiver(post_delete, dispatch_uid='shop.delete_pack_items_for_deleted_target')
def delete_pack_items_for_deleted_target(sender, instance, **kwargs):
    """Keep generic pack relations from becoming orphaned."""
    if sender._meta.label_lower not in PACKABLE_MODELS:
        return

    content_type = ContentType.objects.get_for_model(sender)
    PackItem.objects.filter(
        content_type_id=content_type.pk,
        object_id=instance.pk,
    ).delete()
