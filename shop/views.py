from django.shortcuts import redirect, get_object_or_404
from django.conf import settings
from django.utils import timezone
from django.db import transaction, models
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
import logging
from rest_framework import viewsets, status, generics, views
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from drf_spectacular.utils import extend_schema, extend_schema_view
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer, NoPaginationAutoSchema, \
    get_paginated_response_serializer
from .models import DiscountCode, Cart, CartItem, Order, OrderItem, PaymentBatch
from .serializers import (
    CartSerializer, AddToCartSerializer, ApplyDiscountSerializer,
    OrderSerializer, OrderListSerializer, CartItemSerializer, PaymentInitiateResponseSerializer, PartialCheckoutSerializer, BatchPaymentInitiateSerializer
)
from .payments import ZarrinPal

Presentation = apps.get_model('events', 'Presentation')
SoloCompetition = apps.get_model('events', 'SoloCompetition')
CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
PresentationEnrollment = apps.get_model('events', 'PresentationEnrollment')
SoloCompetitionRegistration = apps.get_model('events', 'SoloCompetitionRegistration')
TeamMembership = apps.get_model('events', 'TeamMembership')
CustomUser = apps.get_model(settings.AUTH_USER_MODEL)
logger = logging.getLogger(__name__)


CompetitionTeam = apps.get_model('events', 'CompetitionTeam')

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

        if order.status != Order.STATUS_PENDING_PAYMENT:
            return Response(
                {"error": f"Order cannot be cancelled in status: {order.get_status_display()}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            for oi in order.items.select_related().all():
                obj = oi.content_object
                if isinstance(obj, CompetitionTeam) and obj.status == CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION:
                    if obj.group_competition.requires_admin_approval:
                        obj.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
                    else:
                        obj.status = CompetitionTeam.STATUS_CANCELLED
                    obj.save(update_fields=["status"])

            order.status = Order.STATUS_CANCELLED
            order.save(update_fields=["status"])

        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)

@extend_schema(tags=['Shop - Cart'])
class CartView(generics.RetrieveAPIView):
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        cart, created = Cart.objects.get_or_create(user=self.request.user)
        return cart

    @extend_schema(
        summary="View user's shopping cart",
        request=None,
        responses={
            200: get_api_response_serializer(CartSerializer),
        },
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

                price = CartItemSerializer().get_price(ci)
                if price is None or price <= 0:
                    continue

                order = Order.objects.create(
                    user=request.user,
                    subtotal_amount=price,
                    discount_code_applied=cart.applied_discount_code,
                    discount_amount=0,
                    total_amount=price,
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

    def delete(self, request, cart_item_pk, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(user=request.user)
        try:
            cart_item = CartItem.objects.get(pk=cart_item_pk, cart=cart)
            content_object = cart_item.content_object
            cart_item.delete()

            if isinstance(content_object, CompetitionTeam) and content_object.status == CompetitionTeam.STATUS_IN_CART:
                if content_object.group_competition.requires_admin_approval:
                    content_object.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
                else:
                    content_object.status = CompetitionTeam.STATUS_CANCELLED
                content_object.save()
            return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)
        except CartItem.DoesNotExist:
            return Response({"error": "Cart item not found."}, status=status.HTTP_404_NOT_FOUND)


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
                discount.times_used = models.F('times_used') + 1
                discount.save(update_fields=['times_used'])
            order.save(update_fields=['status'])

            CartItem.objects.filter(
                reserved_order=order
            ).delete()

        logger.info(f"Finished processing order: {order.order_id}")

    def post(self, request, *args, **kwargs):
        cart = Cart.objects.filter(user=request.user).prefetch_related('items__content_object').first()
        if not cart or not cart.items.exists():
            return Response({"error": "Your cart is empty."}, status=status.HTTP_400_BAD_REQUEST)

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

            if batch.status == PaymentBatch.STATUS_COMPLETED:
                return redirect(success_url)
            if batch.status not in [PaymentBatch.STATUS_AWAITING_GATEWAY_REDIRECT, PaymentBatch.STATUS_PAYMENT_FAILED]:
                return redirect(f"{failure_url}&reason=invalid_batch_state")

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
                    return redirect(success_url)
                else:
                    err = vr.get('error', 'verify_failed')
                    batch.status = PaymentBatch.STATUS_PAYMENT_FAILED
                    batch.save(update_fields=["status"])
                    batch.orders.update(status=Order.STATUS_PAYMENT_FAILED)
                    return redirect(f"{failure_url}&reason=verify_failed&code={err}")
            else:
                batch.status = PaymentBatch.STATUS_PAYMENT_FAILED
                batch.save(update_fields=["status"])
                batch.orders.update(status=Order.STATUS_PAYMENT_FAILED)
                return redirect(f"{failure_url}&reason=user_cancelled_or_gateway_nok")

        order = order_qs.first()
        success_url = f"{frontend_base_url}{default_success_path}?order_id={order.order_id}"
        failure_url = f"{frontend_base_url}{default_failure_path}?order_id={order.order_id}"

        if order.status == Order.STATUS_COMPLETED:
            return redirect(success_url)
        if order.status not in [Order.STATUS_AWAITING_GATEWAY_REDIRECT, Order.STATUS_PAYMENT_FAILED, Order.STATUS_PENDING_PAYMENT]:
            return redirect(f"{failure_url}&reason=invalid_order_state")

        if status_param == "OK":
            vr = z.verify_payment(authority=authority, amount=order.total_amount)
            if vr.get('status') == 'success':
                with transaction.atomic():
                    order.status = Order.STATUS_PROCESSING_ENROLLMENT
                    order.payment_gateway_txn_id = vr.get('ref_id')
                    order.paid_at = timezone.now()
                    order.save()
                    OrderCheckoutView()._process_successful_order(order)
                return redirect(success_url)
            else:
                order.status = Order.STATUS_PAYMENT_FAILED
                order.save()
                return redirect(f"{failure_url}&reason=verify_failed")
        else:
            order.status = Order.STATUS_PAYMENT_FAILED
            order.save()
            return redirect(f"{failure_url}&reason=user_cancelled_or_gateway_nok")


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
    schema = NoPaginationAutoSchema()
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
                        status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE
                    ).exists()
                elif isinstance(obj, CompetitionTeam):
                    owned = (obj.leader_id == o.user_id and
                             obj.status == CompetitionTeam.STATUS_ACTIVE)
                if owned:
                    return Response(
                        {"error": f"Order {o.id} contains item(s) already owned. Batch payment blocked."},
                        status=400
                    )

        total = sum(o.total_amount for o in qs)
        if total <= 0:
            return Response({"error": "Combined amount is zero or less."}, status=400)

        batch = PaymentBatch.objects.create(
            user=request.user,
            total_amount=total,
            status=PaymentBatch.STATUS_PENDING,
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
        return Response({"error": f"Payment gateway error: {msg}"}, status=400)