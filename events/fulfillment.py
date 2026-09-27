"""Domain fulfillment called only after shop order payment settlement (or zero total)."""
from django.db import transaction
from django.utils import timezone

from .models import CompetitionTeamRegistration as Registration
from .services import CompetitionError, lock_registration, sync_legacy


@transaction.atomic
def activate_team_registration(registration_id, order_item):
    registration = lock_registration(registration_id)
    if registration.status == Registration.ACTIVE:
        if registration.order_item_id != order_item.pk:
            raise CompetitionError('This registration was fulfilled by another order.', 'order_mismatch')
        return registration, False
    if registration.status != Registration.PENDING_PAYMENT:
        raise CompetitionError('Only approved unpaid registrations can be fulfilled.')
    if registration.order_item_id != order_item.pk or order_item.order.user_id != registration.leader_id:
        raise CompetitionError('The settled order does not belong to this registration.', 'order_mismatch')
    if order_item.price != registration.price:
        raise CompetitionError('The settled order price does not match the registration.', 'price_mismatch')
    if order_item.order.total_amount > 0 and order_item.order.paid_at is None:
        raise CompetitionError('The order has not been settled.', 'payment_not_settled')
    registration.status = Registration.ACTIVE
    registration.activated_at = timezone.now()
    registration.save(update_fields=['status', 'activated_at', 'updated_at'])
    sync_legacy(registration)
    return registration, True
