from django.apps import apps
from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from django.core.exceptions import ValidationError
import uuid


class Product(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to='products/')
    features = models.JSONField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    event = models.ForeignKey('events.Event', on_delete=models.SET_NULL, null=True, blank=True, related_name="products")

    def __str__(self):
        return self.name


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

    def has_remaining_user_quota(self, user) -> bool:
        per_user_limit = getattr(self, 'max_uses_per_user', None)
        if not per_user_limit:
            return True

        DiscountRedemptionModel = apps.get_model('shop', 'DiscountRedemption')
        used = DiscountRedemptionModel.objects.filter(code=self, user=user).count()
        return used < per_user_limit

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        limit_choices_to=(
                models.Q(app_label='events', model='presentation') |
                models.Q(app_label='events', model='solocompetition') |
                models.Q(app_label='events', model='competitionteam') |
                models.Q(app_label='shop', model='product')
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
        PresentationModel = apps.get_model('events', 'Presentation')
        SoloCompetitionModel = apps.get_model('events', 'SoloCompetition')
        CompetitionTeamModel = apps.get_model('events', 'CompetitionTeam')
        ProductModel = apps.get_model('shop', 'Product')

        for ci in items:
            obj = ci.content_object
            if not obj:
                continue

            if hasattr(obj, 'is_paid') and not obj.is_paid:
                price = Decimal('0')
            elif isinstance(obj, PresentationModel) and obj.price is not None:
                price = obj.price
            elif isinstance(obj, SoloCompetitionModel) and obj.price_per_participant is not None:
                price = obj.price_per_participant
            elif isinstance(obj, CompetitionTeamModel):
                parent = obj.group_competition
                price = (
                    parent.price_per_group
                    if parent.is_paid and parent.price_per_group is not None
                    else Decimal('0')
                )
            elif isinstance(obj, ProductModel):
                price = obj.price
            else:
                price = Decimal('0')

            subtotal += price

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
        on_delete=models.SET_NULL,
        related_name='shop_cart_items',
        db_index=True
    )

    limit_to_models = (
            models.Q(app_label='events', model='presentation')
            | models.Q(app_label='events', model='solocompetition')
            | models.Q(app_label='events', model='competitionteam')
            | models.Q(app_label='shop', model='product')
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
            except Exception as e:
                print(f"[CartItem.save] content_object not available yet: {e}")
            if obj is not None:
                ev_id = getattr(obj, 'event_id', None)
                if ev_id is None:
                    parent = getattr(obj, 'group_competition', None)
                    ev_id = getattr(parent, 'event_id', None) if parent else None

            if ev_id:
                self.event_id = ev_id
            else:
                print(f"[CartItem.save] WARNING: could not resolve event_id; leaving NULL.")

        super().save(*args, **kwargs)


class Order(models.Model):
    STATUS_PENDING_PAYMENT = "pending_payment"
    STATUS_AWAITING_GATEWAY_REDIRECT = "awaiting_gateway_redirect"
    STATUS_PAYMENT_FAILED = "payment_failed"
    STATUS_PROCESSING_ENROLLMENT = "processing_enrollment"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_REFUND_PENDING = "refund_pending"
    STATUS_REFUNDED = "refunded"
    ORDER_STATUS_CHOICES = [
        (STATUS_PENDING_PAYMENT, "Pending Payment"), (STATUS_AWAITING_GATEWAY_REDIRECT, "Awaiting Gateway Redirect"),
        (STATUS_PAYMENT_FAILED, "Payment Failed"), (STATUS_PROCESSING_ENROLLMENT, "Processing Enrollment/Registration"),
        (STATUS_COMPLETED, "Completed"), (STATUS_CANCELLED, "Cancelled"),
        (STATUS_REFUND_PENDING, "Refund Pending"), (STATUS_REFUNDED, "Refunded"),
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
    status = models.CharField(max_length=30, choices=ORDER_STATUS_CHOICES, default=STATUS_PENDING_PAYMENT,
                              verbose_name="Order Status")
    payment_gateway_authority = models.CharField(max_length=50, blank=True, null=True, db_index=True,
                                                 verbose_name="Payment Gateway Authority (Zarinpal)")
    payment_gateway_txn_id = models.CharField(max_length=100, blank=True, null=True,
                                              verbose_name="Payment Gateway Transaction ID (Zarinpal ref_id)")
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(blank=True, null=True, verbose_name="Paid At")
    redirect_app = models.CharField(max_length=50, null=True, blank=True, db_index=True)

    def __str__(self):
        return f"Order {self.order_id} by {self.user.email if self.user else 'Anonymous'}"

    class Meta:
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering = ['-created_at']


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name="Order")
    limit_to_models_for_order = CartItem.limit_to_models
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True,
                                     limit_choices_to=limit_to_models_for_order, verbose_name="Item Type")
    object_id = models.PositiveIntegerField(verbose_name="Item ID", null=True, blank=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    description = models.CharField(max_length=255, verbose_name="Item Description (at time of order)")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Price (at time of order)")

    def __str__(self):
        return f"{self.description} for Order {self.order.order_id}"

    class Meta:
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"
        ordering = ['order']
        unique_together = ('order', 'content_type', 'object_id')


class PaymentBatch(models.Model):
    STATUS_PENDING = "pending"
    STATUS_AWAITING_GATEWAY_REDIRECT = "awaiting_gateway_redirect"
    STATUS_PAYMENT_FAILED = "payment_failed"
    STATUS_VERIFIED = "verified"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_AWAITING_GATEWAY_REDIRECT, "Awaiting Gateway Redirect"),
        (STATUS_PAYMENT_FAILED, "Payment Failed"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_COMPLETED, "Completed"),
    ]

    batch_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_batches")
    orders = models.ManyToManyField('shop.Order', related_name='batches')
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_PENDING)
    payment_gateway_authority = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    payment_gateway_txn_id = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    redirect_app = models.CharField(max_length=50, null=True, blank=True, db_index=True)

    def __str__(self):
        return f"Batch {self.batch_id} for {self.user_id} — {self.status}"


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