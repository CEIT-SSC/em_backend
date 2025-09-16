from django.shortcuts import redirect, get_object_or_404
from django.conf import settings
from django.utils import timezone
from django.db import transaction, models
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
from urllib.parse import urlencode
import logging
from rest_framework import viewsets, status, generics, views
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer, get_paginated_response_serializer
from .models import DiscountCode, Cart, CartItem, Order, OrderItem, DiscountRedemption, Product
from .serializers import (
    CartSerializer, AddToCartSerializer, ApplyDiscountSerializer,
    OrderSerializer, OrderListSerializer, PaymentInitiateResponseSerializer,
    UserPurchasesSerializer, OrderPaymentInitiateSerializer, CartItemSerializer,
    ProductSerializer, RemoveFromCartSerializer
)
from .payments import ZarrinPal


Presentation = apps.get_model('events', 'Presentation')
SoloCompetition = apps.get_model('events', 'SoloCompetition')
CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
PresentationEnrollment = apps.get_model('events', 'PresentationEnrollment')
SoloCompetitionRegistration = apps.get_model('events', 'SoloCompetitionRegistration')
TeamMembership = apps.get_model('events', 'TeamMembership')
CustomUser = apps.get_model(settings.AUTH_USER_MODEL)
Event = apps.get_model('events', 'Event')
logger = logging.getLogger(__name__)


def _release_reservations_for_orders(order_qs_or_list):
    CompetitionTeam = apps.get_model('events', 'CompetitionTeam')

    orders = order_qs_or_list if hasattr(order_qs_or_list, '__iter__') else [order_qs_or_list]
    with transaction.atomic():
        for order in orders:
            for item in order.items.all():
                obj = item.content_object
                if isinstance(obj, CompetitionTeam) and \
                        obj.status == CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION:
                    if obj.group_competition.requires_admin_approval:
                        obj.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
                    else:
                        obj.status = CompetitionTeam.STATUS_CANCELLED
                    obj.save(update_fields=["status"])


def _is_content_available(obj) -> bool:
    if obj is None:
        return False

    if hasattr(obj, "is_active") and obj.is_active is False:
        return False

    ev = getattr(obj, "event", None)
    if ev is not None and hasattr(ev, "is_active") and ev.is_active is False:
        return False

    try:
        from events.models import CompetitionTeam
        if isinstance(obj, CompetitionTeam):
            gc = getattr(obj, "group_competition", None)
            if gc is not None:
                if hasattr(gc, "is_active") and gc.is_active is False:
                    return False
                ev2 = getattr(gc, "event", None)
                if ev2 is not None and hasattr(ev2, "is_active") and ev2.is_active is False:
                    return False
    except Exception:
        pass

    return True


def _is_cart_item_active(ci) -> bool:
    try:
        return _is_content_available(ci.content_object)
    except Exception:
        return False


def _is_already_owned_or_pending(user, item_object):
    content_type = ContentType.objects.get_for_model(item_object)
    user_to_check = item_object.leader if isinstance(item_object, CompetitionTeam) else user

    if isinstance(item_object, Presentation):
        if PresentationEnrollment.objects.filter(user=user_to_check, presentation=item_object, status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).exists():
            return True
    if isinstance(item_object, SoloCompetition):
        if SoloCompetitionRegistration.objects.filter(user=user_to_check, solo_competition=item_object, status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE).exists():
            return True

    return OrderItem.objects.filter(
        content_type=content_type,
        object_id=item_object.pk,
        order__user=user_to_check,
        order__status__in=[
            Order.STATUS_PENDING_PAYMENT,
            Order.STATUS_AWAITING_GATEWAY_REDIRECT,
            Order.STATUS_PROCESSING_ENROLLMENT,
            Order.STATUS_COMPLETED,
        ]
    ).exists()


def _has_capacity(item_object):
    if isinstance(item_object, Presentation):
        if item_object.capacity is None: return True
        return item_object.enrollments.filter(
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).count() < item_object.capacity

    if isinstance(item_object, SoloCompetition):
        if item_object.max_participants is None: return True
        return item_object.registrations.filter(
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE).count() < item_object.max_participants

    if isinstance(item_object, CompetitionTeam):
        group_comp = item_object.group_competition
        if group_comp.max_teams is None: return True
        return group_comp.teams.filter(status=CompetitionTeam.STATUS_ACTIVE).count() < group_comp.max_teams

    if isinstance(item_object, Product):
        if item_object.capacity is None: return True
        content_type = ContentType.objects.get_for_model(Product)
        sold_count = OrderItem.objects.filter(
            content_type=content_type,
            object_id=item_object.pk,
            order__status=Order.STATUS_COMPLETED
        ).count()
        return sold_count < item_object.capacity

    return True


def _add_to_cart_and_update_status(user, item_object):
    cart, _ = Cart.objects.get_or_create(user=user)
    content_type = ContentType.objects.get_for_model(item_object)

    if CartItem.objects.filter(cart=cart, content_type=content_type, object_id=item_object.pk).exists():
        return False, "Item is already in your cart."

    CartItem.objects.create(cart=cart, content_type=content_type, object_id=item_object.pk)
    if isinstance(item_object, CompetitionTeam):
        item_object.status = CompetitionTeam.STATUS_IN_CART
        item_object.save(update_fields=['status'])

    return True, "Item added to your cart."


def _is_registration_open(item_object) -> bool:
    start_time = None
    if isinstance(item_object, (Presentation, SoloCompetition)):
        start_time = getattr(item_object, 'start_time', None) or getattr(item_object, 'start_datetime', None)
    elif isinstance(item_object, CompetitionTeam):
        start_time = getattr(item_object.group_competition, 'start_datetime', None)

    if start_time and timezone.now() > start_time:
        return False

    return True


@extend_schema(
    tags=['Shop - Orders & Payment'],
    summary="Cancel a pending order (by order_id)",
    responses={
        200: get_api_response_serializer(OrderSerializer),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    },
)
class OrderCancelView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id, *args, **kwargs):
        order = get_object_or_404(Order, order_id=order_id, user=request.user)

        cancellable_statuses = {
            Order.STATUS_PENDING_PAYMENT,
            Order.STATUS_PAYMENT_FAILED,
        }
        if order.status not in cancellable_statuses:
            return Response(
                {"error": f"Order cannot be cancelled in status '{order.get_status_display()}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            order.status = Order.STATUS_CANCELLED
            order.save(update_fields=["status"])
            _release_reservations_for_orders(order)

        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)


@extend_schema(tags=['Shop - Cart'])
class CartView(generics.RetrieveAPIView):
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        cart, _ = Cart.objects.get_or_create(user=self.request.user)
        event_param = self.request.query_params.get("event")

        if event_param:
            try:
                cart._filtered_items = cart.items.filter(event_id=int(event_param))
            except (TypeError, ValueError):
                cart._filtered_items = cart.items.filter(event_id__isnull=True)
        else:
            cart._filtered_items = cart.items.filter(event_id__isnull=True)

        return cart

    @extend_schema(
        summary="View user's shopping cart",
        request=None,
        responses={200: get_api_response_serializer(CartSerializer)},
        parameters=[
            OpenApiParameter(name='event', description='Filter cart items by event ID', required=False, type=int),
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


@extend_schema(
    tags=['Shop - Cart'],
    summary="Acquire an item (Enroll/Register/Buy)",
    description="The single endpoint to acquire any item. Handles free items directly or adds paid items to the cart.",
    request=AddToCartSerializer,
    responses={
        200: "Success (Item added to cart or already owned)",
        201: "Success (Enrolled in free item)",
        400: "Bad Request (Validation Error)",
        403: "Permission Denied",
        404: "Not Found",
    },
)
class AddToCartView(views.APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AddToCartSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        item_type_str = serializer.validated_data['item_type']
        item_id = serializer.validated_data['item_id']
        user = request.user

        item_model_map = {
            'presentation': Presentation, 'solo_competition': SoloCompetition,
            'competition_team': CompetitionTeam, 'product': Product,
        }
        item_model = item_model_map.get(item_type_str)
        if not item_model:
            return Response({"error": "Invalid item type."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            item_object = item_model.objects.get(pk=item_id)
        except item_model.DoesNotExist:
            return Response({"error": f"{item_type_str.capitalize()} not found."}, status=status.HTTP_404_NOT_FOUND)

        if not _is_content_available(item_object):
            return Response({"error": "This item is no longer available."}, status=status.HTTP_400_BAD_REQUEST)

        if not _is_registration_open(item_object):
            return Response({"error": "The registration period for this item has passed."}, status=status.HTTP_400_BAD_REQUEST)

        if _is_already_owned_or_pending(user, item_object):
            return Response({"message": "You already own this item or have a pending order for it."},
                            status=status.HTTP_200_OK)

        if not _has_capacity(item_object):
            return Response({"error": "This item is sold out or has reached full capacity."},
                            status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            if isinstance(item_object, (Presentation, SoloCompetition)):
                price = item_object.price if isinstance(item_object,
                                                        Presentation) else item_object.price_per_participant
                is_free = not item_object.is_paid or (price is not None and price <= 0)

                if is_free:
                    if isinstance(item_object, Presentation):
                        enrollment, _ = PresentationEnrollment.objects.update_or_create(
                            user=user, presentation=item_object,
                            defaults={'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE}
                        )
                    else:
                        registration, _ = SoloCompetitionRegistration.objects.update_or_create(
                            user=user, solo_competition=item_object,
                            defaults={'status': SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE}
                        )
                    return Response({"message": "Successfully enrolled/registered."}, status=status.HTTP_201_CREATED)
                else:
                    success, message = _add_to_cart_and_update_status(user, item_object)
                    status_code = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST
                    return Response({"message": message}, status=status_code)

            elif isinstance(item_object, Product):
                success, message = _add_to_cart_and_update_status(user, item_object)
                status_code = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST
                return Response({"message": message}, status=status_code)

            elif isinstance(item_object, CompetitionTeam):
                if item_object.leader != user:
                    return Response({"error": "Only the team leader can pay for the team."},
                                    status=status.HTTP_403_FORBIDDEN)

                if item_object.group_competition.requires_admin_approval and item_object.status != CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT:
                    return Response({"error": "This team has not been approved by an administrator yet."},
                                    status=status.HTTP_400_BAD_REQUEST)

                is_free = not item_object.group_competition.is_paid or (
                            item_object.group_competition.price_per_group is not None and item_object.group_competition.price_per_group <= 0)

                if is_free:
                    item_object.status = CompetitionTeam.STATUS_ACTIVE
                    item_object.save(update_fields=['status'])
                    return Response({"message": "Team registration is complete (free)."},
                                    status=status.HTTP_201_CREATED)
                else:
                    success, message = _add_to_cart_and_update_status(user, item_object)
                    status_code = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST
                    return Response({"message": message}, status=status_code)

        return Response({"error": "Unhandled item type."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    tags=['Shop - Cart'],
    summary="Remove an item from the cart by its type and ID",
    request=RemoveFromCartSerializer,
    responses={
        200: get_api_response_serializer(CartSerializer),
        404: ApiErrorResponseSerializer,
    },
)
class RemoveFromCartView(views.APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = RemoveFromCartSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        item_type_str = serializer.validated_data['item_type']
        item_id = serializer.validated_data['item_id']
        user = request.user

        cart, _ = Cart.objects.get_or_create(user=user)

        item_model_map = {
            'presentation': Presentation,
            'solo_competition': SoloCompetition,
            'competition_team': CompetitionTeam,
            'product': Product,
        }
        item_model = item_model_map.get(item_type_str)
        if not item_model:
            return Response({"error": "Invalid item type."}, status=status.HTTP_400_BAD_REQUEST)

        content_type = ContentType.objects.get_for_model(item_model)

        try:
            cart_item = CartItem.objects.get(
                cart=cart,
                content_type=content_type,
                object_id=item_id
            )
        except CartItem.DoesNotExist:
            return Response({"error": "Item not found in cart."}, status=status.HTTP_404_NOT_FOUND)

        content_object = cart_item.content_object
        cart_item.delete()

        if isinstance(content_object, CompetitionTeam) and content_object.status == CompetitionTeam.STATUS_IN_CART:
            if content_object.group_competition.requires_admin_approval:
                content_object.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
            else:
                content_object.status = CompetitionTeam.STATUS_CANCELLED
            content_object.save(update_fields=["status"])

        if cart.applied_discount_code and not cart._eligible_items_for_code(cart.applied_discount_code):
            cart.applied_discount_code = None
            cart.save(update_fields=['applied_discount_code'])

        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Shop - Cart'],
    summary="Apply discount code to cart",
    request=ApplyDiscountSerializer,
    responses={
        200: get_api_response_serializer(CartSerializer),
        400: ApiErrorResponseSerializer,
    },
)
class ApplyDiscountView(views.APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ApplyDiscountSerializer

    def post(self, request, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(user=request.user)
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        code_str = serializer.validated_data['code']
        try:
            discount_code = DiscountCode.objects.get(code__iexact=code_str)
            eligible_items = cart._eligible_items_for_code(discount_code)
            if not eligible_items:
                return Response({"error": "This code does not apply to any items in your cart."}, status=400)
            if not discount_code.has_remaining_user_quota(request.user):
                return Response({"error": "You have already used this code the maximum allowed times."}, status=400)
            if discount_code.is_valid(cart.get_subtotal()):
                cart.applied_discount_code = discount_code
                cart.save()
                return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)
            else:
                return Response({"error": "Discount code is not valid or applicable."},
                                status=status.HTTP_400_BAD_REQUEST)
        except DiscountCode.DoesNotExist:
            return Response({"error": "Invalid discount code."}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Shop - Cart'],
    summary="Remove discount code from cart",
    responses={200: get_api_response_serializer(CartSerializer)}
)
class RemoveDiscountView(views.APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(user=request.user)
        if cart.applied_discount_code:
            cart.applied_discount_code = None
            cart.save()
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Shop - Orders & Payment'],
    summary="Checkout cart and create an order",
    responses={
        201: get_api_response_serializer(OrderSerializer),
        400: ApiErrorResponseSerializer,
    },
    parameters=[
        OpenApiParameter(name='event', description='Event ID for the order', required=False, type=int),
    ]
)
class OrderCheckoutView(views.APIView):
    permission_classes = [IsAuthenticated]

    def _process_successful_order(self, order):
        logger.info(f"Processing successful order: {order.order_id}")
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
                            'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
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
                DiscountRedemption.objects.get_or_create(code=discount, user=order.user, order=order)
                discount.times_used = models.F('times_used') + 1
                discount.save(update_fields=['times_used'])
            order.save(update_fields=['status'])

        logger.info(f"Finished processing order: {order.order_id}")

    def post(self, request, *args, **kwargs):
        cart = (
            Cart.objects
            .filter(user=request.user)
            .prefetch_related('items__content_object')
            .first()
        )
        if not cart:
            return Response({"error": "Your cart is empty."}, status=status.HTTP_400_BAD_REQUEST)

        event_param = self.request.query_params.get("event")
        if event_param:
            try:
                event_id = int(event_param)
                event = Event.objects.get(pk=event_id)
                cart_items = cart.items.filter(event_id=event_id)
            except (TypeError, ValueError, Event.DoesNotExist):
                return Response({"error": "Invalid event specified."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            event = None
            cart_items = cart.items.filter(event_id__isnull=True)

        if not cart_items.exists():
            return Response({"error": "Your cart is empty for this event."}, status=status.HTTP_400_BAD_REQUEST)

        inactive_items = []
        for ci in cart_items.select_related('content_type'):
            if not _is_cart_item_active(ci):
                inactive_items.append({
                    "cart_item_id": ci.id,
                    "event_id": ci.event_id,
                    "object_id": ci.object_id,
                })
        if inactive_items:
            return Response(
                {
                    "success": False,
                    "statusCode": 400,
                    "message": "Some items are no longer available.",
                    "errors": {"inactive_items": inactive_items},
                    "data": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        subtotal = cart._subtotal_for_items(cart_items)
        discount_amount = cart.get_discount_amount()
        total_amount = subtotal - discount_amount

        if total_amount < 0:
            return Response({"error": "Order total cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

        if total_amount == 0:
            with transaction.atomic():
                order = Order.objects.create(
                    user=request.user, subtotal_amount=subtotal,
                    discount_code_applied=cart.applied_discount_code, discount_amount=discount_amount,
                    total_amount=total_amount, status=Order.STATUS_PROCESSING_ENROLLMENT,
                    paid_at=timezone.now(),
                    event=event
                )
                for cart_item in cart_items:
                    OrderItem.objects.create(
                        order=order, content_type=cart_item.content_type,
                        object_id=cart_item.object_id, description=str(cart_item.content_object),
                        price=CartItemSerializer().get_price(cart_item)
                    )
                    if isinstance(cart_item.content_object, CompetitionTeam):
                        team = cart_item.content_object
                        team.status = CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION
                        team.save()

                self._process_successful_order(order)

                cart_items.delete()
                cart.applied_discount_code = None
                cart.save()
            order.refresh_from_db()
            return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

        with transaction.atomic():
            order = Order.objects.create(
                user=request.user,
                subtotal_amount=subtotal,
                discount_code_applied=cart.applied_discount_code,
                discount_amount=discount_amount,
                total_amount=total_amount,
                status=Order.STATUS_PENDING_PAYMENT,
                event=event
            )
            for cart_item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    content_type=cart_item.content_type,
                    object_id=cart_item.object_id,
                    description=str(cart_item.content_object),
                    price=CartItemSerializer().get_price(cart_item)
                )

            cart_items.delete()
            cart.applied_discount_code = None
            cart.save(update_fields=['applied_discount_code'])

        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=['Shop - Orders & Payment'],
    summary="Initiate payment for an order via Zarinpal",
    responses={
        200: get_api_response_serializer(PaymentInitiateResponseSerializer),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
        500: ApiErrorResponseSerializer,
    },
)
class OrderPaymentInitiateView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id, *args, **kwargs):
        ser = OrderPaymentInitiateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        app_slug = (ser.validated_data.get("app") or "").strip().lower() or None

        order = get_object_or_404(Order, order_id=order_id, user=request.user)

        if order.status not in [Order.STATUS_PENDING_PAYMENT, Order.STATUS_PAYMENT_FAILED]:
            return Response({"error": f"Order not eligible for payment. Status: {order.get_status_display()}"},
                            status=status.HTTP_400_BAD_REQUEST)
        if order.total_amount <= 0:
            return Response({"error": "Order total is zero or less. Payment not required via gateway."},
                            status=status.HTTP_400_BAD_REQUEST)

        unavailable_items = []
        for order_item in order.items.all():
            item_object = order_item.content_object
            if item_object and not _has_capacity(item_object):
                unavailable_items.append(order_item.description)

        if unavailable_items:
            order.status = Order.STATUS_CANCELLED
            order.save()
            _release_reservations_for_orders(order)
            return Response(
                {"error": f"Some items are no longer available due to capacity limits: {', '.join(unavailable_items)}. Your order has been cancelled."},
                status=status.HTTP_400_BAD_REQUEST
            )

        already_fulfilled = False
        for oi in order.items.select_related('content_type'):
            obj = oi.content_object
            if isinstance(obj, Presentation):
                if PresentationEnrollment.objects.filter(
                        user=order.user, presentation=obj,
                        status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE
                ).exists():
                    already_fulfilled = True
                    break
            elif isinstance(obj, SoloCompetition):
                if SoloCompetitionRegistration.objects.filter(
                        user=order.user, solo_competition=obj,
                        status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE
                ).exists():
                    already_fulfilled = True
                    break
            elif isinstance(obj, CompetitionTeam):
                if (obj.leader_id == order.user_id and
                        obj.status == CompetitionTeam.STATUS_ACTIVE):
                    already_fulfilled = True
                    break

        if already_fulfilled:
            return Response(
                {"error": "This order contains item(s) already owned. Payment is blocked."},
                status=status.HTTP_400_BAD_REQUEST
            )

        inactive_order_items = []
        for oi in order.items.select_related('content_type'):
            if not _is_content_available(getattr(oi, "content_object", None)):
                inactive_order_items.append(oi.id)

        if inactive_order_items:
            order.status = Order.STATUS_CANCELLED
            order.save(update_fields=["status"])
            _release_reservations_for_orders(order)
            return Response(
                {
                    "success": False,
                    "statusCode": 400,
                    "message": "This order contains item(s) no longer available.",
                    "errors": {"order_item_ids": inactive_order_items},
                    "data": {},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if app_slug and order.redirect_app != app_slug:
            order.redirect_app = app_slug
            order.save(update_fields=["redirect_app"])

        zarrinpal_client = ZarrinPal()
        if not zarrinpal_client.CALLBACK_URL:
            logger.error("Zarinpal PAYMENT_CALLBACK_URL in settings is not a full URL or is a placeholder.")
            return Response({"error": "Payment callback URL misconfiguration."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        payment_result = zarrinpal_client.create_payment(
            amount=float(order.total_amount),
            mobile=order.user.phone_number or "",
            email=order.user.email or "",
            order_id=order.order_id
        )

        if payment_result.get('status') == 'success':
            order.payment_gateway_authority = payment_result.get('authority')
            order.status = Order.STATUS_AWAITING_GATEWAY_REDIRECT
            order.save()
            return Response({"payment_url": payment_result.get('link'), "authority": payment_result.get('authority')},
                            status=status.HTTP_200_OK)
        else:
            error_msg = payment_result.get('error', 'Unknown payment gateway error during initiation.')
            logger.error(f"ZarrinPal create_payment failed for order {order.order_id}: {error_msg}")
            order.status = Order.STATUS_PAYMENT_FAILED
            order.save()
            return Response({"error": f"Payment gateway error: {error_msg}"}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Shop - Orders & Payment'],
    summary="Handles Zarinpal callback after payment attempt",
    description="This endpoint does not return JSON. It redirects the user back to the frontend.",
    responses={302: None},
)
class PaymentCallbackView(views.APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        authority = request.GET.get('Authority')
        status_param = request.GET.get('Status')

        frontend_url = getattr(settings, 'FRONTEND_URL', '')
        callback_path = "/payment/callback/"

        if not authority:
            logger.warning("Zarinpal callback: Authority missing.")
            params = urlencode({'success': 'false', 'message': 'Invalid callback parameters'})
            return redirect(f"{frontend_url}{callback_path}?{params}")

        order = get_object_or_404(Order, payment_gateway_authority=authority)

        z = ZarrinPal()

        def _finalize_single_order(order):
            with transaction.atomic():
                order.status = Order.STATUS_PROCESSING_ENROLLMENT
                order.save(update_fields=["status"])
                OrderCheckoutView()._process_successful_order(order)

        if status_param == "OK":
            vr = z.verify_payment(authority=authority, amount=order.total_amount)
            if vr.get('status') == 'success':
                with transaction.atomic():
                    order.status = Order.STATUS_PROCESSING_ENROLLMENT
                    order.payment_gateway_txn_id = vr.get('ref_id')
                    order.paid_at = timezone.now()
                    order.save()
                    _finalize_single_order(order)
                params = urlencode({'success': 'true', 'message': 'Payment successful', 'order_id': order.order_id})
                return redirect(f"{frontend_url}{callback_path}?{params}")
            else:
                order.status = Order.STATUS_PAYMENT_FAILED
                order.save()
                _release_reservations_for_orders(order)
                params = urlencode({'success': 'false', 'message': 'Payment verification failed', 'order_id': order.order_id})
                return redirect(f"{frontend_url}{callback_path}?{params}")
        else:
            order.status = Order.STATUS_PAYMENT_FAILED
            order.save()
            _release_reservations_for_orders(order)
            params = urlencode({'success': 'false', 'message': 'Payment cancelled or failed', 'order_id': order.order_id})
            return redirect(f"{frontend_url}{callback_path}?{params}")



@extend_schema(tags=['Shop - Orders & Payment'])
@extend_schema_view(
    list=extend_schema(
        summary="List user's order history",
        responses={200: get_paginated_response_serializer(OrderListSerializer)},
        parameters=[
            OpenApiParameter(name='event', description='Filter orders by event ID', required=False, type=int),
        ]
    ),
    retrieve=extend_schema(
        summary="Retrieve a single order by its UUID",
        responses={
            200: get_api_response_serializer(OrderSerializer),
            404: ApiErrorResponseSerializer
        }
    )
)
class OrderHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    lookup_field = 'order_id'

    def get_queryset(self):
        queryset = Order.objects.filter(user=self.request.user)
        event_param = self.request.query_params.get("event")
        if event_param:
            try:
                queryset = queryset.filter(event_id=int(event_param))
            except (TypeError, ValueError):
                queryset = queryset.filter(event_id__isnull=True)
        else:
            queryset = queryset.filter(event_id__isnull=True)

        return queryset.order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'list':
            return OrderListSerializer
        return OrderSerializer


@extend_schema(
    tags=['Shop - Orders & Payment'],
    summary="List all purchases of the current user (presentations, solo competitions, teams, products). Optionally filter by event.",
    parameters=[
        OpenApiParameter(
            name="event",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Event ID to filter purchases by. If omitted, returns all purchases."
        ),
    ],
    responses={200: UserPurchasesSerializer(many=True)}
)
class UserPurchasesView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user

        raw_event = request.query_params.get("event")
        try:
            event_id = int(raw_event) if raw_event not in (None, "", "null") else None
        except (TypeError, ValueError):
            event_id = None

        response_data = {
            'presentations': [],
            'solo_competitions': [],
            'competition_teams': [],
            'products': [],
        }

        pres_qs = PresentationEnrollment.objects.filter(user=user,
                                                        status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).select_related(
            "presentation__event", "user"
        )
        if event_id:
            pres_qs = pres_qs.filter(presentation__event_id=event_id)

        response_data['presentations'] = [en.presentation for en in pres_qs if en.presentation]

        solo_qs = SoloCompetitionRegistration.objects.filter(user=user,
                                                             status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE).select_related(
            "solo_competition__event", "user"
        )
        if event_id:
            solo_qs = solo_qs.filter(solo_competition__event_id=event_id)

        response_data['solo_competitions'] = [reg.solo_competition for reg in solo_qs if reg.solo_competition]

        team_ids = set()
        lead_qs = CompetitionTeam.objects.filter(leader=user, status=CompetitionTeam.STATUS_ACTIVE).select_related(
            "group_competition__event", "leader"
        )
        if event_id:
            lead_qs = lead_qs.filter(group_competition__event_id=event_id)

        for team in lead_qs:
            team_ids.add(team.id)
            response_data['competition_teams'].append(team)

        mem_qs = TeamMembership.objects.filter(user=user).select_related(
            "team__group_competition__event", "team__leader"
        )
        if event_id:
            mem_qs = mem_qs.filter(team__group_competition__event_id=event_id)

        for m in mem_qs:
            team = m.team
            if team and team.id not in team_ids and team.status == CompetitionTeam.STATUS_ACTIVE:
                team_ids.add(team.id)
                response_data['competition_teams'].append(team)

        product_orders = Order.objects.filter(user=user, status=Order.STATUS_COMPLETED,
                                              items__content_type=ContentType.objects.get_for_model(Product))
        if event_id:
            product_orders = product_orders.filter(event_id=event_id)
        else:
            product_orders = product_orders.filter(event_id__isnull=True)

        for order in product_orders:
            for item in order.items.filter(content_type=ContentType.objects.get_for_model(Product)):
                response_data['products'].append(item.content_object)

        ser = UserPurchasesSerializer(response_data, context={"request": request})
        return Response(ser.data, status=status.HTTP_200_OK)


@extend_schema(tags=['Shop - Orders & Payment'])
class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = Product.objects.filter(is_active=True)
        event_param = self.request.query_params.get("event")
        if event_param:
            try:
                queryset = queryset.filter(event_id=int(event_param))
            except (TypeError, ValueError):
                queryset = queryset.filter(event_id__isnull=True)
        else:
            queryset = queryset.filter(event_id__isnull=True)
        return queryset

    @extend_schema(
        summary="List all available products",
        responses={200: get_paginated_response_serializer(ProductSerializer)},
        parameters=[
            OpenApiParameter(name='event', description='Filter products by event ID', required=False, type=int),
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)