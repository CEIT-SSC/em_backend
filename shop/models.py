import uuid
from decimal import Decimal
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Product(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to='products/')
    features = models.JSONField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True, related_name="products")
    capacity = models.PositiveIntegerField(null=True, blank=True, help_text="Leave blank for unlimited stock.")

    def __str__(self):
        return self.name

class Pack(models.Model):
    """A buyable collection of existing shop/event items."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    real_price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to='packs/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    event = models.ForeignKey(
        'events.Event', on_delete=models.SET_NULL, null=True, blank=True, related_name='packs'
    )

    @property
    def calculated_price(self):
        from .pricing import get_item_price

        if not self.pk:
            return Decimal('0')
        return sum(
            (get_item_price(pack_item.content_object) for pack_item in self.items.all()),
            Decimal('0'),
        )

    def clean(self):
        super().clean()
        if self.real_price is not None and self.real_price < 0:
            raise ValidationError("The pack's real price cannot be negative.")

    def __str__(self):
        return self.name

class PackItem(models.Model):
    pack = models.ForeignKey(Pack, on_delete=models.CASCADE, related_name='items')
    limit_to_models = (
        models.Q(app_label='events', model='presentation')
        | models.Q(app_label='events', model='solocompetition')
        | models.Q(app_label='shop', model='product')
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        limit_choices_to=limit_to_models,
        verbose_name='Item Type',
    )
    object_id = models.PositiveIntegerField(verbose_name='Item ID')
    content_object = GenericForeignKey('content_type', 'object_id')

    def clean(self):
        super().clean()
        item = self.content_object
        if item is None:
            raise ValidationError({'object_id': 'The selected item does not exist.'})

        allowed_models = {
            ('events', 'presentation'),
            ('events', 'solocompetition'),
            ('shop', 'product'),
        }
        if (self.content_type.app_label, self.content_type.model) not in allowed_models:
            raise ValidationError({'content_type': 'This type of item cannot be added to a pack.'})

    def __str__(self):
        return f"{self.pack}: {self.content_object or 'unavailable item'}"

    class Meta:
        unique_together = ('pack', 'content_type', 'object_id')
        ordering = ['pk']
        verbose_name = 'Pack Item'
        verbose_name_plural = 'Pack Items'


class DiscountCode(models.Model):
    code = models.CharField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)

    min_order_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True,
                                           help_text="Total times this code can be used across all users")
    times_used = models.PositiveIntegerField(default=0)
    max_uses_per_user = models.PositiveIntegerField(null=True, blank=True)

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        limit_choices_to=(
            models.Q(app_label='events', model='presentation') |
            models.Q(app_label='events', model='solocompetition') |
            models.Q(app_label='shop', model='product') |
            models.Q(app_label='shop', model='pack')
        ),
        verbose_name="Discount target type"
    )
    object_id = models.PositiveIntegerField(null=True, blank=True, verbose_name="Discount target object id")
    item_object = GenericForeignKey('content_type', 'object_id')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['content_type', 'object_id']),
        ]
        verbose_name = "Discount Code"
        verbose_name_plural = "Discount Codes"

    def __str__(self):
        target = None
        if self.content_type_id and self.object_id:
            target = f"{self.content_type.app_label}.{self.content_type.model}#{self.object_id}"
        return f"{self.code}{' → ' + target if target else ''}"

    def has_remaining_user_quota(self, user) -> bool:
        per_user_limit = getattr(self, 'max_uses_per_user', None)
        if not per_user_limit:
            return True
        DiscountRedemptionModel = apps.get_model('shop', 'DiscountRedemption')
        used = DiscountRedemptionModel.objects.filter(code=self, user=user).count()
        return used < per_user_limit

    def is_valid(self, cart_subtotal: Decimal) -> bool:
        if not self.is_active:
            return False
        now = timezone.now()
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_to and now > self.valid_to:
            return False
        if self.min_order_amount and Decimal(cart_subtotal) < self.min_order_amount:
            return False
        if self.max_uses is not None and self.times_used >= self.max_uses:
            return False
        return True

    def clean(self):
        pct = (self.percentage or Decimal('0'))
        amt = (self.amount or Decimal('0'))

        if (pct > 0 and amt > 0) or (pct <= 0 and amt <= 0):
            raise ValidationError("Set exactly one of 'percentage' OR 'amount' (and it must be > 0).")

        if self.min_order_amount is not None and self.min_order_amount < 0:
            raise ValidationError("'min_order_amount' cannot be negative.")

        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValidationError("'valid_from' must be before 'valid_to'.")

    def is_percentage(self):
        return bool(self.percentage and self.percentage > 0)

    def is_fixed_amount(self):
        return bool(self.amount and self.amount > 0)

    def calculate_discount(self, base_amount: Decimal) -> Decimal:
        base_amount = Decimal(base_amount or 0)
        if base_amount <= 0:
            return Decimal('0')

        if self.is_percentage():
            return (base_amount * self.percentage / Decimal('100')).quantize(Decimal('1.'))
        if self.is_fixed_amount():
            return min(self.amount, base_amount)
        return Decimal('0')


class Cart(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cart",
        verbose_name="User"
    )
    applied_discount_code = models.ForeignKey(
        DiscountCode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Applied Discount Code"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Cart for {self.user.email}"

    def _eligible_items_for_code(self, code: 'DiscountCode'):
        items = list(self.items.select_related('content_type'))
        if code.content_type_id and code.object_id:
            return [
                ci for ci in items
                if ci.content_type_id == code.content_type_id and ci.object_id == code.object_id
            ]
        return items

    def _subtotal_for_items(self, items):
        subtotal = Decimal('0')
        from .pricing import get_item_price

        for ci in items:
            obj = ci.content_object
            if not obj:
                continue

            subtotal += get_item_price(obj)

        return subtotal

    def get_subtotal(self):
        return self._subtotal_for_items(self.items.all())

    def get_discount_amount(self):
        subtotal = self.get_subtotal()
        code = self.applied_discount_code
        if not code or not code.is_valid(subtotal):
            return Decimal('0')

        eligible = self._eligible_items_for_code(code)
        eligible_subtotal = self._subtotal_for_items(eligible)

        if eligible_subtotal <= 0:
            return Decimal('0')

        discount_value = Decimal(code.calculate_discount(eligible_subtotal))
        return min(discount_value, Decimal(subtotal))

    def get_total(self):
        return self.get_subtotal() - self.get_discount_amount()

    class Meta:
        verbose_name = "Shopping Cart"
        verbose_name_plural = "Shopping Carts"


class CartItem(models.Model):
    cart = models.ForeignKey('shop.Cart', on_delete=models.CASCADE, related_name='items')

    event = models.ForeignKey(
        'events.Event',
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name='shop_cart_items',
        db_index=True
    )

    limit_to_models = (
            models.Q(app_label='events', model='presentation')
            | models.Q(app_label='events', model='solocompetition')
            | models.Q(app_label='shop', model='product')
            | models.Q(app_label='shop', model='pack')
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        limit_choices_to=limit_to_models,
        verbose_name="Item Type"
    )
    object_id = models.PositiveIntegerField(verbose_name="Item ID")
    content_object = GenericForeignKey('content_type', 'object_id')

    added_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Item: {self.content_object or 'N/A'} in cart for {self.cart.user.email}"

    class Meta:
        unique_together = ('cart', 'content_type', 'object_id')
        verbose_name = "Cart Item"
        verbose_name_plural = "Cart Items"
        ordering = ['-added_at']

    def save(self, *args, **kwargs):
        if self.event_id is None:
            ev_id = None
            obj = None
            try:
                obj = self.content_object
            except Exception:
                pass
            if obj is not None:
                ev_id = getattr(obj, 'event_id', None)
                if ev_id is None:
                    parent = getattr(obj, 'group_competition', None)
                    ev_id = getattr(parent, 'event_id', None) if parent else None

            if ev_id:
                self.event_id = ev_id

        super().save(*args, **kwargs)


class Order(models.Model):
    STATUS_PENDING_PAYMENT = "pending_payment"
    STATUS_PROCESSING_ENROLLMENT = "processing_enrollment"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_PAYMENT_FAILED = "payment_failed"
    STATUS_REFUNDED = "refunded"

    ORDER_STATUS_CHOICES = [
        (STATUS_PENDING_PAYMENT, "Pending Payment"),
        (STATUS_PROCESSING_ENROLLMENT, "Processing Enrollment/Registration"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_PAYMENT_FAILED, "Payment Failed"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="orders", verbose_name="User")
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    order_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True, verbose_name="Order ID")
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Subtotal Amount")
    discount_code_applied = models.ForeignKey(DiscountCode, on_delete=models.SET_NULL, null=True, blank=True,
                                              related_name="orders_applied_to", verbose_name="Applied Discount Code")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Discount Amount")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Total Amount")
    status = models.CharField(
        max_length=30, choices=ORDER_STATUS_CHOICES, default=STATUS_PENDING_PAYMENT, verbose_name="Order Status"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(blank=True, null=True, verbose_name="Paid At")

    def __str__(self):
        return f"Order {self.order_id} by {self.user.email if self.user else 'Anonymous'}"

    class Meta:
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering = ['-created_at']


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name="Order")
    limit_to_models_for_order = (
            models.Q(app_label='events', model='presentation')
            | models.Q(app_label='events', model='solocompetition')
            | models.Q(app_label='events', model='competitionteam')
            | models.Q(app_label='shop', model='product')
            | models.Q(app_label='shop', model='pack')
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True,
                                     limit_choices_to=limit_to_models_for_order, verbose_name="Item Type")
    object_id = models.PositiveIntegerField(verbose_name="Item ID", null=True, blank=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    description = models.CharField(max_length=255, verbose_name="Item Description (at time of order)")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Price (at time of order)")
    parent_pack = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='pack_components',
        verbose_name='Parent pack order item',
    )

    def __str__(self):
        return f"{self.description} for Order {self.order.order_id}"

    class Meta:
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"
        ordering = ['order']
        unique_together = ('order', 'content_type', 'object_id')


class DiscountRedemption(models.Model):
    code = models.ForeignKey(DiscountCode, on_delete=models.CASCADE, related_name='redemptions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='discount_redemptions')
    order = models.ForeignKey('shop.Order', on_delete=models.CASCADE, related_name='discount_redemptions')
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('code', 'user', 'order')
        indexes = [models.Index(fields=['code', 'user'])]


class PaymentApp(models.Model):
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Payment App"
        verbose_name_plural = "Payment Apps"

    def __str__(self):
        return self.name or self.slug
