from rest_framework import serializers

from .models import PaymentAttempt, PaymentIntent, PaymentSettlement


class CreatePaymentIntentSerializer(serializers.Serializer):
    amount_rial = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    purpose = serializers.ChoiceField(choices=PaymentIntent.PURPOSE_CHOICES)
    reference_id = serializers.CharField(min_length=1, max_length=128)
    description = serializers.CharField(min_length=1, max_length=255)
    idempotency_key = serializers.CharField(min_length=1, max_length=128)
    metadata = serializers.JSONField(required=False, default=dict)


class StartPaymentAttemptSerializer(serializers.Serializer):
    provider = serializers.RegexField(regex=r"^[A-Za-z0-9_.-]{1,64}$")
    idempotency_key = serializers.CharField(min_length=1, max_length=128)
    callback_url = serializers.URLField(max_length=500)


class PaymentAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAttempt
        fields = (
            "id", "provider", "status", "gateway_authority", "gateway_reference_id",
            "payment_url", "error_code", "error_message", "created_at", "updated_at",
            "verified_at", "failed_at", "reversed_at",
        )
        read_only_fields = fields


class PaymentIntentSerializer(serializers.ModelSerializer):
    attempts = PaymentAttemptSerializer(many=True, read_only=True)
    settlement_status = serializers.SerializerMethodField()

    class Meta:
        model = PaymentIntent
        fields = (
            "id", "amount_rial", "currency", "purpose", "reference_id", "description", "status",
            "metadata", "settlement_status", "attempts", "created_at", "updated_at", "succeeded_at",
            "reversed_at",
        )
        read_only_fields = fields

    def get_settlement_status(self, obj) -> str | None:
        try:
            return obj.settlement.status
        except PaymentSettlement.DoesNotExist:
            return None


class CallbackResponseSerializer(serializers.Serializer):
    intent_id = serializers.UUIDField()
    attempt_id = serializers.UUIDField()
    payment_status = serializers.CharField()
    attempt_status = serializers.CharField()
    settlement_status = serializers.CharField(allow_null=True)


class PaymentErrorSerializer(serializers.Serializer):
    error = serializers.CharField()
    code = serializers.CharField()
