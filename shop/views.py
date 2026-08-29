from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
from rest_framework import viewsets, status, generics, views
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer, \
    get_paginated_response_serializer
from .models import DiscountCode, Cart, CartItem, Order, OrderItem, Product
from .serializers import (
    CartSerializer, AddToCartSerializer, ApplyDiscountSerializer,
    OrderSerializer, OrderCheckoutResultSerializer, OrderListSerializer,
    UserPurchasesSerializer, CartItemSerializer, ProductSerializer
)
from .fulfillment import process_successful_order
from .eligibility import (
    has_capacity as _has_capacity,
    is_already_owned as _is_already_owned,
    is_cart_item_active as _is_cart_item_active,
    is_content_available as _is_content_available,
    is_registration_open as _is_registration_open,
)

Presentation = apps.get_model('events', 'Presentation')
SoloCompetition = apps.get_model('events', 'SoloCompetition')
CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
PresentationEnrollment = apps.get_model('events', 'PresentationEnrollment')
SoloCompetitionRegistration = apps.get_model('events', 'SoloCompetitionRegistration')
TeamMembership = apps.get_model('events', 'TeamMembership')
Event = apps.get_model('events', 'Event')


def _release_reservations_for_orders(order_qs_or_list):
    CompetitionTeam = apps.get_model('events', 'CompetitionTeam')

    orders = order_qs_or_list if hasattr(order_qs_or_list, '__iter__') else [order_qs_or_list]
    with transaction.atomic():
        for order in orders:
            for item in order.items.all():
                obj = item.content_object
                if isinstance(obj, CompetitionTeam) and \
                        obj.status == CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION:
                    obj.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
                    obj.save(update_fields=["status"])


def _add_to_cart_and_update_status(user, item_object):
    cart, _ = Cart.objects.get_or_create(user=user)
    content_type = ContentType.objects.get_for_model(item_object)

    if CartItem.objects.filter(cart=cart, content_type=content_type, object_id=item_object.pk).exists():
        return False, "Item is already in your cart."

    CartItem.objects.create(cart=cart, content_type=content_type, object_id=item_object.pk)

    return True, "Item added to your cart."


def _find_matching_pending_order(*, user, event, cart_item_prices, subtotal, discount_code, discount, total):
    candidates = (
        Order.objects.filter(
            user=user,
            event=event,
            status__in=[Order.STATUS_PENDING_PAYMENT, Order.STATUS_PAYMENT_FAILED],
            subtotal_amount=subtotal,
            discount_code_applied=discount_code,
            discount_amount=discount,
            total_amount=total,
        )
        .select_for_update()
        .prefetch_related('items')
        .order_by('-created_at')
    )[:20]

    expected = {
        (item.content_type_id, item.object_id, Decimal(str(price)))
        for item, price in cart_item_prices
    }
    for candidate in candidates:
        actual = {
            (item.content_type_id, item.object_id, item.price)
            for item in candidate.items.all()
        }
        if actual == expected and len(actual) == len(cart_item_prices):
            return candidate
    return None


def _supersede_overlapping_pending_orders(*, user, cart_item_prices):
    """Cancel older payable orders sharing any item with a new cart snapshot."""
    overlap = Q()
    for item, _price in cart_item_prices:
        overlap |= Q(
            items__content_type_id=item.content_type_id,
            items__object_id=item.object_id,
        )

    if not overlap:
        return

    older_orders = list(
        Order.objects.select_for_update()
        .filter(
            user=user,
            status__in=[Order.STATUS_PENDING_PAYMENT, Order.STATUS_PAYMENT_FAILED],
        )
        .filter(overlap)
        .distinct()
    )
    if not older_orders:
        return

    Order.objects.filter(pk__in=[order.pk for order in older_orders]).update(
        status=Order.STATUS_CANCELLED,
    )
    _release_reservations_for_orders(older_orders)


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
    serializer_class = OrderSerializer

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
class CartItemView(views.APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Add an item to the cart (Enroll/Register/Buy)",
        description="Handles adding paid items to the cart. For free items, it enrolls the user directly.",
        request=AddToCartSerializer,
        responses={
            200: get_api_response_serializer(CartSerializer),
            201: get_api_response_serializer(None),
            400: ApiErrorResponseSerializer,
            403: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = AddToCartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        item_type_str = serializer.validated_data['item_type']
        item_id = serializer.validated_data['item_id']
        user = request.user

        item_model_map = {
            'presentation': Presentation, 'solo_competition': SoloCompetition, 'product': Product,
        }
        item_model = item_model_map.get(item_type_str)
        if not item_model:
            return Response({"error": "Invalid item type."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            item_object = item_model.objects.get(pk=item_id)
        except item_model.DoesNotExist:
            return Response({"error": f"{item_type_str.replace('_', ' ').capitalize()} not found."},
                            status=status.HTTP_404_NOT_FOUND)

        if not _is_content_available(item_object):
            return Response({"error": "This item is no longer available."}, status=status.HTTP_400_BAD_REQUEST)

        if not _is_registration_open(item_object):
            return Response({"error": "The registration period for this item has passed."},
                            status=status.HTTP_400_BAD_REQUEST)

        if _is_already_owned(user, item_object):
            return Response({"message": "You already own this item."},
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
                        PresentationEnrollment.objects.update_or_create(
                            user=user, presentation=item_object,
                            defaults={'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE}
                        )
                    else:
                        SoloCompetitionRegistration.objects.update_or_create(
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

        return Response({"error": "Unhandled item type."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @extend_schema(
        summary="Remove an item from the cart",
        description="Removes an item from the cart by its type and ID, provided as query parameters.",
        parameters=[
            OpenApiParameter(name='item_type', description='Type of the item to remove', required=True, type=str,
                             enum=['presentation', 'solo_competition', 'product']),
            OpenApiParameter(name='item_id', description='ID of the item to remove', required=True, type=str),
        ],
        responses={
            200: get_api_response_serializer(CartSerializer),
            400: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        },
    )
    def delete(self, request, *args, **kwargs):
        item_type_str = request.query_params.get('item_type')
        item_id = request.query_params.get('item_id')
        user = request.user

        if not item_type_str or not item_id:
            return Response({"error": "Both 'item_type' and 'item_id' query parameters are required."},
                            status=status.HTTP_400_BAD_REQUEST)

        cart, _ = Cart.objects.get_or_create(user=user)

        item_model_map = {
            'presentation': Presentation,
            'solo_competition': SoloCompetition,
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
        except Exception:
            return Response({"error": "Invalid item ID format."}, status=status.HTTP_400_BAD_REQUEST)

        cart_item.delete()

        if cart.applied_discount_code and not cart._eligible_items_for_code(cart.applied_discount_code):
            cart.applied_discount_code = None
            cart.save(update_fields=['applied_discount_code'])

        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)


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
    summary="Checkout and pay the cart through the wallet",
    responses={
        201: get_api_response_serializer(OrderCheckoutResultSerializer),
        400: ApiErrorResponseSerializer,
    },
    parameters=[
        OpenApiParameter(name='event', description='Event ID for the order', required=False, type=int),
    ]
)
class OrderCheckoutView(views.APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderCheckoutResultSerializer

    def post(self, request, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(user=request.user)

        event_param = request.query_params.get("event")
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

        cart_item_list = list(cart_items.select_related('content_type'))
        if not cart_item_list:
            return Response({"error": "Your cart is empty for this event."}, status=status.HTTP_400_BAD_REQUEST)

        inactive = [ci.id for ci in cart_item_list if not _is_cart_item_active(ci)]
        if inactive:
            return Response(
                {"error": "Some items are no longer available.", "cart_item_ids": inactive},
                status=status.HTTP_400_BAD_REQUEST
            )

        subtotal = cart._subtotal_for_items(cart_item_list)
        discount_amount = cart.get_discount_amount()
        total_amount = subtotal - discount_amount
        if total_amount < 0:
            return Response({"error": "Order total cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

        cart_item_prices = [
            (item, CartItemSerializer().get_price(item))
            for item in cart_item_list
        ]

        with transaction.atomic():
            Cart.objects.select_for_update().get(pk=cart.pk)
            expected_cart_item_ids = {item.pk for item in cart_item_list}
            current_items = (
                cart.items.filter(event=event)
                if event is not None
                else cart.items.filter(event__isnull=True)
            )
            if set(current_items.values_list('pk', flat=True)) != expected_cart_item_ids:
                return Response(
                    {"error": "Your cart changed while checkout was being prepared. Please try again."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            order = _find_matching_pending_order(
                user=request.user,
                event=event,
                cart_item_prices=cart_item_prices,
                subtotal=subtotal,
                discount_code=cart.applied_discount_code,
                discount=discount_amount,
                total=total_amount,
            )
            if order is None:
                _supersede_overlapping_pending_orders(
                    user=request.user,
                    cart_item_prices=cart_item_prices,
                )
                # A callback may have completed an older overlapping order while
                # this checkout was waiting for that order's row lock.
                if set(current_items.values_list('pk', flat=True)) != expected_cart_item_ids:
                    return Response(
                        {"error": "Your cart changed while checkout was being prepared. Please try again."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                order = Order.objects.create(
                    user=request.user,
                    event=event,
                    subtotal_amount=subtotal,
                    discount_code_applied=cart.applied_discount_code,
                    discount_amount=discount_amount,
                    total_amount=total_amount,
                    status=Order.STATUS_PENDING_PAYMENT,
                )
                for item, price in cart_item_prices:
                    OrderItem.objects.create(
                        order=order,
                        content_type=item.content_type,
                        object_id=item.object_id,
                        description=str(item.content_object),
                        price=price,
                    )

        if total_amount == 0:
            process_successful_order(order)
            CartItem.objects.filter(pk__in=[item.pk for item in cart_item_list]).delete()
            if cart.applied_discount_code:
                cart.applied_discount_code = None
                cart.save(update_fields=['applied_discount_code'])
            from wallet.services import WalletService

            payment = {
                'payment_required': False,
                'payment_url': None,
                'topup': None,
                'balance': WalletService.get_balance(request.user),
            }
        else:
            from wallet.exceptions import WalletError
            from wallet.payments import get_wallet_callback_url
            from wallet.services import WalletService

            try:
                payment = WalletService.pay_or_start_order_payment(
                    request.user,
                    order,
                    callback_url=get_wallet_callback_url(request),
                    metadata={'source': 'cart_checkout'},
                )
            except WalletError as exc:
                return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        order.refresh_from_db()
        return Response({
            'order': OrderSerializer(order, context={'request': request}).data,
            'payment_required': payment['payment_required'],
            'payment_url': payment['payment_url'],
            'topup_id': payment['topup'].public_id if payment['topup'] else None,
            'wallet_balance': payment['balance'],
        }, status=status.HTTP_201_CREATED)


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
    responses={200: get_api_response_serializer(UserPurchasesSerializer)}
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
