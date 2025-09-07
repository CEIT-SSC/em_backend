from django.contrib import admin
from .models import DiscountCode, Cart, CartItem, Order, OrderItem, PaymentBatch

@admin.register(DiscountCode)
class DiscountCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_type', 'value', 'is_active', 'valid_from', 'valid_to', 'max_uses', 'times_used', 'min_order_value')
    search_fields = ('code',)
    list_filter = ('is_active', 'discount_type', 'valid_from', 'valid_to')
    readonly_fields = ('times_used',)

class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ('content_object', 'added_at')

    def get_queryset(self, request):
        return super().get_queryset(request)

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('user', 'applied_discount_code', 'created_at', 'get_subtotal_display', 'get_total_display')
    search_fields = ('user__email',)
    list_filter = ('created_at',)
    readonly_fields = ('created_at',)
    autocomplete_fields = ['user', 'applied_discount_code']
    inlines = [CartItemInline]

    def get_subtotal_display(self, obj):
        return obj.get_subtotal()
    get_subtotal_display.short_description = "Subtotal"

    def get_total_display(self, obj):
        return obj.get_total()
    get_total_display.short_description = "Total"


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('content_object', 'description', 'price')

    def get_queryset(self, request):
        return super().get_queryset(request)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_id', 'user', 'total_amount', 'status', 'payment_gateway_txn_id', 'created_at', 'paid_at')
    search_fields = ('order_id', 'user__email', 'payment_gateway_txn_id')
    list_filter = ('status', 'created_at', 'paid_at')
    readonly_fields = ('order_id', 'created_at', 'paid_at', 'subtotal_amount', 'discount_amount', 'total_amount')
    autocomplete_fields = ['user', 'discount_code_applied']
    inlines = [OrderItemInline]
    date_hierarchy = 'created_at'


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'description', 'price', 'content_object_display')
    search_fields = ('order__order_id', 'description')
    list_filter = ('order__status',)
    readonly_fields = ('content_object',)

    def content_object_display(self, obj):
        return str(obj.content_object) if obj.content_object else "N/A"
    content_object_display.short_description = "Purchased Item"

@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('cart', 'content_object_display', 'added_at')

    def content_object_display(self, obj):
        return str(obj.content_object) if obj.content_object else "N/A"
    content_object_display.short_description = "Item in Cart"

@admin.register(PaymentBatch)
class PaymentBatchAdmin(admin.ModelAdmin):
    list_display = ('batch_id', 'user', 'total_amount', 'status', 'payment_gateway_authority', 'created_at', 'paid_at')
    search_fields = ('batch_id', 'payment_gateway_authority', 'user__email')
    list_filter = ('status', 'created_at', 'paid_at')