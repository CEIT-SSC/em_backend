from django.core.management.base import BaseCommand
from django.db import transaction
from datetime import timedelta
from django.utils import timezone
from shop.models import Order
from shop.views import OrderCheckoutView, _release_reservations_for_orders
from shop.payments import ZarrinPal


STALE_AWAITING_MINUTES = 31


class Command(BaseCommand):
    help = "Verify Zarinpal unverified payments, and mark stale awaiting payments as failed via Inquiry."

    def handle(self, *args, **options):
        client = ZarrinPal()
        self._process_unverified(client)
        self._sweep_stale_awaiting(client, minutes=STALE_AWAITING_MINUTES)

    def _process_unverified(self, client: ZarrinPal):
        authorities = client.list_unverified() or []
        if not authorities:
            self.stdout.write("[UNVERIFIED] none found")

        for rec in authorities:
            authority = (rec or {}).get("authority")
            if not authority:
                continue

            qs = Order.objects.filter(payment_gateway_authority=authority)
            if not qs.exists():
                continue

            for order in qs:
                if order.status == Order.STATUS_COMPLETED:
                    continue

                try:
                    vr = client.verify_payment(authority=authority, amount=order.total_amount)
                except Exception as e:
                    self.stdout.write(f"[ORDER] Verify exception ({order.order_id}): {e}")
                    continue

                if vr.get("status") == "success":
                    with transaction.atomic():
                        order.status = Order.STATUS_PROCESSING_ENROLLMENT
                        order.payment_gateway_txn_id = vr.get("ref_id")
                        order.paid_at = timezone.now()
                        order.save(update_fields=["status", "payment_gateway_txn_id", "paid_at"])
                        OrderCheckoutView()._process_successful_order(order)
                    self.stdout.write(f"[ORDER] Verified & finalized: {order.order_id}")
                else:
                    order.status = Order.STATUS_PAYMENT_FAILED
                    order.save(update_fields=["status"])
                    _release_reservations_for_orders(order)
                    self.stdout.write(f"[ORDER] Verify failed: {order.order_id} -> {vr.get('error')}")

    def _sweep_stale_awaiting(self, client: ZarrinPal, *, minutes: int):
        cutoff = timezone.now() - timedelta(minutes=minutes)

        stale_orders = Order.objects.filter(
            status=Order.STATUS_AWAITING_GATEWAY_REDIRECT,
            payment_gateway_authority__isnull=False,
            created_at__lt=cutoff,
        )

        for order in stale_orders:
            try:
                iq = client.inquiry(authority=order.payment_gateway_authority or "")
            except Exception as e:
                self.stdout.write(f"[ORDER-INQ] {order.order_id} inquiry exception: {e}")
                continue

            status = (iq or {}).get("status")
            if status == "failed":
                order.status = Order.STATUS_PAYMENT_FAILED
                order.save(update_fields=["status"])
                _release_reservations_for_orders(order)
                self.stdout.write(f"[ORDER-INQ] FAILED → released: {order.order_id}")
            elif status == "in_bank":
                self.stdout.write(f"[ORDER-INQ] Still in bank: {order.order_id}")
            else:
                self.stdout.write(f"[ORDER-INQ] {order.order_id} → {status} ({(iq or {}).get('error')})")
