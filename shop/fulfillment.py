import logging

from django.apps import apps
from django.db import models, transaction

from .models import DiscountRedemption, Order

logger = logging.getLogger(__name__)

Presentation = apps.get_model('events', 'Presentation')
SoloCompetition = apps.get_model('events', 'SoloCompetition')
CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
PresentationEnrollment = apps.get_model('events', 'PresentationEnrollment')
SoloCompetitionRegistration = apps.get_model('events', 'SoloCompetitionRegistration')


def process_successful_order(order):
    """Settle a paid order: enrollments, discounts, and completed status."""
    logger.info("Processing successful order: %s", order.order_id)
    with transaction.atomic():
        for order_item in order.items.all():
            content_object = order_item.content_object
            if not content_object:
                continue

            if isinstance(content_object, Presentation):
                PresentationEnrollment.objects.update_or_create(
                    user=order.user, presentation=content_object,
                    defaults={
                        'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
                        'order_item': order_item
                    }
                )
            elif isinstance(content_object, SoloCompetition):
                SoloCompetitionRegistration.objects.update_or_create(
                    user=order.user, solo_competition=content_object,
                    defaults={
                        'status': SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
                        'order_item': order_item
                    }
                )
            elif isinstance(content_object, CompetitionTeam):
                team = content_object
                team.status = CompetitionTeam.STATUS_ACTIVE
                team.save(update_fields=['status'])

        order.status = Order.STATUS_COMPLETED
        if order.discount_code_applied:
            discount = order.discount_code_applied
            _redemption, created = DiscountRedemption.objects.get_or_create(
                code=discount,
                user=order.user,
                order=order,
            )
            if created:
                discount.times_used = models.F('times_used') + 1
                discount.save(update_fields=['times_used'])
        order.save(update_fields=['status'])

    logger.info("Finished processing order: %s", order.order_id)
