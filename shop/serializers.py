from django_typomatic import ts_interface
from rest_framework import serializers
from events.models import Presentation, SoloCompetition, CompetitionTeam
from events.serializers import PresentationSerializer, SoloCompetitionSerializer, CompetitionTeamDetailSerializer
from .models import Cart, CartItem, Order, Product
from drf_spectacular.utils import extend_schema_field, OpenApiTypes


@ts_interface()
class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'


@ts_interface()
class CartItemSerializer(serializers.ModelSerializer):
    price = serializers.SerializerMethodField()
    event_id = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            'id', 'content_type', 'object_id',
            'price', 'added_at',
            'event_id',
        ]
        read_only_fields = ['added_at', 'price', 'event_id']

    @extend_schema_field(OpenApiTypes.INT)
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

    @extend_schema_field(OpenApiTypes.NUMBER)
    def get_price(self, obj):
        content_object = obj.content_object
        if not content_object:
            return 0

        if hasattr(content_object, 'is_paid') and not content_object.is_paid:
            return 0
        if isinstance(content_object, Presentation) and content_object.price is not None:
            return content_object.price
        if isinstance(content_object, SoloCompetition) and content_object.price_per_participant is not None:
            return content_object.price_per_participant
        if isinstance(content_object, CompetitionTeam):
            parent_comp = content_object.group_competition
            if parent_comp.is_paid and parent_comp.price_per_member is not None:
                member_count = content_object.memberships.count()
                return parent_comp.price_per_member * member_count
        if isinstance(content_object, Product):
            return content_object.price
        return 0


@ts_interface()
class CartSerializer(serializers.ModelSerializer):
    presentations = serializers.SerializerMethodField()
    solo_competitions = serializers.SerializerMethodField()
    competition_teams = serializers.SerializerMethodField()
    products = serializers.SerializerMethodField()
    discount_code = serializers.CharField(
        source='applied_discount_code.code', read_only=True, allow_null=True
    )
    subtotal_amount = serializers.SerializerMethodField()
    discount_amount = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = (
            'applied_discount_code',
            'discount_code',
            'presentations',
            'solo_competitions',
            'competition_teams',
            'products',
            'subtotal_amount',
            'discount_amount',
            'total_amount',
            'created_at',
        )

    def _get_items_by_type(self, obj, model):
        items = []
        for item in self._filtered_items_qs(obj):
            if isinstance(item.content_object, model):
                items.append(item.content_object)
        return items

    def get_presentations(self, obj):
        return PresentationSerializer(self._get_items_by_type(obj, Presentation), many=True, context=self.context).data

    def get_solo_competitions(self, obj):
        return SoloCompetitionSerializer(self._get_items_by_type(obj, SoloCompetition), many=True,
                                         context=self.context).data

    def get_competition_teams(self, obj):
        return CompetitionTeamDetailSerializer(self._get_items_by_type(obj, CompetitionTeam), many=True,
                                               context=self.context).data

    def get_products(self, obj):
        return ProductSerializer(self._get_items_by_type(obj, Product), many=True, context=self.context).data

    def _filtered_items_qs(self, obj):
        return getattr(obj, "_filtered_items", obj.items.all())

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_subtotal_amount(self, obj):
        qs = self._filtered_items_qs(obj)
        return obj._subtotal_for_items(qs)

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_discount_amount(self, obj):
        qs = list(self._filtered_items_qs(obj))
        subtotal = obj._subtotal_for_items(qs)
        code = obj.applied_discount_code
        if not code or not code.is_valid(subtotal):
            return 0

        eligible = obj._eligible_items_for_code(code)
        eligible_ids = {(ci.content_type_id, ci.object_id) for ci in eligible}
        filtered_eligible = [ci for ci in qs if (ci.content_type_id, ci.object_id) in eligible_ids]
        eligible_subtotal = obj._subtotal_for_items(filtered_eligible)

        if eligible_subtotal <= 0:
            return 0

        discount_value = code.calculate_discount(eligible_subtotal)
        return min(discount_value, subtotal)

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_total_amount(self, obj):
        subtotal = self.get_subtotal_amount(obj)
        discount = self.get_discount_amount(obj)
        return subtotal - discount


@ts_interface()
class AddToCartSerializer(serializers.Serializer):
    item_type = serializers.ChoiceField(choices=['presentation', 'solo_competition', 'competition_team', 'product'])
    item_id = serializers.IntegerField()


@ts_interface()
class ApplyDiscountSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)


@ts_interface()
class OrderSerializer(serializers.ModelSerializer):
    presentations = serializers.SerializerMethodField()
    solo_competitions = serializers.SerializerMethodField()
    competition_teams = serializers.SerializerMethodField()
    products = serializers.SerializerMethodField()
    user_email = serializers.EmailField(source='user.email', read_only=True, allow_null=True)
    discount_code_str = serializers.CharField(source='discount_code_applied.code', read_only=True, allow_null=True)

    class Meta:
        model = Order
        fields = [
            'order_id', 'user', 'user_email', 'event',
            'presentations', 'solo_competitions', 'competition_teams', 'products',
            'subtotal_amount', 'discount_code_applied', 'discount_code_str',
            'discount_amount', 'total_amount', 'status',
            'payment_gateway_authority', 'payment_gateway_txn_id',
            'created_at', 'paid_at',
        ]
        read_only_fields = [
            'order_id', 'user', 'user_email',
            'presentations', 'solo_competitions', 'competition_teams', 'products',
            'subtotal_amount', 'discount_code_str', 'discount_amount', 'total_amount',
            'payment_gateway_authority', 'payment_gateway_txn_id',
            'created_at', 'paid_at',
        ]

    def _get_items_by_type(self, obj, model):
        items = []
        for item in obj.items.all():
            if isinstance(item.content_object, model):
                items.append(item.content_object)
        return items

    def get_presentations(self, obj):
        return PresentationSerializer(self._get_items_by_type(obj, Presentation), many=True, context=self.context).data

    def get_solo_competitions(self, obj):
        return SoloCompetitionSerializer(self._get_items_by_type(obj, SoloCompetition), many=True,
                                         context=self.context).data

    def get_competition_teams(self, obj):
        return CompetitionTeamDetailSerializer(self._get_items_by_type(obj, CompetitionTeam), many=True,
                                               context=self.context).data

    def get_products(self, obj):
        return ProductSerializer(self._get_items_by_type(obj, Product), many=True, context=self.context).data


@ts_interface()
class OrderListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = [
            'id',
            'order_id',
            'total_amount',
            'status',
            'created_at',
            'paid_at',
            'event'
        ]


@ts_interface()
class PaymentInitiateResponseSerializer(serializers.Serializer):
    payment_url = serializers.URLField()
    authority = serializers.CharField()


@ts_interface()
class OrderPaymentInitiateSerializer(serializers.Serializer):
    app = serializers.SlugField(required=False, allow_blank=True, allow_null=True)


@ts_interface()
class UserPurchasesSerializer(serializers.Serializer):
    presentations = PresentationSerializer(many=True, read_only=True)
    solo_competitions = SoloCompetitionSerializer(many=True, read_only=True)
    competition_teams = serializers.SerializerMethodField()
    products = ProductSerializer(many=True, read_only=True)

    def get_competition_teams(self, instance):
        teams = instance.get('competition_teams', [])
        return CompetitionTeamDetailSerializer(teams, many=True, context=self.context).data