from django.contrib import admin

from wallet.models import Wallet, WalletEntry, WalletTopUp


class WalletEntryInline(admin.TabularInline):
    model = WalletEntry
    extra = 0
    can_delete = False
    show_change_link = True
    readonly_fields = (
        'entry_type', 'direction', 'amount', 'balance_after', 'idempotency_key',
        'order', 'topup', 'related_entry', 'reason', 'metadata', 'created_at', 'created_by',
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'balance', 'created_at', 'updated_at')
    search_fields = ('user__email',)
    readonly_fields = ('user', 'balance', 'created_at', 'updated_at')
    inlines = [WalletEntryInline]
    list_select_related = ('user',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WalletEntry)
class WalletEntryAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'wallet', 'entry_type', 'direction', 'amount', 'balance_after',
        'idempotency_key', 'created_at', 'created_by',
    )
    list_filter = ('entry_type', 'direction', 'created_at')
    search_fields = ('idempotency_key', 'wallet__user__email', 'reason', 'order__order_id')
    readonly_fields = (
        'wallet', 'entry_type', 'direction', 'amount', 'balance_after', 'idempotency_key',
        'order', 'topup', 'related_entry', 'reason', 'metadata', 'created_at', 'created_by',
    )
    list_select_related = ('wallet__user', 'created_by', 'order')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WalletTopUp)
class WalletTopUpAdmin(admin.ModelAdmin):
    list_display = (
        'public_id', 'wallet', 'order', 'amount', 'status', 'gateway_authority',
        'gateway_ref_id', 'created_at', 'credited_at',
    )
    list_filter = ('status', 'created_at')
    search_fields = ('public_id', 'gateway_authority', 'wallet__user__email', 'order__order_id')
    readonly_fields = (
        'public_id', 'wallet', 'order', 'amount', 'status',
        'gateway_authority', 'gateway_ref_id', 'payment_url', 'metadata',
        'created_at', 'updated_at', 'credited_at',
    )
    list_select_related = ('wallet__user', 'order')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
