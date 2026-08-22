from decimal import Decimal

from django.contrib.auth import get_user_model

from shop.models import Order
from wallet.models import WalletEntry
from wallet.services import WalletService

User = get_user_model()


def make_user(email, *, staff=False, superuser=False):
    if superuser:
        return User.objects.create_superuser(email=email, password='pass12345')
    user = User.objects.create_user(email=email, password='pass12345', is_active=True)
    if staff:
        user.is_staff = True
        user.save(update_fields=['is_staff'])
    return user


def make_order(user, amount, *, status=Order.STATUS_PENDING_PAYMENT):
    money = Decimal(str(amount))
    return Order.objects.create(
        user=user,
        subtotal_amount=money,
        total_amount=money,
        status=status,
    )


def credit(user, amount, key, actor=None):
    actor = actor or user
    if not actor.is_staff:
        actor.is_staff = True
        actor.save(update_fields=['is_staff'])
    entry, created = WalletService.admin_adjust(
        user=user,
        amount=amount,
        direction=WalletEntry.DIRECTION_CREDIT,
        reason='Test credit seed',
        actor=actor,
        idempotency_key=key,
    )
    return entry, created
