from django_typomatic import ts_interface
from rest_framework import serializers
from django.apps import apps
from events.serializers import PresentationSerializer, SoloCompetitionSerializer, CompetitionTeamDetailSerializer
from .models import Cart, CartItem, Order, OrderItem


@ts_interface()
class ItemDetailSerializer(serializers.Serializer):
    presentation = PresentationSerializer(read_only=True, required=False)
    solo_competition = SoloCompetitionSerializer(read_only=True, required=False)
    competition_team = CompetitionTeamDetailSerializer(read_only=True, required=False)
    item_type = serializers.ChoiceField(choices=['presentation', 'solo_competition', 'competition_team'])


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

    class Meta:
        model = CartItem
        fields = ['id', 'content_type', 'object_id', 'item_details', 'price', 'added_at']
        read_only_fields = ['added_at', 'price', 'item_details']

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

    def get_price(self, obj):
        content_object = obj.content_object
        if content_object:
            PresentationModel = apps.get_model('events', 'Presentation')
            SoloCompetitionModel = apps.get_model('events', 'SoloCompetition')
            CompetitionTeamModel = apps.get_model('events', 'CompetitionTeam')

            if hasattr(content_object, 'is_paid') and not content_object.is_paid: return 0
            if isinstance(content_object,
                          PresentationModel) and content_object.price is not None: return content_object.price
            if isinstance(content_object,
                          SoloCompetitionModel) and content_object.price_per_participant is not None: return content_object.price_per_participant
            if isinstance(content_object, CompetitionTeamModel):
                parent_comp = content_object.group_competition
                if parent_comp.is_paid and parent_comp.price_per_group is not None: return parent_comp.price_per_group
        return 0


@ts_interface()
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
    class Meta:
        model = Order
        fields = ['order_id', 'total_amount', 'status', 'created_at', 'paid_at']


@ts_interface()
class PaymentInitiateResponseSerializer(serializers.Serializer):
    payment_url = serializers.URLField()
    authority = serializers.CharField()
