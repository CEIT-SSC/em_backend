from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.core.exceptions import ValidationError

from .models import CompetitionTeam


@receiver(pre_delete, sender=CompetitionTeam)
def protect_registered_team(sender, instance, using, **kwargs):
    from shop.models import OrderItem
    if instance.group_competition_id or instance.registrations.using(using).exists() or OrderItem.objects.using(using).filter(
        content_type=ContentType.objects.db_manager(using).get_for_model(CompetitionTeam), object_id=instance.pk,
    ).exists():
        raise ValidationError('Teams with registrations or purchase history cannot be deleted.')
