from django.contrib import admin
from django import forms
from .models import Cart, CartItem, Order, OrderItem, PaymentBatch, DiscountCode, PaymentApp, Product
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
from django.core.exceptions import ValidationError

ITEM_SOURCES = [
    ('Presentation', ('events', 'Presentation'), 'title'),
    ('Solo Competition', ('events', 'SoloCompetition'), 'title'),
    ('Competition Team', ('events', 'CompetitionTeam'), 'name'),
    ('Product', ('shop', 'Product'), 'name'),
]

def build_generic_item_choices(limit_per_type=500):
    """
    Returns choices like:
      [
        ('24:3', '[Presentation] Gamecraft Keynote'),
        ('16:1', '[Solo Competition] Speed Coding'),
        ('31:7', '[Competition Team] Team Phoenix'),
        ('32:1', '[Product] T-Shirt'),
      ]
    """
    choices = [('', '---------')]
    for type_label, (app_label, model_name), display_field in ITEM_SOURCES:
        Model = apps.get_model(app_label, model_name)
        ct = ContentType.objects.get_for_model(Model)
        qs = Model.objects.all().order_by('id')[:limit_per_type]
        for obj in qs:
            display = getattr(obj, display_field, None)
            if not display:
                display = str(obj)
            value = f"{ct.pk}:{obj.pk}"
            label = f"[{type_label}] {display}"
            choices.append((value, label))
    return choices


class DiscountCodeAdminForm(forms.ModelForm):
    target_item = forms.ChoiceField(
        required=False,
        label='Discount target item',
        help_text="Pick a specific item (optional). Leave empty for a global discount."
    )

    class Meta:
        model = DiscountCode
        exclude = ('content_type', 'object_id',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['target_item'].choices = build_generic_item_choices()

        if self.instance and self.instance.pk and self.instance.content_type_id and self.instance.object_id:
            initial_value = f"{self.instance.content_type_id}:{self.instance.object_id}"
            self.fields['target_item'].initial = initial_value

    def clean(self):
        cleaned = super().clean()

        pct = cleaned.get('percentage') or 0
        amt = cleaned.get('amount') or 0
        if (pct > 0 and amt > 0) or (pct <= 0 and amt <= 0):
            raise ValidationError("Set exactly one of 'percentage' OR 'amount' and it must be > 0.")

        raw = cleaned.get('target_item')
        if raw:
            try:
                ct_id_str, obj_id_str = raw.split(':', 1)
                ct_id = int(ct_id_str)
                obj_id = int(obj_id_str)
            except Exception:
                raise ValidationError("Invalid target item selection.")

            ct = ContentType.objects.get_for_id(ct_id)
            Model = ct.model_class()
            if not Model.objects.filter(pk=obj_id).exists():
                raise ValidationError("Chosen target item no longer exists.")

            cleaned['content_type'] = ct
            cleaned['object_id'] = obj_id
        else:
            cleaned['content_type'] = None
            cleaned['object_id'] = None

        return cleaned

    def save(self, commit=True):
        self.instance.content_type = self.cleaned_data.get('content_type')
        self.instance.object_id = self.cleaned_data.get('object_id')
        return super().save(commit=commit)


@admin.register(DiscountCode)
class DiscountCodeAdmin(admin.ModelAdmin):
    form = DiscountCodeAdminForm

    list_display = (
        'code', 'is_active', 'percentage', 'amount',
        'valid_from', 'valid_to', 'min_order_amount',
        'max_uses', 'times_used', 'target_display',
    )
    list_filter = ('is_active',)
    search_fields = ('code',)

    fieldsets = (
        (None, {
            'fields': ('code', 'is_active')
        }),
        ('Discount Value', {
            'fields': ('percentage', 'amount', 'min_order_amount', 'max_uses'),
            'description': "Set exactly ONE of percentage or amount (the other must be 0 or blank)."
        }),
        ('Validity Window', {
            'fields': ('valid_from', 'valid_to')
        }),
        ('Target (optional)', {
            'fields': ('target_item',),
            'description': "Pick a specific item (Presentation, Solo Competition, Competition Team, or Product). Leave empty for a global discount."
        }),
    )

    def target_display(self, obj):
        if obj.content_type_id and obj.object_id:
            return f"{obj.content_type.app_label}.{obj.content_type.model} #{obj.object_id}"
        return "Global"
    target_display.short_description = "Target"


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

@admin.register(PaymentApp)
class PaymentAppAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "is_active")
    list_filter  = ("is_active",)
    search_fields = ("slug", "name")

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name',)