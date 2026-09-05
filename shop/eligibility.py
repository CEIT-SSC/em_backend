from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from shop.models import Order, OrderItem, Product

Presentation = apps.get_model('events', 'Presentation')
SoloCompetition = apps.get_model('events', 'SoloCompetition')
CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
PresentationEnrollment = apps.get_model('events', 'PresentationEnrollment')
SoloCompetitionRegistration = apps.get_model('events', 'SoloCompetitionRegistration')
TeamMembership = apps.get_model('events', 'TeamMembership')


class OrderPaymentEligibilityError(Exception):
    pass


def is_content_available(obj):
    if obj is None:
        return False
    if getattr(obj, 'is_active', True) is False:
        return False

    event = getattr(obj, 'event', None)
    if event is not None and getattr(event, 'is_active', True) is False:
        return False

    if isinstance(obj, CompetitionTeam):
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
    user_to_check = item_object.leader if isinstance(item_object, CompetitionTeam) else user

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
    elif isinstance(item_object, CompetitionTeam):
        if item_object.status == CompetitionTeam.STATUS_ACTIVE and (
            item_object.leader_id == user.id
            or TeamMembership.objects.filter(team=item_object, user=user).exists()
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
    user_to_check = item_object.leader if isinstance(item_object, CompetitionTeam) else user
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
        return competition.teams.filter(
            status=CompetitionTeam.STATUS_ACTIVE,
        ).count() < competition.max_teams

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
    start_time = None
    if isinstance(item_object, (Presentation, SoloCompetition)):
        start_time = getattr(item_object, 'start_time', None) or getattr(
            item_object, 'start_datetime', None,
        )
    elif isinstance(item_object, CompetitionTeam):
        start_time = getattr(item_object.group_competition, 'start_datetime', None)
    return not start_time or timezone.now() <= start_time


def validate_order_items_for_payment(order):
    """Lock and recheck every item immediately before a payment is settled."""
    for order_item in order.items.select_related('content_type').order_by('pk'):
        item_object = order_item.content_object
        if item_object is None:
            raise OrderPaymentEligibilityError(
                f"Order item {order_item.pk} no longer exists."
            )

        if isinstance(item_object, CompetitionTeam):
            competition = item_object.group_competition
            type(competition).objects.select_for_update().get(pk=competition.pk)

        item_object = type(item_object).objects.select_for_update().get(pk=item_object.pk)
        if not is_content_available(item_object):
            raise OrderPaymentEligibilityError(
                f"{order_item.description} is no longer available."
            )
        if not is_registration_open(item_object):
            raise OrderPaymentEligibilityError(
                f"Registration for {order_item.description} has closed."
            )
        if is_already_owned(order.user, item_object):
            raise OrderPaymentEligibilityError(
                f"{order_item.description} is already owned."
            )
        if not has_capacity(item_object):
            raise OrderPaymentEligibilityError(
                f"{order_item.description} is sold out or at capacity."
            )
