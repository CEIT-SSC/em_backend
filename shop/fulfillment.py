import logging

from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction

from events.models import (
    CompetitionTeam,
    GroupCompetition,
    Presentation,
    PresentationEnrollment,
    SoloCompetition,
    SoloCompetitionRegistration,
)

from .models import Cart, CartItem, DiscountCode, DiscountRedemption, Order, OrderItem, Product

logger = logging.getLogger(__name__)


class OrderFulfillmentError(Exception):
    """Raised when an order cannot safely be fulfilled."""


class OrderCapacityError(OrderFulfillmentError):
    """Raised when an order item no longer has capacity."""


def has_capacity(item_object):
    if isinstance(item_object, Presentation):
        if item_object.capacity is None:
            return True
        return item_object.enrollments.filter(
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
        ).count() < item_object.capacity

    if isinstance(item_object, SoloCompetition):
        if item_object.max_participants is None:
            return True
        return item_object.registrations.filter(
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
        ).count() < item_object.max_participants

    if isinstance(item_object, CompetitionTeam):
        competition = item_object.group_competition
        if competition.max_teams is None:
            return True
        return competition.teams.filter(status=CompetitionTeam.STATUS_ACTIVE).count() < competition.max_teams

    if isinstance(item_object, Product):
        if item_object.capacity is None:
            return True
        content_type = ContentType.objects.get_for_model(Product)
        sold_count = OrderItem.objects.filter(
            content_type=content_type,
            object_id=item_object.pk,
            order__status=Order.STATUS_COMPLETED,
        ).count()
        return sold_count < item_object.capacity

    return True


def _already_fulfilled(order, item_object):
    if isinstance(item_object, Presentation):
        return PresentationEnrollment.objects.filter(
            user=order.user,
            presentation=item_object,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
        ).exists()

    if isinstance(item_object, SoloCompetition):
        return SoloCompetitionRegistration.objects.filter(
            user=order.user,
            solo_competition=item_object,
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
        ).exists()

    if isinstance(item_object, CompetitionTeam):
        return item_object.status == CompetitionTeam.STATUS_ACTIVE

    return False


def _lock_capacity_scope(item_object):
    if isinstance(item_object, CompetitionTeam) and item_object.group_competition_id:
        GroupCompetition.objects.select_for_update().get(pk=item_object.group_competition_id)
        return CompetitionTeam.objects.select_for_update().get(pk=item_object.pk)

    return type(item_object).objects.select_for_update().get(pk=item_object.pk)


def _clear_fulfilled_cart_items(order):
    if not order.user_id:
        return

    cart = Cart.objects.select_for_update().filter(user_id=order.user_id).first()
    if not cart:
        return

    for order_item in order.items.all():
        CartItem.objects.filter(
            cart=cart,
            content_type_id=order_item.content_type_id,
            object_id=order_item.object_id,
        ).delete()

    if (
        order.discount_code_applied_id
        and cart.applied_discount_code_id == order.discount_code_applied_id
    ):
        cart.applied_discount_code = None
        cart.save(update_fields=['applied_discount_code'])


def fulfill_order(order):
    """Fulfill an order once, independently of how its payment was completed."""

    with transaction.atomic():
        order = (
            Order.objects.select_for_update()
            .select_related('discount_code_applied')
            .get(pk=order.pk)
        )

        if order.status == Order.STATUS_COMPLETED:
            return order, False

        allowed_statuses = {
            Order.STATUS_PENDING_PAYMENT,
            Order.STATUS_PROCESSING_ENROLLMENT,
        }
        if order.status not in allowed_statuses:
            raise OrderFulfillmentError(
                f"Order {order.order_id} cannot be fulfilled from status '{order.status}'."
            )

        order_items = list(order.items.select_related('content_type'))
        locked_items = []
        for order_item in order_items:
            item_object = order_item.content_object
            if item_object is None:
                raise OrderFulfillmentError(
                    f"Order item {order_item.pk} no longer references an available object."
                )

            item_object = _lock_capacity_scope(item_object)
            if not _already_fulfilled(order, item_object) and not has_capacity(item_object):
                raise OrderCapacityError(f"No capacity remains for '{order_item.description}'.")
            locked_items.append((order_item, item_object))

        order.status = Order.STATUS_PROCESSING_ENROLLMENT
        order.save(update_fields=['status'])

        for order_item, item_object in locked_items:
            if isinstance(item_object, Presentation):
                PresentationEnrollment.objects.update_or_create(
                    user=order.user,
                    presentation=item_object,
                    defaults={
                        'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
                        'order_item': order_item,
                    },
                )
            elif isinstance(item_object, SoloCompetition):
                SoloCompetitionRegistration.objects.update_or_create(
                    user=order.user,
                    solo_competition=item_object,
                    defaults={
                        'status': SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
                        'order_item': order_item,
                    },
                )
            elif isinstance(item_object, CompetitionTeam):
                item_object.status = CompetitionTeam.STATUS_ACTIVE
                item_object.save(update_fields=['status'])

        if order.discount_code_applied_id:
            discount = DiscountCode.objects.select_for_update().get(pk=order.discount_code_applied_id)
            _, redemption_created = DiscountRedemption.objects.get_or_create(
                code=discount,
                user=order.user,
                order=order,
            )
            if redemption_created:
                DiscountCode.objects.filter(pk=discount.pk).update(times_used=models.F('times_used') + 1)

        order.status = Order.STATUS_COMPLETED
        order.save(update_fields=['status'])
        _clear_fulfilled_cart_items(order)

    logger.info("Fulfilled order %s", order.order_id)
    return order, True


def release_order_reservations(order):
    """Release domain reservations held by an order that will not be fulfilled."""

    with transaction.atomic():
        for order_item in order.items.select_related('content_type'):
            item_object = order_item.content_object
            if (
                isinstance(item_object, CompetitionTeam)
                and item_object.status == CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION
            ):
                team = CompetitionTeam.objects.select_for_update().get(pk=item_object.pk)
                if team.status == CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION:
                    team.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
                    team.save(update_fields=['status'])
