from django.contrib import admin
from django import forms
from .models import Cart, CartItem, Order, OrderItem, DiscountCode, PaymentApp, Product
from django.contrib.contenttypes.models import ContentType
from django.apps import apps
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.db import models

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

class HasAuthorityFilter(admin.SimpleListFilter):
    title = "Has gateway authority"
    parameter_name = "has_authority"
    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "No"))
    def queryset(self, request, queryset):
        val = self.value()
        if val == "yes":
            return queryset.exclude(payment_gateway_authority__isnull=True).exclude(payment_gateway_authority__exact="")
        if val == "no":
            return queryset.filter(models.Q(payment_gateway_authority__isnull=True) | models.Q(payment_gateway_authority=""))
        return queryset

class HasTxnFilter(admin.SimpleListFilter):
    title = "Has gateway txn_id"
    parameter_name = "has_txn"
    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "No"))
    def queryset(self, request, queryset):
        val = self.value()
        if val == "yes":
            return queryset.exclude(payment_gateway_txn_id__isnull=True).exclude(payment_gateway_txn_id__exact="")
        if val == "no":
            return queryset.filter(models.Q(payment_gateway_txn_id__isnull=True) | models.Q(payment_gateway_txn_id=""))
        return queryset
    
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_id', 'user', 'event', 'redirect_app',
        'total_amount', 'status',
        'payment_gateway_authority', 'payment_gateway_txn_id',
        'created_at', 'paid_at',
    )
    search_fields = (
        'order_id',
        'user__email', 'user__first_name', 'user__last_name',
        'payment_gateway_authority', 'payment_gateway_txn_id',
    )
    list_filter = (
        'status', 'created_at', 'paid_at', 'event', 'redirect_app',
        HasAuthorityFilter, HasTxnFilter,
    )
    readonly_fields = (
        'order_id', 'created_at', 'paid_at',
        'subtotal_amount', 'discount_amount', 'total_amount',
        'payment_gateway_authority', 'payment_gateway_txn_id',
        'gateway_inquiry_readonly',
    )
    autocomplete_fields = ['user', 'discount_code_applied']
    inlines = [OrderItemInline]
    date_hierarchy = 'created_at'
    list_select_related = ('user', 'event')

    actions = ['export_orders_csv', 'inquiry_gateway_status']

    def gateway_inquiry_readonly(self, obj):
        from .payments import ZarrinPal
        if not obj.payment_gateway_authority:
            return "—"
        z = ZarrinPal()
        res = z.inquiry(authority=obj.payment_gateway_authority)
        parts = []
        for k in ('status', 'error'):
            v = res.get(k)
            if v:
                parts.append(f"<b>{k}</b>: {v}")
        html = "<br>".join(parts) if parts else "No data"
        return mark_safe(html)
    gateway_inquiry_readonly.short_description = "Gateway inquiry (live)"

    def gateway_inquiry_badge(self, obj):
        from .payments import ZarrinPal
        if not obj.payment_gateway_authority:
            return "-"
        z = ZarrinPal()
        res = z.inquiry(authority=obj.payment_gateway_authority)
        st = (res.get('status') or '').lower()
        color = {
            'failed': '#c0392b',
            'in_bank': '#f39c12',
            'not_found': '#7f8c8d',
        }.get(st, '#2ecc71')
        label = st.upper() if st else 'N/A'
        return format_html('<span style="padding:2px 6px;border-radius:10px;background:{};color:#fff;">{}</span>', color, label)
    gateway_inquiry_badge.short_description = "Gateway status"

    def inquiry_gateway_status(self, request, queryset):
        from .payments import ZarrinPal
        z = ZarrinPal()

        MAX_CHECK = 20
        qs = queryset.exclude(payment_gateway_authority__isnull=True)\
                    .exclude(payment_gateway_authority__exact="")[:MAX_CHECK]

        total = queryset.count()
        checked = 0
        for o in qs:
            res = z.inquiry(authority=o.payment_gateway_authority)
            checked += 1
            messages.info(request, f"[{o.order_id}] inquiry → {res}")

        if total > MAX_CHECK:
            messages.warning(request, f"Selected {total} orders; limited to first {MAX_CHECK} to avoid load.")

        if checked == 0:
            messages.warning(request, "No selected orders had an Authority to check.")
    inquiry_gateway_status.short_description = "Gateway inquiry (show results in messages)"

    def export_orders_csv(self, request, queryset):
        import csv
        from django.http import HttpResponse
        fieldnames = [
            'order_id', 'user_email', 'event_id', 'redirect_app',
            'subtotal_amount', 'discount_amount', 'total_amount',
            'status', 'payment_gateway_authority', 'payment_gateway_txn_id',
            'created_at', 'paid_at',
        ]
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = 'attachment; filename="orders_export.csv"'
        writer = csv.DictWriter(resp, fieldnames=fieldnames)
        writer.writeheader()
        for o in queryset.select_related('user', 'event'):
            writer.writerow({
                'order_id': str(o.order_id),
                'user_email': getattr(o.user, 'email', ''),
                'event_id': getattr(o.event, 'id', '') if o.event_id else '',
                'redirect_app': o.redirect_app or '',
                'subtotal_amount': o.subtotal_amount,
                'discount_amount': o.discount_amount,
                'total_amount': o.total_amount,
                'status': o.status,
                'payment_gateway_authority': o.payment_gateway_authority or '',
                'payment_gateway_txn_id': o.payment_gateway_txn_id or '',
                'created_at': o.created_at.isoformat(),
                'paid_at': o.paid_at.isoformat() if o.paid_at else '',
            })
        return resp
    export_orders_csv.short_description = "Export selected orders to CSV"


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