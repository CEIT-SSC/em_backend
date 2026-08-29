from django.contrib import admin

from .models import PaymentAttempt, PaymentIntent, PaymentSettlement


class PaymentAttemptInline(admin.TabularInline):
    model = PaymentAttempt
    extra = 0
    can_delete = False
    fields = (
        "id", "provider", "status", "gateway_authority", "gateway_reference_id", "error_code",
        "created_at", "verified_at",
    )
    readonly_fields = fields
    show_change_link = True


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = ("id", "purpose", "reference_id", "amount_rial", "status", "user", "created_at")
    list_filter = ("purpose", "status", "created_at")
    search_fields = ("id", "idempotency_key", "reference_id", "user__email")
    list_select_related = ("user",)
    readonly_fields = (
        "id", "user", "amount_rial", "currency", "purpose", "reference_id", "description",
        "idempotency_key", "status", "metadata", "created_at", "updated_at", "succeeded_at", "reversed_at",
    )
    inlines = (PaymentAttemptInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ("id", "intent", "provider", "status", "gateway_authority", "gateway_reference_id", "created_at")
    list_filter = ("provider", "status", "created_at")
    search_fields = (
        "id", "intent__id", "idempotency_key", "gateway_authority", "gateway_reference_id",
        "intent__reference_id",
    )
    list_select_related = ("intent",)
    readonly_fields = [field.name for field in PaymentAttempt._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentSettlement)
class PaymentSettlementAdmin(admin.ModelAdmin):
    list_display = ("intent", "status", "idempotency_key", "settled_at", "reversed_at", "updated_at")
    list_filter = ("status", "created_at")
    search_fields = ("intent__id", "intent__reference_id", "idempotency_key", "error_message")
    list_select_related = ("intent",)
    readonly_fields = [field.name for field in PaymentSettlement._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
