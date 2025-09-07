from django_typomatic import ts_interface
from rest_framework import serializers
from django.apps import apps
from events.models import Presentation, SoloCompetition, CompetitionTeam
from events.serializers import PresentationSerializer, SoloCompetitionSerializer, CompetitionTeamDetailSerializer
from .models import Cart, CartItem, Order, OrderItem
from events.serializers import CompetitionTeamDetailSerializer


@ts_interface()
class ItemDetailSerializer(serializers.Serializer):
    item_type = serializers.SerializerMethodField()
    presentation = PresentationSerializer(read_only=True)
    solo_competition = SoloCompetitionSerializer(read_only=True)
    competition_team = CompetitionTeamDetailSerializer(read_only=True)

    def get_item_type(self, obj):
        if isinstance(obj, Presentation):
            return 'presentation'
        if isinstance(obj, SoloCompetition):
            return 'solo_competition'
        if isinstance(obj, CompetitionTeam):
            return 'competition_team'
        return None

    def to_representation(self, obj):
        data = {'item_type': self.get_item_type(obj)}
        if isinstance(obj, Presentation):
            data['presentation'] = PresentationSerializer(obj, context=self.context).data
        elif isinstance(obj, SoloCompetition):
            data['solo_competition'] = SoloCompetitionSerializer(obj, context=self.context).data
        elif isinstance(obj, CompetitionTeam):
            data['competition_team'] = CompetitionTeamDetailSerializer(obj, context=self.context).data
        return data


@ts_interface()
class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


@ts_interface()
class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()


@ts_interface()
class CartItemSerializer(serializers.ModelSerializer):
    item_details = ItemDetailSerializer(read_only=True, source='content_object')
    price = serializers.SerializerMethodField()

    event_id = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    reserved_order_id = serializers.SerializerMethodField()
    reserved_order_item_id = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            'id', 'content_type', 'object_id',
            'item_details', 'price', 'added_at',
            'event_id', 'status', 'reserved_order_id', 'reserved_order_item_id',
        ]
        read_only_fields = ['added_at', 'price', 'item_details', 'event_id', 'status',
                            'reserved_order_id', 'reserved_order_item_id']


    def _pending_statuses(self):
        from .models import Order
        return [
            Order.STATUS_PENDING_PAYMENT,
            Order.STATUS_AWAITING_GATEWAY_REDIRECT,
            Order.STATUS_PAYMENT_FAILED,
        ]

    def _find_unpaid_reservation(self, obj):
        """Return the latest unpaid OrderItem (if any) that already references this cart item."""
        from .models import OrderItem
        qs = (OrderItem.objects
              .select_related('order')
              .filter(
                  content_type=obj.content_type,
                  object_id=obj.object_id,
                  order__user=obj.cart.user,
                  order__status__in=self._pending_statuses(),
              )
              .order_by('-order__created_at', '-id'))
        return qs.first()

    def _already_owned(self, obj):
        """Whether the user already completed purchase/enrollment for this item."""
        from .models import Order, OrderItem
        return OrderItem.objects.filter(
            content_type=obj.content_type,
            object_id=obj.object_id,
            order__user=obj.cart.user,
            order__status=Order.STATUS_COMPLETED,
        ).exists()

    def get_event_id(self, obj):
        if getattr(obj, 'event_id', None):
            return obj.event_id

        co = obj.content_object
        if not co:
            return None
        ev_id = getattr(co, 'event_id', None)
        if ev_id:
            return ev_id
        parent = getattr(co, 'group_competition', None)
        return getattr(parent, 'event_id', None) if parent else None

    def get_status(self, obj):
        if self._already_owned(obj):
            return 'owned'
        if self._find_unpaid_reservation(obj):
            return 'reserved'
        return 'free'

    def get_reserved_order_id(self, obj):
        oi = self._find_unpaid_reservation(obj)
        return oi.order.id if oi else None

    def get_reserved_order_item_id(self, obj):
        oi = self._find_unpaid_reservation(obj)
        return oi.id if oi else None

    def get_price(self, obj):
        content_object = obj.content_object
        if not content_object:
            return 0

        PresentationModel = apps.get_model('events', 'Presentation')
        SoloCompetitionModel = apps.get_model('events', 'SoloCompetition')
        CompetitionTeamModel = apps.get_model('events', 'CompetitionTeam')

        if hasattr(content_object, 'is_paid') and not content_object.is_paid:
            return 0
        if isinstance(content_object, PresentationModel) and content_object.price is not None:
            return content_object.price
        if isinstance(content_object, SoloCompetitionModel) and content_object.price_per_participant is not None:
            return content_object.price_per_participant
        if isinstance(content_object, CompetitionTeamModel):
            parent_comp = content_object.group_competition
            if parent_comp.is_paid and parent_comp.price_per_group is not None:
                return parent_comp.price_per_group
        return 0

@ts_interface()
class OrderItemWithEventSerializer(serializers.ModelSerializer):
    event_id = serializers.SerializerMethodField()
    item_type = serializers.SerializerMethodField()
    item_title = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "description",
            "price",
            "content_type",
            "object_id",
            "event_id",
            "item_type",
            "item_title",
        ]

    def get_event_id(self, obj):
        try:
            co = obj.content_object
            if not co:
                return None
            ev = getattr(co, "event_id", None)
            if ev:
                return ev
            parent = getattr(co, "group_competition", None)
            if parent:
                return getattr(parent, "event_id", None)
        except Exception:
            pass
        return None

    def get_item_type(self, obj):
        try:
            co = obj.content_object
            if not co:
                return None
            model_name = co.__class__.__name__.lower()
            if model_name == "presentation":
                return "presentation"
            if model_name == "solocompetition":
                return "solocompetition"
            if model_name == "competitionteam":
                return "competitionteam"
            return model_name
        except Exception:
            return None

    def get_item_title(self, obj):
        try:
            co = obj.content_object
            if not co:
                return None
            for attr in ("title", "name", "team_name"):
                if hasattr(co, attr):
                    return getattr(co, attr)
        except Exception:
            pass
        return obj.description

@ts_interface()
class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal_amount = serializers.SerializerMethodField()
    discount_code = serializers.CharField(
        source='applied_discount_code.code',
        read_only=True,
        allow_null=True
    )
    discount_amount = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = [
            'id',
            'user',
            'applied_discount_code',
            'discount_code',
            'items',
            'subtotal_amount',
            'discount_amount',
            'total_amount',
            'created_at',
        ]
        read_only_fields = [
            'id',
            'user',
            'discount_code',
            'items',
            'subtotal_amount',
            'discount_amount',
            'total_amount',
            'created_at',
        ]

    def get_subtotal_amount(self, obj):
        return obj.get_subtotal()

    def get_discount_amount(self, obj):
        return obj.get_discount_amount()

    def get_total_amount(self, obj):
        return obj.get_total()

@ts_interface()
class AddToCartSerializer(serializers.Serializer):
    item_type = serializers.ChoiceField(choices=['presentation', 'solo_competition', 'competition_team'])
    item_id = serializers.IntegerField()


@ts_interface()
class ApplyDiscountSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)


@ts_interface()
class OrderItemSerializer(serializers.ModelSerializer):
    item_details = ItemDetailSerializer(read_only=True, source='content_object')

    class Meta:
        model = OrderItem
        fields = ['id', 'item_details', 'description', 'price']

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        content_object = instance.content_object
        request = self.context.get('request')

        if isinstance(content_object, apps.get_model('events', 'Presentation')):
            data = PresentationSerializer(content_object, context={'request': request}).data
            data['item_type'] = 'presentation'
            representation['item_details'] = data
        elif isinstance(content_object, apps.get_model('events', 'SoloCompetition')):
            data = SoloCompetitionSerializer(content_object, context={'request': request}).data
            data['item_type'] = 'solo_competition'
            representation['item_details'] = data
        elif isinstance(content_object, apps.get_model('events', 'CompetitionTeam')):
            data = CompetitionTeamDetailSerializer(content_object, context={'request': request}).data
            data['item_type'] = 'competition_team'
            representation['item_details'] = data
        else:
            representation['item_details'] = None

        return representation


@ts_interface()
class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True, allow_null=True)
    discount_code_str = serializers.CharField(source='discount_code_applied.code', read_only=True, allow_null=True)

    class Meta:
        model = Order
        fields = [
            'order_id', 'user', 'user_email', 'items',
            'subtotal_amount', 'discount_code_applied', 'discount_code_str',
            'discount_amount', 'total_amount', 'status',
            'payment_gateway_authority', 'payment_gateway_txn_id',
            'created_at', 'paid_at',
        ]
        read_only_fields = [
            'order_id', 'user', 'user_email', 'items',
            'subtotal_amount', 'discount_code_str', 'discount_amount', 'total_amount',
            'payment_gateway_authority', 'payment_gateway_txn_id',
            'created_at', 'paid_at',
        ]


@ts_interface()
class OrderListSerializer(serializers.ModelSerializer):
    items = OrderItemWithEventSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            'id',
            'order_id',
            'total_amount',
            'status',
            'created_at',
            'paid_at',
            'items',
        ]


@ts_interface()
class PaymentInitiateResponseSerializer(serializers.Serializer):
    payment_url = serializers.URLField()
    authority = serializers.CharField()

@ts_interface()
class PartialCheckoutSerializer(serializers.Serializer):
    cart_item_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)

@ts_interface()
class BatchPaymentInitiateSerializer(serializers.Serializer):
    order_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)