from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from shop.models import Order, OrderItem, Pack, Product

Presentation = apps.get_model('events', 'Presentation')
SoloCompetition = apps.get_model('events', 'SoloCompetition')
CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
Registration = apps.get_model('events', 'CompetitionTeamRegistration')
PresentationEnrollment = apps.get_model('events', 'PresentationEnrollment')
SoloCompetitionRegistration = apps.get_model('events', 'SoloCompetitionRegistration')
TeamMembership = apps.get_model('events', 'TeamMembership')


class OrderPaymentEligibilityError(Exception):
    pass


def capacity_scope_key(item_object):
    """Use the same lock order across checkout validation and fulfillment."""
    if isinstance(item_object, (CompetitionTeam, Registration)):
        competition = item_object.group_competition
        return ('events.groupcompetition', competition.pk if competition else 0)
    return (item_object._meta.label_lower, item_object.pk)


def pack_components(pack):
    return [item.content_object for item in pack.items.select_related('content_type').all()]


def purchase_item_keys(item_object):
    """Return the concrete entitlements represented by a cart item."""
    if isinstance(item_object, Pack):
        return {
            (item.content_type_id, item.object_id)
            for item in item_object.items.all()
        }
    content_type = ContentType.objects.get_for_model(item_object)
    return {(content_type.pk, item_object.pk)}


def is_content_available(obj):
    if obj is None:
        return False
    if getattr(obj, 'is_active', True) is False:
        return False

    if isinstance(obj, Pack):
        components = pack_components(obj)
        return bool(components) and all(is_content_available(component) for component in components)

    event = getattr(obj, 'event', None)
    if event is not None and getattr(event, 'is_active', True) is False:
        return False

    if isinstance(obj, (CompetitionTeam, Registration)):
        competition = getattr(obj, 'group_competition', None)
        if competition is not None:
            if getattr(competition, 'is_active', True) is False:
                return False
            event = getattr(competition, 'event', None)
            if event is not None and getattr(event, 'is_active', True) is False:
                return False
    return True


def is_cart_item_active(cart_item):
    try:
        return is_content_available(cart_item.content_object)
    except Exception:
        return False


def is_already_owned(user, item_object):
    if isinstance(item_object, Pack):
        content_type = ContentType.objects.get_for_model(Pack)
        if OrderItem.objects.filter(
            content_type=content_type,
            object_id=item_object.pk,
            order__user=user,
            order__status=Order.STATUS_COMPLETED,
        ).exists():
            return True
        return any(is_already_owned(user, component) for component in pack_components(item_object))

    user_to_check = item_object.leader if isinstance(item_object, (CompetitionTeam, Registration)) else user

    if isinstance(item_object, Presentation):
        enrollment_status = PresentationEnrollment.objects.filter(
            user=user_to_check,
            presentation=item_object,
        ).values_list('status', flat=True).first()
        if enrollment_status == PresentationEnrollment.STATUS_CANCELLED:
            return False
        if enrollment_status == PresentationEnrollment.STATUS_COMPLETED_OR_FREE:
            return True
    elif isinstance(item_object, SoloCompetition):
        if SoloCompetitionRegistration.objects.filter(
            user=user_to_check,
            solo_competition=item_object,
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
        ).exists():
            return True
    elif isinstance(item_object, Registration):
        return item_object.status == Registration.ACTIVE and (
            item_object.leader_id == user.pk or item_object.members.filter(user=user).exists())
    elif isinstance(item_object, CompetitionTeam):
        if item_object.status == CompetitionTeam.STATUS_ACTIVE and (
            item_object.leader_id == user.id
            or TeamMembership.objects.filter(team=item_object, user=user, status='accepted').exists()
        ):
            return True

    content_type = ContentType.objects.get_for_model(item_object)
    return OrderItem.objects.filter(
        content_type=content_type,
        object_id=item_object.pk,
        order__user=user_to_check,
        order__status=Order.STATUS_COMPLETED,
    ).exists()


def is_pending(user, item_object):
    if isinstance(item_object, Pack):
        content_type = ContentType.objects.get_for_model(Pack)
        directly_pending = OrderItem.objects.filter(
            content_type=content_type,
            object_id=item_object.pk,
            order__user=user,
            order__status__in=[
                Order.STATUS_PENDING_PAYMENT,
                Order.STATUS_PROCESSING_ENROLLMENT,
            ],
        ).exists()
        return directly_pending or any(is_pending(user, component) for component in pack_components(item_object))

    user_to_check = item_object.leader if isinstance(item_object, (CompetitionTeam, Registration)) else user
    content_type = ContentType.objects.get_for_model(item_object)
    return OrderItem.objects.filter(
        content_type=content_type,
        object_id=item_object.pk,
        order__user=user_to_check,
        order__status__in=[
            Order.STATUS_PENDING_PAYMENT,
            Order.STATUS_PROCESSING_ENROLLMENT,
        ],
    ).exists()


def is_already_owned_or_pending(user, item_object):
    return is_already_owned(user, item_object) or is_pending(user, item_object)


def has_capacity(item_object, user=None):
    if isinstance(item_object, Pack):
        components = pack_components(item_object)
        return bool(components) and all(has_capacity(component, user=user) for component in components)

    if isinstance(item_object, Presentation):
        if item_object.capacity is None:
            return True
        return item_object.enrollments.filter(
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
        ).count() < item_object.capacity

    if isinstance(item_object, SoloCompetition):
        if item_object.max_participants is None:
            return True
        reserved = item_object.registrations.filter(
            status__in=[SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE, SoloCompetitionRegistration.STATUS_PENDING_PAYMENT],
        )
        if user is not None:
            reserved = reserved.exclude(user=user)
        return reserved.count() < item_object.max_participants

    if isinstance(item_object, Registration):
        # Registration already holds a seat, including the final available seat.
        return item_object.status in Registration.RESERVED_STATUSES
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


def is_registration_open(item_object):
    if isinstance(item_object, Pack):
        components = pack_components(item_object)
        return bool(components) and (
            item_object.bypass_item_time_limits
            or all(is_registration_open(component) for component in components)
        )

    start_time = None
    if isinstance(item_object, (Presentation, SoloCompetition)):
        start_time = getattr(item_object, 'start_time', None) or getattr(
            item_object, 'start_datetime', None,
        )
    elif isinstance(item_object, (CompetitionTeam, Registration)):
        start_time = getattr(item_object.group_competition, 'start_datetime', None)
    return not start_time or timezone.now() <= start_time


def validate_order_items_for_payment(order):
    """Lock and recheck every item immediately before a payment is settled."""
    order_items = order.items.select_related(
        'content_type', 'parent_pack__content_type',
    ).order_by('pk')
    targets = []
    for order_item in order_items:
        item_object = order_item.content_object
        if item_object is None:
            raise OrderPaymentEligibilityError(
                f"Order item {order_item.pk} no longer exists."
            )
        targets.append((order_item, item_object))

    for order_item, item_object in sorted(targets, key=lambda target: capacity_scope_key(target[1])):
        if isinstance(item_object, (CompetitionTeam, Registration)):
            competition = item_object.group_competition
            type(competition).objects.select_for_update().get(pk=competition.pk)

        item_object = type(item_object).objects.select_for_update().get(pk=item_object.pk)
        if not is_content_available(item_object):
            raise OrderPaymentEligibilityError(
                f"{order_item.description} is no longer available."
            )
        parent_pack = (
            order_item.parent_pack.content_object
            if order_item.parent_pack_id
            else None
        )
        bypass_time_limit = (
            isinstance(parent_pack, Pack)
            and parent_pack.bypass_item_time_limits
        )
        if not bypass_time_limit and not is_registration_open(item_object):
            raise OrderPaymentEligibilityError(
                f"Registration for {order_item.description} has closed."
            )
        if is_already_owned(order.user, item_object):
            raise OrderPaymentEligibilityError(
                f"{order_item.description} is already owned."
            )
        if isinstance(item_object, Registration):
            if (item_object.status != Registration.PENDING_PAYMENT or
                item_object.order_item_id != order_item.pk or item_object.leader_id != order.user_id):
                raise OrderPaymentEligibilityError('This order does not own an approved team registration.')
            if item_object.price != order_item.price:
                raise OrderPaymentEligibilityError('The team registration price has changed.')
        if isinstance(item_object, SoloCompetition):
            from events.services import ensure_solo_capacity, CompetitionError
            registration = item_object.registrations.filter(user=order.user).first()
            if registration and registration.status == SoloCompetitionRegistration.STATUS_PENDING_PAYMENT and registration.order_item_id != order_item.pk:
                raise OrderPaymentEligibilityError('Another order holds this solo registration.')
            try:
                ensure_solo_capacity(item_object, exclude_user=order.user_id)
            except CompetitionError as exc:
                raise OrderPaymentEligibilityError(str(exc)) from exc
            continue
        if not has_capacity(item_object):
            raise OrderPaymentEligibilityError(
                f"{order_item.description} is sold out or at capacity."
            )
