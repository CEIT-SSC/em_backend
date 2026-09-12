import csv
from django import forms
from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponse

from .models import Cart, CartItem, DiscountCode, Order, OrderItem, Pack, PackItem, PaymentApp, Product

ITEM_SOURCES = [
    ('Presentation', ('events', 'Presentation'), 'title'),
    ('Solo Competition', ('events', 'SoloCompetition'), 'title'),
    ('Competition Team', ('events', 'CompetitionTeam'), 'name'),
    ('Product', ('shop', 'Product'), 'name'),
    ('Pack', ('shop', 'Pack'), 'name'),
]

PACK_ITEM_SOURCES = [
    ('Presentation', ('events', 'Presentation'), 'title'),
    ('Solo Competition', ('events', 'SoloCompetition'), 'title'),
    ('Product', ('shop', 'Product'), 'name'),
]


def build_generic_item_choices(limit_per_type=500, sources=None, active_only=False):
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
    for type_label, (app_label, model_name), display_field in (sources or ITEM_SOURCES):
        Model = apps.get_model(app_label, model_name)
        ct = ContentType.objects.get_for_model(Model)
        qs = Model.objects.all()
        if active_only and any(field.name == 'is_active' for field in Model._meta.fields):
            qs = qs.filter(is_active=True)
        qs = qs.order_by('id')[:limit_per_type]
        for obj in qs:
            display = getattr(obj, display_field, None) or str(obj)
            choices.append((f"{ct.pk}:{obj.pk}", f"[{type_label}] {display}"))
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
            self.fields['target_item'].initial = f"{self.instance.content_type_id}:{self.instance.object_id}"

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
                ct = ContentType.objects.get_for_id(int(ct_id_str))
                Model = ct.model_class()
                if not Model.objects.filter(pk=int(obj_id_str)).exists():
                    raise ValidationError("Chosen target item no longer exists.")
                cleaned['content_type'] = ct
                cleaned['object_id'] = int(obj_id_str)
            except Exception:
                raise ValidationError("Invalid target item selection.")
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
            'description': "Pick a specific item (Presentation, Solo Competition, Competition Team, Product, or Pack). Leave empty for a global discount."
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


class PackItemAdminForm(forms.ModelForm):
    target_item = forms.ChoiceField(required=True, label='Contained item')

    class Meta:
        model = PackItem
        exclude = ('content_type', 'object_id')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['target_item'].choices = build_generic_item_choices(
            sources=PACK_ITEM_SOURCES,
            active_only=True,
        )
        if self.instance and self.instance.pk:
            initial = f'{self.instance.content_type_id}:{self.instance.object_id}'
            self.fields['target_item'].initial = initial
            if initial not in {value for value, _label in self.fields['target_item'].choices}:
                self.fields['target_item'].choices.append(
                    (initial, f'[Unavailable] {self.instance.content_object or initial}')
                )

    def clean_target_item(self):
        raw = self.cleaned_data['target_item']
        try:
            content_type_id, object_id = (int(value) for value in raw.split(':', 1))
            content_type = ContentType.objects.get_for_id(content_type_id)
            model = content_type.model_class()
            item = model.objects.get(pk=object_id)
        except (TypeError, ValueError, ContentType.DoesNotExist, AttributeError, ObjectDoesNotExist):
            raise ValidationError('Invalid contained item.')

        if (content_type.app_label, content_type.model) not in {
            ('events', 'presentation'), ('events', 'solocompetition'), ('shop', 'product'),
        }:
            raise ValidationError('This type of item cannot be added to a pack.')
        self.instance.content_type = content_type
        self.instance.object_id = object_id
        return raw


class PackItemInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        active_forms = [
            form for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get('DELETE')
        ]
        if not active_forms:
            raise ValidationError('A pack must contain at least one item.')
        selected = [form.cleaned_data.get('target_item') for form in active_forms]
        if len(selected) != len(set(selected)):
            raise ValidationError('The same item cannot be included in a pack more than once.')


class PackItemInline(admin.TabularInline):
    model = PackItem
    form = PackItemAdminForm
    formset = PackItemInlineFormSet
    extra = 1


@admin.register(Pack)
class PackAdmin(admin.ModelAdmin):
    list_display = ('name', 'calculated_price', 'real_price', 'event', 'is_active', 'created_at')
    list_filter = ('is_active', 'event', 'created_at')
    search_fields = ('name', 'description')
    readonly_fields = ('calculated_price', 'created_at')
    inlines = [PackItemInline]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_id', 'user', 'event', 'total_amount', 'status', 'created_at', 'paid_at',
    )
    search_fields = ('order_id', 'user__email', 'user__first_name', 'user__last_name')
    list_filter = ('status', 'created_at', 'paid_at', 'event')
    readonly_fields = (
        'order_id', 'created_at', 'paid_at', 'subtotal_amount', 'discount_amount', 'total_amount',
    )
    autocomplete_fields = ['user', 'discount_code_applied']
    inlines = [OrderItemInline]
    date_hierarchy = 'created_at'
    list_select_related = ('user', 'event')
    actions = ['export_orders_csv']

    def export_orders_csv(self, request, queryset):
        fieldnames = [
            'order_id', 'user_email', 'event_id',
            'subtotal_amount', 'discount_amount', 'total_amount',
            'status', 'created_at', 'paid_at',
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
                'subtotal_amount': o.subtotal_amount,
                'discount_amount': o.discount_amount,
                'total_amount': o.total_amount,
                'status': o.status,
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
