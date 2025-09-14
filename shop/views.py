from django.db.models import Sum
from django.shortcuts import redirect, get_object_or_404
from django.conf import settings
from django.utils import timezone
from django.db import transaction, models
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
from urllib.parse import quote
import logging
from rest_framework import viewsets, status, generics, views
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from decimal import Decimal
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer, get_paginated_response_serializer
from .models import DiscountCode, Cart, CartItem, Order, OrderItem, PaymentBatch, DiscountRedemption, PaymentApp
from .serializers import (
    CartSerializer, AddToCartSerializer, ApplyDiscountSerializer,
    OrderSerializer, OrderListSerializer, CartItemSerializer, PaymentInitiateResponseSerializer,
    PartialCheckoutSerializer, BatchPaymentInitiateSerializer, RegisteredThingSerializer, OrderPaymentInitiateSerializer
)
from .payments import ZarrinPal


Presentation = apps.get_model('events', 'Presentation')
SoloCompetition = apps.get_model('events', 'SoloCompetition')
CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
PresentationEnrollment = apps.get_model('events', 'PresentationEnrollment')
SoloCompetitionRegistration = apps.get_model('events', 'SoloCompetitionRegistration')
TeamMembership = apps.get_model('events', 'TeamMembership')
CustomUser = apps.get_model(settings.AUTH_USER_MODEL)
CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
logger = logging.getLogger(__name__)


def _release_reservations_for_orders(order_qs_or_list):
    CartItem = apps.get_model('shop', 'CartItem')
    CompetitionTeam = apps.get_model('events', 'CompetitionTeam')

    orders = order_qs_or_list if hasattr(order_qs_or_list, '__iter__') else [order_qs_or_list]
    with transaction.atomic():
        for order in orders:
            for ci in CartItem.objects.select_related('content_type').filter(reserved_order=order):
                obj = ci.content_object
                if isinstance(obj, CompetitionTeam) and \
                   obj.status == CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION:
                    if obj.group_competition.requires_admin_approval:
                        obj.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
                    else:
                        obj.status = CompetitionTeam.STATUS_CANCELLED
                    obj.save(update_fields=["status"])

                ci.status = CartItem.STATUS_OWNED
                ci.reserved_order = None
                ci.reserved_order_item = None
                ci.save(update_fields=['status', 'reserved_order', 'reserved_order_item'])


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

    def post(self, request, order_pk, *args, **kwargs):
        order = get_object_or_404(Order, pk=order_pk, user=request.user)

        cancellable_statuses = {
            Order.STATUS_PENDING_PAYMENT,
            Order.STATUS_PAYMENT_FAILED,
        }
        if order.status not in cancellable_statuses:
            return Response(
                {"error": f"Order cannot be cancelled in status '{order.get_status_display()}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if order.batches.filter(
            status__in=[
                PaymentBatch.STATUS_AWAITING_GATEWAY_REDIRECT,
                PaymentBatch.STATUS_VERIFIED,
                PaymentBatch.STATUS_COMPLETED,
            ]
        ).exists():
            return Response(
                {"error": "This order is attached to a batch that is in progress or paid. "
                          "Please wait or request a refund/cancellation through the batch flow."},
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
                pass
        return cart

    @extend_schema(
        summary="View user's shopping cart",
        request=None,
        responses={200: get_api_response_serializer(CartSerializer)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


@extend_schema(
    tags=['Shop - Cart'],
    summary="Add item to cart",
    request=AddToCartSerializer,
    responses={
        200: get_api_response_serializer(CartSerializer),
        201: get_api_response_serializer(CartSerializer),
        400: ApiErrorResponseSerializer,
        403: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    },
)
class AddToCartView(views.APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AddToCartSerializer

    def resolve_event_id(self, obj):
        ev_id = getattr(obj, 'event_id', None)
        if ev_id:
            print(f"[resolve_event_id] {obj.__class__.__name__}#{obj.pk} -> event_id={ev_id}")
            return ev_id
        parent = getattr(obj, 'group_competition', None)
        parent_ev = getattr(parent, 'event_id', None) if parent else None
        return parent_ev

    def post(self, request, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(user=request.user)
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        item_type_str = serializer.validated_data['item_type']
        item_id = serializer.validated_data['item_id']

        item_model_map = {
            'presentation': Presentation,
            'solocompetition': SoloCompetition,
            'competitionteam': CompetitionTeam,
        }
        item_model = item_model_map.get(item_type_str)
        if not item_model:
            return Response({"error": "Invalid item type."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            content_object = item_model.objects.get(pk=item_id)
            if not _is_content_available(content_object):
                return Response({"error": "This item is not available anymore."}, status=400)
        except item_model.DoesNotExist:
            return Response({"error": f"{item_type_str.capitalize()} not found."}, status=status.HTTP_404_NOT_FOUND)

        is_item_actually_paid = False
        if hasattr(content_object, 'is_paid') and content_object.is_paid:
            price_attr = getattr(content_object, 'price', getattr(content_object, 'price_per_participant', None))
            if price_attr is not None and price_attr > 0:
                is_item_actually_paid = True
        elif isinstance(content_object, CompetitionTeam):
            parent_comp = content_object.group_competition
            if parent_comp.is_paid and parent_comp.price_per_group is not None and parent_comp.price_per_group > 0:
                is_item_actually_paid = True

        if not is_item_actually_paid:
            return Response({"error": "This item is free or has no price. Use direct enrollment/registration."},
                            status=status.HTTP_400_BAD_REQUEST)

        if isinstance(content_object, CompetitionTeam):
            if content_object.leader != request.user:
                return Response({"error": "You are not the leader of this team."}, status=status.HTTP_403_FORBIDDEN)
            if content_object.group_competition.requires_admin_approval and \
               content_object.status != CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT:
                return Response({"error": "This team must be admin-approved and awaiting payment."},
                                status=status.HTTP_400_BAD_REQUEST)

        content_type = ContentType.objects.get_for_model(content_object)

        pending_statuses = [
            Order.STATUS_PENDING_PAYMENT,
            Order.STATUS_AWAITING_GATEWAY_REDIRECT,
            Order.STATUS_PAYMENT_FAILED,
        ]
        exists_unpaid = OrderItem.objects.filter(
            content_type=content_type,
            object_id=content_object.pk,
            order__user=request.user,
            order__status__in=pending_statuses,
        ).exists()
        if exists_unpaid:
            return Response({"error": "You already have an unpaid order for this item."},
                            status=status.HTTP_400_BAD_REQUEST)

        event_id = self.resolve_event_id(content_object)


        defaults = {}
        if event_id:
            defaults['event_id'] = event_id
        
        existing_ci = CartItem.objects.filter(
            cart=cart, content_type=content_type, object_id=content_object.pk
        ).first()
        if existing_ci:
            if existing_ci.status == CartItem.STATUS_RESERVED and existing_ci.reserved_order and \
            existing_ci.reserved_order.status in [Order.STATUS_PENDING_PAYMENT, Order.STATUS_AWAITING_GATEWAY_REDIRECT, Order.STATUS_PAYMENT_FAILED]:
                return Response({"error": "This item is already reserved by an unpaid order. Please complete or retry payment from Orders."},
                                status=400)
            return Response(CartSerializer(cart).data, status=200)

        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            content_type=content_type,
            object_id=content_object.pk,
            defaults=defaults
        )

        if (cart_item.event_id is None and event_id) or (event_id and cart_item.event_id != event_id):
            cart_item.event_id = event_id
            cart_item.save(update_fields=['event'])

        if created and isinstance(content_object, CompetitionTeam):
            content_object.status = CompetitionTeam.STATUS_IN_CART
            content_object.save(update_fields=['status'])

        return Response(
            CartSerializer(cart).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


@extend_schema(
    tags=['Shop - Orders & Payment'],
    summary='Partial checkout (create one Order per selected cart item)',
    description=(
        "Creates **one Order per selected** `CartItem` and removes those items from the cart.\n\n"
        "- Input is a list of `cart_item_ids` belonging to the current user.\n"
        "- Only payable items are converted to orders. Free items are skipped.\n"
        "- Teams moved to checkout are set to *awaiting payment confirmation*.\n"
        "- Returns an **array** of the newly created orders (not paginated).\n"
    ),
    request=PartialCheckoutSerializer,
    responses={
        201: OrderListSerializer(many=True),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    },
)
class OrderPartialCheckoutView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        ser = PartialCheckoutSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=400)
        ids = ser.validated_data['cart_item_ids']

        cart = Cart.objects.filter(user=request.user).prefetch_related('items__content_object').first()
        if not cart:
            return Response({"error": "Cart not found."}, status=404)

        created_orders = []
        with transaction.atomic():
            items = list(
                cart.items
                    .filter(id__in=ids)
                    .select_related('content_type')
                    .select_for_update()
            )
            if not items:
                return Response({"error": "No matching cart items."}, status=400)

            for ci in items:
                if ci.status == CartItem.STATUS_RESERVED and ci.reserved_order_id:
                    continue

                if not _is_cart_item_active(ci):
                    return Response(
                        {
                            "success": False,
                            "statusCode": 400,
                            "message": "Some selected items are no longer available.",
                            "errors": {"inactive_cart_item_ids": [ci.id]},
                            "data": {},
                        },
                        status=400,
                    )

                price = CartItemSerializer().get_price(ci)
                if price is None or price <= 0:
                    continue

                discount_code = cart.applied_discount_code
                discount_amount = 0
                if discount_code and discount_code.is_valid(price):
                    if (discount_code.event_id is None) or (discount_code.event_id == ci.event_id):
                        discount_amount = min(
                            discount_code.calculate_discount(price),
                            price
                        )

                order = Order.objects.create(
                    user=request.user,
                    subtotal_amount=price,
                    discount_code_applied=discount_code if discount_amount > 0 else None,
                    discount_amount=discount_amount,
                    total_amount=(price - discount_amount),
                    status=Order.STATUS_PENDING_PAYMENT,
                )
                order_item = OrderItem.objects.create(
                    order=order,
                    content_type=ci.content_type,
                    object_id=ci.object_id,
                    description=str(ci.content_object),
                    price=price,
                )

                ci.status = CartItem.STATUS_RESERVED
                ci.reserved_order = order
                ci.reserved_order_item = order_item
                ci.save(update_fields=['status', 'reserved_order', 'reserved_order_item'])

                if isinstance(ci.content_object, CompetitionTeam):
                    team = ci.content_object
                    team.status = CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION
                    team.save(update_fields=['status'])

                created_orders.append(order)

            if not created_orders:
                return Response({"error": "No payable items."}, status=400)

        return Response(OrderListSerializer(created_orders, many=True).data, status=201)

@extend_schema(
    tags=['Shop - Cart'],
    summary="Remove item from cart",
    responses={
        200: get_api_response_serializer(CartSerializer),
        404: ApiErrorResponseSerializer,
    },
)
class RemoveCartItemView(views.APIView):
    permission_classes = [IsAuthenticated]

    def _batch_locked_statuses(self):
        return [
            PaymentBatch.STATUS_PENDING,
            PaymentBatch.STATUS_AWAITING_GATEWAY_REDIRECT,
        ]

    def _batch_is_potentially_paid(self, batch: PaymentBatch) -> bool:
        if not batch.payment_gateway_authority:
            return False
        try:
            zp = ZarrinPal()
            unverified = set(zp.get_unverified_authorities())
            return batch.payment_gateway_authority in unverified
        except Exception:
            return True

    def _detach_order_from_unpaid_batch(self, batch: PaymentBatch, order: Order):
        with transaction.atomic():
            batch.orders.remove(order)
            remaining = batch.orders.all().only('total_amount')
            new_total = sum((o.total_amount for o in remaining), start=Decimal('0.00'))

            if remaining.exists():
                batch.total_amount = new_total
                batch.payment_gateway_authority = None
                batch.payment_gateway_txn_id = None
                # batch.status = PaymentBatch.STATUS_PAYMENT_FAILED
                batch.status = PaymentBatch.STATUS_PENDING
                batch.save(update_fields=[
                    'total_amount', 'payment_gateway_authority',
                    'payment_gateway_txn_id', 'status'
                ])
            else:
                batch.delete()

    def _terminal_batch_statuses(self):
        return [
            PaymentBatch.STATUS_PAYMENT_FAILED,
            PaymentBatch.STATUS_VERIFIED,
            PaymentBatch.STATUS_COMPLETED,
        ]

    def delete(self, request, cart_item_pk, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(user=request.user)
        try:
            cart_item = (
                CartItem.objects
                .select_related('reserved_order', 'reserved_order_item', 'content_type')
                .get(pk=cart_item_pk, cart=cart)
            )
        except CartItem.DoesNotExist:
            return Response({"error": "Cart item not found."}, status=status.HTTP_404_NOT_FOUND)

        content_object = cart_item.content_object
        reserved_order = cart_item.reserved_order

        if reserved_order:
            batches = list(reserved_order.batches.select_for_update())

            if any(b.status in self._terminal_batch_statuses() for b in batches):
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

            for batch in batches:
                if batch.status in self._batch_locked_statuses():
                    if self._batch_is_potentially_paid(batch):
                        return Response(
                            {"error": "This batch payment looks submitted but not confirmed yet. "
                                    "Please try again after verification (or cancel the batch)."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    self._detach_order_from_unpaid_batch(batch, reserved_order)

            with transaction.atomic():
                if reserved_order.items.exists():
                    for oi in reserved_order.items.select_related().all():
                        obj = oi.content_object
                        if isinstance(obj, CompetitionTeam) and \
                        obj.status == CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION:
                            if obj.group_competition.requires_admin_approval:
                                obj.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
                            else:
                                obj.status = CompetitionTeam.STATUS_CANCELLED
                            obj.save(update_fields=["status"])
                reserved_order.delete()

        if cart_item.pk:
            cart_item.delete()

        if isinstance(content_object, CompetitionTeam) and \
        content_object.status == CompetitionTeam.STATUS_IN_CART:
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
    summary = "Remove discount code from cart",
    responses = {200: get_api_response_serializer(CartSerializer)}
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

            CartItem.objects.filter(
                reserved_order=order
            ).delete()

        logger.info(f"Finished processing order: {order.order_id}")

    def post(self, request, *args, **kwargs):
        cart = (
            Cart.objects
            .filter(user=request.user)
            .prefetch_related('items__content_object')
            .first()
        )
        if not cart or not cart.items.exists():
            return Response({"error": "Your cart is empty."}, status=status.HTTP_400_BAD_REQUEST)

        inactive_items = []
        for ci in cart.items.select_related('content_type'):
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

        subtotal = cart.get_subtotal()
        discount_amount = cart.get_discount_amount()
        total_amount = cart.get_total()

        if total_amount < 0:
            return Response({"error": "Order total cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

        if total_amount == 0:
            with transaction.atomic():
                order = Order.objects.create(
                    user=request.user, subtotal_amount=subtotal,
                    discount_code_applied=cart.applied_discount_code, discount_amount=discount_amount,
                    total_amount=total_amount, status=Order.STATUS_PROCESSING_ENROLLMENT,
                    paid_at=timezone.now()
                )
                for cart_item in cart.items.all():
                    item_price = CartItemSerializer().get_price(cart_item)
                    OrderItem.objects.create(
                        order=order, content_type=cart_item.content_type,
                        object_id=cart_item.object_id, description=str(cart_item.content_object),
                        price=item_price
                    )
                    if isinstance(cart_item.content_object, CompetitionTeam):
                        team = cart_item.content_object
                        team.status = CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION
                        team.save()

                self._process_successful_order(order)

                cart.items.all().delete()
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
                status=Order.STATUS_PENDING_PAYMENT
            )
            for cart_item in cart.items.select_related('content_type').select_for_update():
                item_price = CartItemSerializer().get_price(cart_item)
                order_item = OrderItem.objects.create(
                    order=order,
                    content_type=cart_item.content_type,
                    object_id=cart_item.object_id,
                    description=str(cart_item.content_object),
                    price=item_price
                )

                cart_item.status = CartItem.STATUS_RESERVED
                cart_item.reserved_order = order
                cart_item.reserved_order_item = order_item
                cart_item.save(update_fields=['status', 'reserved_order', 'reserved_order_item'])

                if isinstance(cart_item.content_object, CompetitionTeam):
                    team = cart_item.content_object
                    team.status = CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION
                    team.save(update_fields=['status'])

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

    def post(self, request, order_pk, *args, **kwargs):
        ser = OrderPaymentInitiateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        app_slug = (ser.validated_data.get("app") or "").strip().lower() or None

        order = get_object_or_404(Order, pk=order_pk, user=request.user)

        if order.status not in [Order.STATUS_PENDING_PAYMENT, Order.STATUS_PAYMENT_FAILED]:
            return Response({"error": f"Order not eligible for payment. Status: {order.get_status_display()}"},
                            status=status.HTTP_400_BAD_REQUEST)
        if order.total_amount <= 0:
            return Response({"error": "Order total is zero or less. Payment not required via gateway."},
                            status=status.HTTP_400_BAD_REQUEST)
        
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
        print(f"[OrderPaymentInitiate] Using BASE = {zarrinpal_client.BASE}")
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

def _append_app_if_valid(url: str, app_slug: str | None) -> str:
    if app_slug and PaymentApp.objects.filter(slug=app_slug, is_active=True).exists():
        sep = '&' if ('?' in url) else '?'
        return f"{url}{sep}app={quote(app_slug)}"
    return url

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

        frontend_base_url = getattr(settings, 'FRONTEND_BASE_URL', '')
        default_failure_path = getattr(settings, 'FRONTEND_PAYMENT_FAILURE_PATH', '/payment-failed')
        default_success_path = getattr(settings, 'FRONTEND_PAYMENT_SUCCESS_PATH', '/payment-success')

        if not authority:
            logger.warning("Zarinpal callback: Authority missing.")
            return redirect(f"{frontend_base_url}{default_failure_path}?error=invalid_callback_params")

        order_qs = Order.objects.filter(payment_gateway_authority=authority)
        batch = PaymentBatch.objects.filter(payment_gateway_authority=authority).first()

        if not order_qs.exists() and not batch:
            logger.error(f"Zarinpal callback: No order/batch found for authority {authority}.")
            return redirect(f"{frontend_base_url}{default_failure_path}?error=order_not_found")

        z = ZarrinPal()

        def _finalize_single_order(order):
            with transaction.atomic():
                order.status = Order.STATUS_PROCESSING_ENROLLMENT
                order.save(update_fields=["status"])
                OrderCheckoutView()._process_successful_order(order)

        if batch:
            success_url = f"{frontend_base_url}{default_success_path}?batch_id={batch.batch_id}"
            failure_url = f"{frontend_base_url}{default_failure_path}?batch_id={batch.batch_id}"
            batch_app = (batch.redirect_app or "").strip().lower() if batch else None

            if batch.status == PaymentBatch.STATUS_COMPLETED:
                return redirect(_append_app_if_valid(success_url, batch_app))
            if batch.status not in [PaymentBatch.STATUS_AWAITING_GATEWAY_REDIRECT, PaymentBatch.STATUS_PAYMENT_FAILED]:
                return redirect(_append_app_if_valid(f"{failure_url}&reason=invalid_batch_state", batch_app))

            if status_param == "OK":
                vr = z.verify_payment(authority=authority, amount=batch.total_amount)
                if vr.get("status") == "success":
                    with transaction.atomic():
                        batch.status = PaymentBatch.STATUS_VERIFIED
                        batch.payment_gateway_txn_id = vr.get("ref_id")
                        batch.paid_at = timezone.now()
                        batch.save(update_fields=["status", "payment_gateway_txn_id", "paid_at"])

                        member_orders = list(batch.orders.select_related().all())
                        for o in member_orders:
                            o.payment_gateway_txn_id = vr.get("ref_id")
                            o.paid_at = batch.paid_at
                            o.save(update_fields=["payment_gateway_txn_id", "paid_at"])
                            _finalize_single_order(o)

                        batch.status = PaymentBatch.STATUS_COMPLETED
                        batch.save(update_fields=["status"])
                    return redirect(_append_app_if_valid(success_url, batch_app))
                else:
                    err = vr.get('error', 'verify_failed')
                    batch.status = PaymentBatch.STATUS_PAYMENT_FAILED
                    batch.save(update_fields=["status"])
                    batch.orders.update(status=Order.STATUS_PAYMENT_FAILED)
                    _release_reservations_for_orders(batch.orders.all())
                    return redirect(_append_app_if_valid(f"{failure_url}&reason=verify_failed&code={err}", batch_app))
            else:
                batch.status = PaymentBatch.STATUS_PAYMENT_FAILED
                batch.save(update_fields=["status"])
                batch.orders.update(status=Order.STATUS_PAYMENT_FAILED)
                _release_reservations_for_orders(batch.orders.all())
                return redirect(_append_app_if_valid(f"{failure_url}&reason=user_cancelled_or_gateway_nok", batch_app))

        order = order_qs.first()
        success_url = f"{frontend_base_url}{default_success_path}?order_id={order.order_id}"
        failure_url = f"{frontend_base_url}{default_failure_path}?order_id={order.order_id}"
        order_app = (order.redirect_app or "").strip().lower()

        if order.status == Order.STATUS_COMPLETED:
            return redirect(_append_app_if_valid(success_url, order_app))
        if order.status not in [Order.STATUS_AWAITING_GATEWAY_REDIRECT, Order.STATUS_PAYMENT_FAILED, Order.STATUS_PENDING_PAYMENT]:
            return redirect(_append_app_if_valid(f"{failure_url}&reason=invalid_order_state", order_app))

        if status_param == "OK":
            vr = z.verify_payment(authority=authority, amount=order.total_amount)
            if vr.get('status') == 'success':
                with transaction.atomic():
                    order.status = Order.STATUS_PROCESSING_ENROLLMENT
                    order.payment_gateway_txn_id = vr.get('ref_id')
                    order.paid_at = timezone.now()
                    order.save()
                    OrderCheckoutView()._process_successful_order(order)
                return redirect(_append_app_if_valid(success_url, order_app))
            else:
                order.status = Order.STATUS_PAYMENT_FAILED
                order.save()
                _release_reservations_for_orders(order)
                return redirect(_append_app_if_valid(f"{failure_url}&reason=verify_failed", order_app))
        else:
            order.status = Order.STATUS_PAYMENT_FAILED
            order.save()
            _release_reservations_for_orders(order)
            return redirect(_append_app_if_valid(f"{failure_url}&reason=user_cancelled_or_gateway_nok", order_app))


@extend_schema(tags=['Shop - Orders & Payment'])
@extend_schema_view(
    list=extend_schema(
        summary="List user's order history",
        responses={200: get_paginated_response_serializer(OrderListSerializer)}
    ),
    retrieve=extend_schema(
        summary="Retrieve a single order",
        responses={
            200: get_api_response_serializer(OrderSerializer),
            404: ApiErrorResponseSerializer
        }
    )
)
class OrderHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'list':
            return OrderListSerializer
        return OrderSerializer

@extend_schema(
    tags=['Shop - Orders & Payment'],
    summary="Initiate ONE Zarinpal payment for MANY pending orders",
    request=BatchPaymentInitiateSerializer,
    responses={
        200: get_api_response_serializer(PaymentInitiateResponseSerializer),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
        500: ApiErrorResponseSerializer,
    },
)
class BatchPaymentInitiateView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        ser = BatchPaymentInitiateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        app_slug = (ser.validated_data.get("app") or "").strip().lower() or None

        ser = BatchPaymentInitiateSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=400)

        ids = ser.validated_data['order_ids']
        qs = Order.objects.filter(pk__in=ids, user=request.user)
        if qs.count() != len(ids):
            return Response({"error": "Some orders not found or not yours."}, status=404)

        allowed_status = [Order.STATUS_PENDING_PAYMENT, Order.STATUS_PAYMENT_FAILED]
        not_allowed = qs.exclude(status__in=allowed_status).values_list('id', flat=True)
        if not_allowed:
            return Response({"error": f"Orders not eligible for payment: {list(not_allowed)}"}, status=400)

        inactive = []
        for o in qs.prefetch_related('items__content_object'):
            for oi in o.items.all():
                if not _is_content_available(getattr(oi, "content_object", None)):
                    inactive.append({"order_id": o.id, "order_item_id": oi.id, "object_id": oi.object_id})
        if inactive:
            return Response(
                {"error": "Some orders contain items that are no longer available.", "inactive": inactive},
                status=400,
            )

        for o in qs.prefetch_related('items__content_type'):
            for oi in o.items.all():
                obj = oi.content_object
                owned = False
                if isinstance(obj, Presentation):
                    owned = PresentationEnrollment.objects.filter(
                        user=o.user, presentation=obj,
                        status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE
                    ).exists()
                elif isinstance(obj, SoloCompetition):
                    owned = SoloCompetitionRegistration.objects.filter(
                        user=o.user, solo_competition=obj,
                        status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE
                    ).exists()
                elif isinstance(obj, CompetitionTeam):
                    owned = (obj.leader_id == o.user_id and obj.status == CompetitionTeam.STATUS_ACTIVE)
                if owned:
                    return Response(
                        {"error": f"Order {o.id} contains item(s) already owned. Batch payment blocked."},
                        status=400
                    )

        total = qs.aggregate(t=Sum('total_amount'))['t'] or Decimal('0.00')
        if total <= 0:
            return Response({"error": "Combined amount is zero or less."}, status=400)

        batch = PaymentBatch.objects.create(
            user=request.user,
            total_amount=total,
            status=PaymentBatch.STATUS_PENDING,
            redirect_app=app_slug or None,
        )
        batch.orders.add(*list(qs))

        z = ZarrinPal()
        if not z.CALLBACK_URL:
            logger.error("Zarinpal PAYMENT_CALLBACK_URL misconfigured.")
            return Response({"error": "Payment callback URL misconfiguration."}, status=500)

        res = z.create_payment(
            amount=float(total),
            mobile=request.user.phone_number or "",
            email=request.user.email or "",
            order_id=batch.batch_id,
        )

        if res.get("status") == "success":
            batch.payment_gateway_authority = res.get("authority")
            batch.status = PaymentBatch.STATUS_AWAITING_GATEWAY_REDIRECT
            batch.save(update_fields=["payment_gateway_authority", "status"])

            qs.update(status=Order.STATUS_AWAITING_GATEWAY_REDIRECT)
            return Response(
                {"payment_url": res.get("link"), "authority": res.get("authority")},
                status=200
            )

        msg = res.get("error", "Payment gateway error")
        batch.status = PaymentBatch.STATUS_PAYMENT_FAILED
        batch.save(update_fields=["status"])
        qs.update(status=Order.STATUS_PAYMENT_FAILED)
        _release_reservations_for_orders(qs)
        return Response({"error": f"Payment gateway error: {msg}"}, status=400)
    

@extend_schema(
    tags=['Shop - Registrations'],
    summary="List all registrations of the current user (presentations, solo competitions, teams). Optionally filter by event.",
    parameters=[
        OpenApiParameter(
            name="event",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Event ID to filter registrations by. If omitted, returns all registrations."
        ),
    ],
    responses={200: RegisteredThingSerializer(many=True)} 
)
class UserRegistrationsView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user

        raw_event = request.query_params.get("event")
        try:
            event_id = int(raw_event) if raw_event not in (None, "", "null") else None
        except (TypeError, ValueError):
            event_id = None

        items = []

        pres_qs = PresentationEnrollment.objects.filter(user=user).select_related(
            "presentation__event", "user"
        )
        if event_id:
            pres_qs = pres_qs.filter(presentation__event_id=event_id)

        for en in pres_qs:
            if en.presentation:
                items.append({
                    "item_type": "presentation",
                    "status": en.status,
                    "role": None,
                    "item_details": en.presentation,
                })

        solo_qs = SoloCompetitionRegistration.objects.filter(user=user).select_related(
            "solo_competition__event", "user"
        )
        if event_id:
            solo_qs = solo_qs.filter(solo_competition__event_id=event_id)

        for reg in solo_qs:
            if reg.solo_competition:
                items.append({
                    "item_type": "solo_competition",
                    "status": reg.status,
                    "role": None,
                    "item_details": reg.solo_competition,
                })

        team_ids = set()
        lead_qs = CompetitionTeam.objects.filter(leader=user).select_related(
            "group_competition__event", "leader"
        )
        if event_id:
            lead_qs = lead_qs.filter(group_competition__event_id=event_id)

        for team in lead_qs:
            team_ids.add(team.id)
            items.append({
                "item_type": "competition_team",
                "status": team.status,
                "role": "leader",
                "item_details": team,
            })

        mem_qs = TeamMembership.objects.filter(user=user).select_related(
            "team__group_competition__event", "team__leader"
        )
        if event_id:
            mem_qs = mem_qs.filter(team__group_competition__event_id=event_id)

        for m in mem_qs:
            team = m.team
            if team and team.id not in team_ids:
                team_ids.add(team.id)
                items.append({
                    "item_type": "competition_team",
                    "status": team.status,
                    "role": "member",
                    "item_details": team,
                })

        ser = RegisteredThingSerializer(items, many=True, context={"request": request})
        return Response(ser.data, status=status.HTTP_200_OK)