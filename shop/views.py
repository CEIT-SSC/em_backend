from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status, views, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from em_backend.schemas import (
    ApiErrorResponseSerializer,
    get_api_response_serializer,
    get_paginated_response_serializer,
)
from .fulfillment import (
    OrderCapacityError,
    fulfill_order,
    has_capacity,
    release_order_reservations,
)
from .models import Cart, CartItem, DiscountCode, Order, OrderItem, Product
from .serializers import (
    AddToCartSerializer,
    ApplyDiscountSerializer,
    CartItemSerializer,
    CartSerializer,
    OrderListSerializer,
    OrderSerializer,
    ProductSerializer,
    UserPurchasesSerializer,
)

Presentation = apps.get_model('events', 'Presentation')
SoloCompetition = apps.get_model('events', 'SoloCompetition')
CompetitionTeam = apps.get_model('events', 'CompetitionTeam')
PresentationEnrollment = apps.get_model('events', 'PresentationEnrollment')
SoloCompetitionRegistration = apps.get_model('events', 'SoloCompetitionRegistration')
TeamMembership = apps.get_model('events', 'TeamMembership')
Event = apps.get_model('events', 'Event')
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


def _is_already_owned(user, item_object) -> bool:
    user_to_check = item_object.leader if isinstance(item_object, CompetitionTeam) else user

    if isinstance(item_object, Presentation):
        if PresentationEnrollment.objects.filter(
                user=user_to_check, presentation=item_object,
                status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE
        ).exists():
            return True

    elif isinstance(item_object, SoloCompetition):
        if SoloCompetitionRegistration.objects.filter(
                user=user_to_check, solo_competition=item_object,
                status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE
        ).exists():
            return True

    elif isinstance(item_object, CompetitionTeam):
        if item_object.status == CompetitionTeam.STATUS_ACTIVE and (
                item_object.leader_id == user.id or
                TeamMembership.objects.filter(team=item_object, user=user).exists()
        ):
            return True

    ct = ContentType.objects.get_for_model(item_object)
    if OrderItem.objects.filter(
            content_type=ct,
            object_id=item_object.pk,
            order__user=user_to_check,
            order__status__in=[Order.STATUS_COMPLETED],
    ).exists():
        return True

    return False


def _add_to_cart_and_update_status(user, item_object):
    cart, _ = Cart.objects.get_or_create(user=user)
    content_type = ContentType.objects.get_for_model(item_object)

    if CartItem.objects.filter(cart=cart, content_type=content_type, object_id=item_object.pk).exists():
        return False, "Item is already in your cart."

    CartItem.objects.create(cart=cart, content_type=content_type, object_id=item_object.pk)
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
        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update(),
                order_id=order_id,
                user=request.user,
            )
            cancellable_statuses = {
                Order.STATUS_PENDING_PAYMENT,
                Order.STATUS_PAYMENT_FAILED,
            }
            if order.status not in cancellable_statuses:
                return Response(
                    {"error": f"Order cannot be cancelled in status '{order.get_status_display()}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            order.status = Order.STATUS_CANCELLED
            order.save(update_fields=["status"])
            release_order_reservations(order)

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

        if not has_capacity(item_object):
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
        parameters=[
            OpenApiParameter(name='item_type', description='Type of item to remove', required=True, type=str,
                             enum=['presentation', 'solo_competition', 'product']),
            OpenApiParameter(name='item_id', description='ID of item to remove', required=True, type=str),
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

        if not cart_items.exists():
            return Response({"error": "Your cart is empty for this event."}, status=status.HTTP_400_BAD_REQUEST)

        inactive = [ci.id for ci in cart_items if not _is_cart_item_active(ci)]
        if inactive:
            return Response(
                {"error": "Some items are no longer available.", "cart_item_ids": inactive},
                status=status.HTTP_400_BAD_REQUEST
            )

        unavailable = [ci.id for ci in cart_items if not has_capacity(ci.content_object)]
        if unavailable:
            return Response(
                {"error": "Some items have reached capacity.", "cart_item_ids": unavailable},
                status=status.HTTP_400_BAD_REQUEST,
            )

        already_owned = [ci.id for ci in cart_items if _is_already_owned(request.user, ci.content_object)]
        if already_owned:
            return Response(
                {"error": "Some items are already owned.", "cart_item_ids": already_owned},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subtotal = cart._subtotal_for_items(cart_items)
        discount_amount = cart.get_discount_amount()
        total_amount = subtotal - discount_amount
        if total_amount < 0:
            return Response({"error": "Order total cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            order = Order.objects.create(
                user=request.user,
                event=event,
                subtotal_amount=subtotal,
                discount_code_applied=cart.applied_discount_code,
                discount_amount=discount_amount,
                total_amount=total_amount,
                status=Order.STATUS_PENDING_PAYMENT,
            )
            for ci in cart_items.select_related('content_type'):
                OrderItem.objects.create(
                    order=order,
                    content_type=ci.content_type,
                    object_id=ci.object_id,
                    description=str(ci.content_object),
                    price=CartItemSerializer().get_price(ci),
                )

        if total_amount == 0:
            try:
                order, _ = fulfill_order(order)
            except OrderCapacityError as exc:
                order.status = Order.STATUS_CANCELLED
                order.save(update_fields=['status'])
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


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
    summary="List all purchases of the current user",
    parameters=[
        OpenApiParameter(
            name="event",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Event ID to filter purchases by. If omitted, returns non-event purchases."
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
