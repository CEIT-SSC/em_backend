import logging
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction

from events.models import (
    CompetitionTeam, CompetitionTeamRegistration,
    GroupCompetition,
    Presentation,
    PresentationEnrollment,
    SoloCompetition,
    SoloCompetitionRegistration,
)

from .models import Cart, CartItem, DiscountCode, DiscountRedemption, Order, OrderItem, Pack, Product
from .eligibility import capacity_scope_key, prerequisite_error_for_item

logger = logging.getLogger(__name__)


class OrderFulfillmentError(Exception):
    """Raised when an order cannot safely be fulfilled."""


class OrderCapacityError(OrderFulfillmentError):
    """Raised when an order item no longer has capacity."""


@dataclass
class PackBackfillResult:
    purchasers: int = 0
    granted: int = 0
    reactivated: int = 0
    already_owned: int = 0
    unavailable: int = 0

    def add(self, other):
        self.purchasers += other.purchasers
        self.granted += other.granted
        self.reactivated += other.reactivated
        self.already_owned += other.already_owned
        self.unavailable += other.unavailable
        return self


def _get_or_create_pack_component(parent_pack_item, content_type, item_object):
    order_item, created = OrderItem.objects.get_or_create(
        order=parent_pack_item.order,
        content_type=content_type,
        object_id=item_object.pk,
        defaults={
            'description': str(item_object),
            'price': Decimal('0'),
            'parent_pack': parent_pack_item,
        },
    )
    return order_item, created


def _grant_pack_item(parent_pack_item, pack_item, item_object):
    user = parent_pack_item.order.user

    if isinstance(item_object, Presentation):
        enrollment = PresentationEnrollment.objects.select_for_update().filter(
            user=user,
            presentation=item_object,
        ).first()
        if enrollment and enrollment.status == PresentationEnrollment.STATUS_COMPLETED_OR_FREE:
            return 'already_owned'
        if prerequisite_error_for_item(user, item_object):
            return 'unavailable'

        if enrollment and enrollment.order_item_id:
            enrollment.status = PresentationEnrollment.STATUS_COMPLETED_OR_FREE
            enrollment.save(update_fields=['status'])
            return 'reactivated'

        order_item, _created = _get_or_create_pack_component(
            parent_pack_item,
            pack_item.content_type,
            item_object,
        )
        if enrollment:
            enrollment.status = PresentationEnrollment.STATUS_COMPLETED_OR_FREE
            enrollment.order_item = order_item
            enrollment.save(update_fields=['status', 'order_item'])
            return 'reactivated'

        PresentationEnrollment.objects.create(
            user=user,
            presentation=item_object,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
            order_item=order_item,
        )
        return 'granted'

    if isinstance(item_object, SoloCompetition):
        registration = SoloCompetitionRegistration.objects.select_for_update().filter(
            user=user,
            solo_competition=item_object,
        ).first()
        if registration and registration.status == SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE:
            return 'already_owned'
        if prerequisite_error_for_item(user, item_object):
            return 'unavailable'

        if registration and registration.order_item_id:
            registration.status = SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE
            registration.save(update_fields=['status'])
            return 'reactivated'

        order_item, _created = _get_or_create_pack_component(
            parent_pack_item,
            pack_item.content_type,
            item_object,
        )
        if registration:
            registration.status = SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE
            registration.order_item = order_item
            registration.save(update_fields=['status', 'order_item'])
            return 'reactivated'

        SoloCompetitionRegistration.objects.create(
            user=user,
            solo_competition=item_object,
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
            order_item=order_item,
        )
        return 'granted'

    if isinstance(item_object, Product):
        already_owned = OrderItem.objects.filter(
            order__user=user,
            order__status=Order.STATUS_COMPLETED,
            content_type=pack_item.content_type,
            object_id=item_object.pk,
        ).exists()
        if already_owned:
            return 'already_owned'

        _get_or_create_pack_component(
            parent_pack_item,
            pack_item.content_type,
            item_object,
        )
        return 'granted'

    return 'unavailable'


def grant_current_pack_items_to_previous_purchasers(pack):
    """Grant a pack's current contents to users with a completed pack order.

    This deliberately bypasses registration windows and capacity because it is an
    explicit administrative correction for entitlements that were already sold.
    It is safe to run repeatedly: completed entitlements are left untouched.
    """

    result = PackBackfillResult()
    pack_content_type = ContentType.objects.get_for_model(Pack)

    with transaction.atomic():
        purchased_pack_items = list(
            OrderItem.objects.select_for_update()
            .select_related('order', 'order__user')
            .filter(
                content_type=pack_content_type,
                object_id=pack.pk,
                parent_pack__isnull=True,
                order__status=Order.STATUS_COMPLETED,
                order__user__isnull=False,
            )
            .order_by('order__created_at', 'pk')
        )
        current_items = list(pack.items.select_related('content_type'))
        seen_users = set()

        for purchased_pack_item in purchased_pack_items:
            user_id = purchased_pack_item.order.user_id
            if user_id in seen_users:
                continue
            seen_users.add(user_id)
            result.purchasers += 1

            for pack_item in current_items:
                item_object = pack_item.content_object
                outcome = (
                    _grant_pack_item(purchased_pack_item, pack_item, item_object)
                    if item_object is not None
                    else 'unavailable'
                )
                setattr(result, outcome, getattr(result, outcome) + 1)

    return result


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
            status__in=[SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE, SoloCompetitionRegistration.STATUS_PENDING_PAYMENT],
        ).count() < item_object.max_participants

    if isinstance(item_object, CompetitionTeamRegistration):
        return item_object.status in CompetitionTeamRegistration.RESERVED_STATUSES
    if isinstance(item_object, CompetitionTeam):
        from events.services import legacy_registration
        return has_capacity(legacy_registration(item_object))

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

    if isinstance(item_object, (CompetitionTeam, CompetitionTeamRegistration)):
        return item_object.status == CompetitionTeam.STATUS_ACTIVE

    return False


def _lock_capacity_scope(item_object):
    if isinstance(item_object, CompetitionTeamRegistration):
        from events.services import lock_registration
        return lock_registration(item_object.pk)
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
        targets = []
        for order_item in order_items:
            item_object = order_item.content_object
            if item_object is None:
                raise OrderFulfillmentError(
                    f"Order item {order_item.pk} no longer references an available object."
                )
            targets.append((order_item, item_object))

        locked_items = []
        for order_item, item_object in sorted(targets, key=lambda target: capacity_scope_key(target[1])):
            item_object = _lock_capacity_scope(item_object)
            error = prerequisite_error_for_item(order.user, item_object)
            if error:
                raise OrderFulfillmentError(error)
            if isinstance(item_object, SoloCompetition):
                from events.services import ensure_solo_capacity, CompetitionError
                registration = item_object.registrations.filter(user=order.user).first()
                if registration and registration.status == SoloCompetitionRegistration.STATUS_PENDING_PAYMENT and registration.order_item_id != order_item.pk:
                    raise OrderFulfillmentError('Another order holds this solo registration.')
                try:
                    ensure_solo_capacity(item_object, exclude_user=order.user_id)
                except CompetitionError as exc:
                    raise OrderCapacityError(str(exc)) from exc
            elif not _already_fulfilled(order, item_object) and not has_capacity(item_object):
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
            elif isinstance(item_object, CompetitionTeamRegistration):
                from events.fulfillment import activate_team_registration
                from events.services import CompetitionError
                try:
                    activate_team_registration(item_object.pk, order_item)
                except CompetitionError as exc:
                    raise OrderFulfillmentError(str(exc)) from exc
            elif isinstance(item_object, CompetitionTeam):
                # Old order integrations are supported only while unambiguous.
                from events.services import legacy_registration
                from events.fulfillment import activate_team_registration
                registration = legacy_registration(item_object)
                if registration.order_item_id is None:
                    registration.order_item = order_item
                    registration.save(update_fields=['order_item'])
                activate_team_registration(registration.pk, order_item)

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


def process_successful_order(order):
    """Compatibility wrapper for wallet payment services."""
    fulfilled_order, _created = fulfill_order(order)
    return fulfilled_order


def release_order_reservations(order):
    """Release domain reservations held by an order that will not be fulfilled."""

    from events.services import lock_registration, sync_legacy, legacy_registration
    with transaction.atomic():
        for order_item in order.items.select_related('content_type'):
            item_object = order_item.content_object
            if isinstance(item_object, CompetitionTeam):
                item_object = legacy_registration(item_object)
                if not item_object.order_item_id:
                    item_object.order_item = order_item
                    item_object.save(update_fields=['order_item'])
            if isinstance(item_object, CompetitionTeamRegistration):
                registration = lock_registration(item_object.pk)
                if registration.status == CompetitionTeamRegistration.PENDING_PAYMENT and registration.order_item_id == order_item.pk:
                    registration.order_item = None
                    registration.save(update_fields=['order_item', 'updated_at'])
                    sync_legacy(registration)
            elif isinstance(item_object, SoloCompetition):
                SoloCompetition.objects.select_for_update().get(pk=item_object.pk)
                SoloCompetitionRegistration.objects.filter(
                    order_item=order_item, status=SoloCompetitionRegistration.STATUS_PENDING_PAYMENT,
                ).update(status=SoloCompetitionRegistration.STATUS_CANCELLED, order_item=None)
