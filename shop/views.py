from django.shortcuts import redirect, get_object_or_404
from django.conf import settings
from django.utils import timezone
from django.db import transaction, models
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
import logging

from rest_framework import viewsets, status, generics, views
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes

from .models import DiscountCode, Cart, CartItem, Order, OrderItem
from .serializers import (
    CartSerializer, AddToCartSerializer, ApplyDiscountSerializer,
    OrderSerializer, OrderListSerializer, CartItemSerializer
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


class CartView(generics.RetrieveAPIView):
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        cart, created = Cart.objects.get_or_create(user=self.request.user)
        return cart

    @extend_schema(summary="View user's shopping cart", tags=['Shop - Cart'])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AddToCartView(views.APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AddToCartSerializer

    @extend_schema(summary="Add item to cart", request=AddToCartSerializer,
                   responses={200: CartSerializer, 400: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
                   tags=['Shop - Cart'])
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
            if content_object.group_competition.requires_admin_approval and content_object.status != CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT:
                return Response({"error": "This team must be admin-approved and awaiting payment."},
                                status=status.HTTP_400_BAD_REQUEST)

        content_type = ContentType.objects.get_for_model(content_object)

        cart_item, created = CartItem.objects.get_or_create(cart=cart, content_type=content_type,
                                                            object_id=content_object.pk)

        if not created:
            return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)

        if isinstance(content_object, CompetitionTeam):
            content_object.status = CompetitionTeam.STATUS_IN_CART
            content_object.save()

        return Response(CartSerializer(cart).data, status=status.HTTP_201_CREATED)


class RemoveCartItemView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Remove item from cart", responses={200: CartSerializer, 404: OpenApiTypes.OBJECT},
                   tags=['Shop - Cart'])
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


class ApplyDiscountView(views.APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ApplyDiscountSerializer

    @extend_schema(summary="Apply discount code to cart", request=ApplyDiscountSerializer,
                   responses={200: CartSerializer, 400: OpenApiTypes.OBJECT}, tags=['Shop - Cart'])
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


class RemoveDiscountView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Remove discount code from cart", responses={200: CartSerializer}, tags=['Shop - Cart'])
    def delete(self, request, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(user=request.user)
        if cart.applied_discount_code:
            cart.applied_discount_code = None
            cart.save()
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)


@extend_schema(tags=['Shop - Orders & Payment'])
class OrderCheckoutView(views.APIView):
    permission_classes = [IsAuthenticated]

    def _process_successful_order(self, order):
        logger.info(f"Processing successful order: {order.order_id}")
        with transaction.atomic():
            for order_item in order.items.all():
                content_object = order_item.content_object
                if not content_object: continue

                if isinstance(content_object, Presentation):
                    enrollment, _ = PresentationEnrollment.objects.update_or_create(
                        user=order.user, presentation=content_object,
                        defaults={'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE, 'order_item': order_item}
                    )
                elif isinstance(content_object, SoloCompetition):
                    registration, _ = SoloCompetitionRegistration.objects.update_or_create(
                        user=order.user, solo_competition=content_object,
                        defaults={'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
                                  'order_item': order_item}
                    )
                elif isinstance(content_object, CompetitionTeam):
                    team = content_object
                    team.status = CompetitionTeam.STATUS_ACTIVE
                    team.save()
                    if not team.group_competition.requires_admin_approval and team.member_emails_snapshot:
                        TeamMembership.objects.get_or_create(user=team.leader, team=team)
                        for email in team.member_emails_snapshot:
                            try:
                                member_user = CustomUser.objects.get(email=email)
                                TeamMembership.objects.get_or_create(user=member_user, team=team)
                            except CustomUser.DoesNotExist:
                                logger.error(
                                    f"User with email {email} not found for team {team.name} in order {order.order_id}")

            order.status = Order.STATUS_COMPLETED
            if order.discount_code_applied:
                discount = order.discount_code_applied
                discount.times_used = models.F('times_used') + 1
                discount.save(update_fields=['times_used'])
            order.save(update_fields=['status'])
        logger.info(f"Finished processing order: {order.order_id}")

    @extend_schema(summary="Checkout cart and create an order",
                   responses={201: OrderSerializer, 400: OpenApiTypes.OBJECT})
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
                user=request.user, subtotal_amount=subtotal,
                discount_code_applied=cart.applied_discount_code, discount_amount=discount_amount,
                total_amount=total_amount, status=Order.STATUS_PENDING_PAYMENT
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

            cart.items.all().delete()
            cart.applied_discount_code = None
            cart.save()

        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Shop - Orders & Payment'])
class OrderPaymentInitiateView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Initiate payment for an order via Zarinpal",
                   responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT,
                              500: OpenApiTypes.OBJECT})
    def post(self, request, order_pk, *args, **kwargs):
        order = get_object_or_404(Order, pk=order_pk, user=request.user)

        if order.status not in [Order.STATUS_PENDING_PAYMENT, Order.STATUS_PAYMENT_FAILED]:
            return Response({"error": f"Order not eligible for payment. Status: {order.get_status_display()}"},
                            status=status.HTTP_400_BAD_REQUEST)
        if order.total_amount <= 0:
            return Response({"error": "Order total is zero or less. Payment not required via gateway."},
                            status=status.HTTP_400_BAD_REQUEST)

        zarrinpal_client = ZarrinPal()
        if not zarrinpal_client.CALLBACK_URL:
            logger.error("Zarinpal PAYMENT_CALLBACK_URL in settings is not a full URL or is a placeholder.")
            return Response({"error": "Payment callback URL misconfiguration."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        payment_result = zarrinpal_client.create_payment(
            amount=order.total_amount,  # ZarrinPal class expects amount in Toman
            mobile=order.user.phone_number or "",
            email=order.user.email or ""
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


@extend_schema(tags=['Shop - Orders & Payment'])
class ZarinpalPaymentCallbackView(views.APIView):
    permission_classes = [AllowAny]

    @extend_schema(summary="Handles Zarinpal callback after payment attempt",
                   responses={302: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT})
    def get(self, request, *args, **kwargs):
        authority = request.GET.get('Authority')
        status_param = request.GET.get('Status')

        frontend_base_url = getattr(settings, 'FRONTEND_BASE_URL', '')
        default_failure_path = getattr(settings, 'FRONTEND_PAYMENT_FAILURE_PATH', '/payment-failed')
        default_success_path = getattr(settings, 'FRONTEND_PAYMENT_SUCCESS_PATH', '/payment-success')

        if not authority:
            logger.warning("Zarinpal callback: Authority missing.")
            return redirect(f"{frontend_base_url}{default_failure_path}?error=invalid_callback_params")

        try:
            order = Order.objects.get(payment_gateway_authority=authority)
        except Order.DoesNotExist:
            logger.error(f"Zarinpal callback: Order not found for authority {authority}.")
            return redirect(f"{frontend_base_url}{default_failure_path}?error=order_not_found")

        success_url = f"{frontend_base_url}{default_success_path}?order_id={order.order_id}"
        failure_url = f"{frontend_base_url}{default_failure_path}?order_id={order.order_id}"

        if order.status == Order.STATUS_COMPLETED:
            logger.info(f"Order {order.order_id} already completed. Redirecting to success.")
            return redirect(success_url)
        if order.status not in [Order.STATUS_AWAITING_GATEWAY_REDIRECT, Order.STATUS_PAYMENT_FAILED,
                                Order.STATUS_PENDING_PAYMENT]:
            logger.warning(
                f"Order {order.order_id} not in a verifiable state. Current: {order.status}. Redirecting to failure.")
            return redirect(f"{failure_url}&reason=invalid_order_state")

        if status_param == "OK":
            zarrinpal_client = ZarrinPal()
            verification_result = zarrinpal_client.verify_payment(
                authority=authority,
                amount=order.total_amount
            )

            if verification_result.get('status') == 'success':
                with transaction.atomic():
                    order.status = Order.STATUS_PROCESSING_ENROLLMENT
                    order.payment_gateway_txn_id = verification_result.get('ref_id')
                    order.paid_at = timezone.now()
                    order.save()

                    OrderCheckoutView()._process_successful_order(order)
                logger.info(
                    f"ZarrinPal payment verified for order {order.order_id}, Ref ID: {verification_result.get('ref_id')}")
                return redirect(success_url)
            else:
                error_msg = verification_result.get('error', 'Verification failed.')
                logger.error(f"ZarrinPal verification failed for order {order.order_id}: {error_msg}")
                order.status = Order.STATUS_PAYMENT_FAILED
                order.save()
                return redirect(f"{failure_url}&reason=verify_failed&code={error_msg}")
        else:
            logger.warning(f"ZarrinPal callback with status '{status_param}' for order {order.order_id}.")
            order.status = Order.STATUS_PAYMENT_FAILED
            order.save()
            return redirect(f"{failure_url}&reason=user_cancelled_or_gateway_nok")


@extend_schema(tags=['Shop - Orders & Payment'])
class OrderHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'list':
            return OrderListSerializer
        return OrderSerializer
