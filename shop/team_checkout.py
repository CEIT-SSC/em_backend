"""Translate a domain registration into a payable shop order."""
from django.db import transaction
from django.utils import timezone

from events.models import CompetitionTeamRegistration as Registration
from events.services import CompetitionError, lock_registration, require_leader, sync_legacy, validate_open
from events.prerequisites import prerequisite_error
from .models import Order, OrderItem


@transaction.atomic
def prepare_team_order(registration_id, actor):
    registration = lock_registration(registration_id)
    require_leader(registration.team, actor)
    if registration.status == Registration.ACTIVE:
        return registration.order_item.order if registration.order_item_id else None
    if registration.status != Registration.PENDING_PAYMENT:
        raise CompetitionError('This registration is not approved and awaiting payment.')
    error = prerequisite_error(registration.competition, registration.members.values_list('user_id', flat=True))
    if error:
        raise CompetitionError(error, 'prerequisite_required')
    validate_open(registration.competition)
    if registration.price == 0:
        registration.status = Registration.ACTIVE
        registration.activated_at = timezone.now()
        registration.save(update_fields=['status', 'activated_at', 'updated_at'])
        sync_legacy(registration)
        return None
    if registration.order_item_id:
        order = registration.order_item.order
        if order.status in (Order.STATUS_PENDING_PAYMENT, Order.STATUS_PAYMENT_FAILED):
            return order
        raise CompetitionError('The linked order cannot be paid. Cancel or review it first.', 'order_not_payable')
    order = Order.objects.create(
        user=actor, event=registration.competition.event,
        subtotal_amount=registration.price, total_amount=registration.price,
    )
    item = OrderItem.objects.create(
        order=order, content_object=registration, price=registration.price,
        description=str(registration)[:255],
    )
    registration.order_item = item
    registration.save(update_fields=['order_item', 'updated_at'])
    sync_legacy(registration, payment_started=True)
    return order
