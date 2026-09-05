from decimal import Decimal

from django_typomatic import ts_interface
from rest_framework import serializers

from wallet.models import WalletEntry, WalletTopUp


@ts_interface()
class WalletBalanceSerializer(serializers.Serializer):
    balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency = serializers.CharField()


@ts_interface()
class WalletEntrySerializer(serializers.ModelSerializer):
    signed_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    order_id = serializers.UUIDField(source='order.order_id', read_only=True, allow_null=True)
    topup_id = serializers.UUIDField(source='topup.public_id', read_only=True, allow_null=True)

    class Meta:
        model = WalletEntry
        fields = (
            'id',
            'entry_type',
            'direction',
            'amount',
            'signed_amount',
            'balance_after',
            'idempotency_key',
            'order_id',
            'topup_id',
            'related_entry',
            'reason',
            'metadata',
            'created_at',
        )
        read_only_fields = fields


@ts_interface()
class StartTopUpSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('1.00'))


@ts_interface()
class TopUpSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTopUp
        fields = (
            'public_id',
            'payment_intent_id',
            'payment_attempt_id',
            'amount',
            'status',
            'payment_url',
            'gateway_authority',
            'gateway_ref_id',
            'created_at',
            'credited_at',
        )
        read_only_fields = fields


@ts_interface()
class TopUpStartResponseSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    payment_intent_id = serializers.UUIDField()
    payment_attempt_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    status = serializers.CharField()
    payment_url = serializers.URLField(allow_null=True)
    authority = serializers.CharField(allow_null=True)


@ts_interface()
class PayOrderSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()


@ts_interface()
class WalletOrderPaymentResultSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    payment_required = serializers.BooleanField()
    payment_url = serializers.URLField(allow_null=True)
    topup_id = serializers.UUIDField(allow_null=True)
    entry_id = serializers.IntegerField(allow_null=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    already_processed = serializers.BooleanField()


@ts_interface()
class AdminAdjustmentSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    direction = serializers.ChoiceField(choices=[WalletEntry.DIRECTION_CREDIT, WalletEntry.DIRECTION_DEBIT])
    reason = serializers.CharField(min_length=3, max_length=2000)
    idempotency_key = serializers.RegexField(regex=r'^[\w.:-]{8,128}$')


@ts_interface()
class AdminRefundSerializer(serializers.Serializer):
    order_id = serializers.UUIDField(required=False)
    entry_id = serializers.IntegerField(required=False)
    reason = serializers.CharField(min_length=3, max_length=2000)
    idempotency_key = serializers.RegexField(regex=r'^[\w.:-]{8,128}$', required=False)

    def validate(self, attrs):
        if not attrs.get('order_id') and not attrs.get('entry_id'):
            raise serializers.ValidationError("Provide either order_id or entry_id.")
        return attrs


@ts_interface()
class AdminLedgerResultSerializer(serializers.Serializer):
    entry_id = serializers.IntegerField()
    entry_type = serializers.CharField()
    direction = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    already_processed = serializers.BooleanField()
