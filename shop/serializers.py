from rest_framework import serializers
from django.apps import apps
from .models import Cart, CartItem, Order, OrderItem


class GenericRelatedField(serializers.RelatedField):
    def to_representation(self, value):
        if hasattr(value, 'title'):
            return f"{value.__class__.__name__}: {value.title}"
        if hasattr(value, 'name'):
            return f"{value.__class__.__name__}: {value.name}"
        return str(value)

class CartItemSerializer(serializers.ModelSerializer):
    item_details = GenericRelatedField(source='content_object', read_only=True)
    price = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ['id', 'content_type', 'object_id', 'item_details', 'price', 'added_at']
        read_only_fields = ['added_at', 'price', 'item_details']

    def get_price(self, obj):
        content_object = obj.content_object
        if content_object:
            PresentationModel = apps.get_model('events', 'Presentation')
            SoloCompetitionModel = apps.get_model('events', 'SoloCompetition')
            CompetitionTeamModel = apps.get_model('events', 'CompetitionTeam')

            if hasattr(content_object, 'is_paid') and not content_object.is_paid: return 0
            if isinstance(content_object, PresentationModel) and content_object.price is not None: return content_object.price
            if isinstance(content_object, SoloCompetitionModel) and content_object.price_per_participant is not None: return content_object.price_per_participant
            if isinstance(content_object, CompetitionTeamModel):
                parent_comp = content_object.group_competition
                if parent_comp.is_paid and parent_comp.price_per_group is not None: return parent_comp.price_per_group
        return 0

class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    applied_discount_code_details = serializers.StringRelatedField(source='applied_discount_code', read_only=True)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, source='get_subtotal', read_only=True)
    discount_applied = serializers.DecimalField(max_digits=10, decimal_places=2, source='get_discount_amount', read_only=True)
    total = serializers.DecimalField(max_digits=10, decimal_places=2, source='get_total', read_only=True)

    class Meta:
        model = Cart
        fields = ['id', 'user', 'items', 'applied_discount_code', 'applied_discount_code_details',
                  'subtotal', 'discount_applied', 'total', 'created_at',]
        read_only_fields = ['user', 'created_at',]

class AddToCartSerializer(serializers.Serializer):
    item_type = serializers.ChoiceField(choices=['presentation', 'solocompetition', 'competitionteam'])
    item_id = serializers.IntegerField() # PK of the Presentation, SoloCompetition, or CompetitionTeam

class ApplyDiscountSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)

class OrderItemSerializer(serializers.ModelSerializer):
    item_details = GenericRelatedField(source='content_object', read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'item_details', 'description', 'price'] # content_object details via item_details

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


class OrderListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ['order_id', 'total_amount', 'status', 'created_at', 'paid_at']
