from django.apps import apps
from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.utils import timezone
import uuid


class DiscountCode(models.Model):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"
    DISCOUNT_TYPE_CHOICES = [(PERCENTAGE, "Percentage Discount"), (FIXED_AMOUNT, "Fixed Amount Discount")]

    code = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="Discount Code")
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, verbose_name="Discount Type")
    value = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Value")
    is_active = models.BooleanField(default=True, verbose_name="Is Active?")
    valid_from = models.DateTimeField(blank=True, null=True, verbose_name="Valid From")
    valid_to = models.DateTimeField(blank=True, null=True, verbose_name="Valid To")
    max_uses = models.PositiveIntegerField(blank=True, null=True, verbose_name="Maximum Uses")
    times_used = models.PositiveIntegerField(default=0, verbose_name="Times Used")
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, blank=True, null=True, verbose_name="Minimum Order Value")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.code

    def is_valid(self, current_subtotal=None):
        if not self.is_active: return False
        now = timezone.now()
        if self.valid_from and now < self.valid_from: return False
        if self.valid_to and now > self.valid_to: return False
        if self.max_uses is not None and self.times_used >= self.max_uses: return False
        if current_subtotal is not None and self.min_order_value is not None and current_subtotal < self.min_order_value: return False
        return True

    def calculate_discount(self, amount_to_discount):
        if not self.is_valid(amount_to_discount): return 0
        discount_value = (amount_to_discount * self.value) / 100 if self.discount_type == self.PERCENTAGE else self.value
        return min(discount_value, amount_to_discount)

    class Meta:
        verbose_name = "Discount Code"
        verbose_name_plural = "Discount Codes"
        ordering = ['-created_at', 'code']

class Cart(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart", verbose_name="User")
    applied_discount_code = models.ForeignKey(DiscountCode, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Applied Discount Code")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Cart for {self.user.email}"

    def get_subtotal(self):
        subtotal = 0
        PresentationModel = apps.get_model('events', 'Presentation')
        SoloCompetitionModel = apps.get_model('events', 'SoloCompetition')
        CompetitionTeamModel = apps.get_model('events', 'CompetitionTeam')

        for item in self.items.all():
            price = 0
            content_object = item.content_object
            if content_object:
                if hasattr(content_object, 'is_paid') and not content_object.is_paid: price = 0
                elif isinstance(content_object, PresentationModel) and content_object.price is not None: price = content_object.price
                elif isinstance(content_object, SoloCompetitionModel) and content_object.price_per_participant is not None: price = content_object.price_per_participant
                elif isinstance(content_object, CompetitionTeamModel):
                    parent_comp = content_object.group_competition
                    if parent_comp.is_paid and parent_comp.price_per_group is not None: price = parent_comp.price_per_group
                    else: price = 0
            subtotal += price
        return subtotal

    def get_discount_amount(self):
        subtotal = self.get_subtotal()
        if self.applied_discount_code and self.applied_discount_code.is_valid(subtotal):
            return self.applied_discount_code.calculate_discount(subtotal)
        return 0

    def get_total(self):
        return self.get_subtotal() - self.get_discount_amount()

    class Meta:
        verbose_name = "Shopping Cart"
        verbose_name_plural = "Shopping Carts"

class CartItem(models.Model):
    STATUS_OWNED = "owned"
    STATUS_RESERVED = "reserved"
    STATUS_CHOICES = (
        (STATUS_OWNED, "owned"),
        (STATUS_RESERVED, "reserved"),
    )

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
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        limit_choices_to=limit_to_models,
        verbose_name="Item Type"
    )
    object_id = models.PositiveIntegerField(verbose_name="Item ID")
    content_object = GenericForeignKey('content_type', 'object_id')

    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_OWNED
    )

    reserved_order = models.ForeignKey(
        'shop.Order', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reserved_cart_items'
    )
    reserved_order_item = models.ForeignKey(
        'shop.OrderItem', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reserved_cart_items'
    )

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

        if not self.status:
            self.status = self.STATUS_OWNED

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

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders", verbose_name="User")
    order_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True, verbose_name="Order ID")
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Subtotal Amount")
    discount_code_applied = models.ForeignKey(DiscountCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders_applied_to", verbose_name="Applied Discount Code")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Discount Amount")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Total Amount")
    status = models.CharField(max_length=30, choices=ORDER_STATUS_CHOICES, default=STATUS_PENDING_PAYMENT, verbose_name="Order Status")
    payment_gateway_authority = models.CharField(max_length=50, blank=True, null=True, db_index=True, verbose_name="Payment Gateway Authority (Zarinpal)")
    payment_gateway_txn_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="Payment Gateway Transaction ID (Zarinpal ref_id)")
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
    limit_to_models_for_order = CartItem.limit_to_models
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, limit_choices_to=limit_to_models_for_order, verbose_name="Item Type")
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

    def __str__(self):
        return f"Batch {self.batch_id} for {self.user_id} — {self.status}"