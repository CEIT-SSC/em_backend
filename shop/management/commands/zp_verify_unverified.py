from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from shop.models import Order
from shop.views import OrderCheckoutView
from shop.payments import ZarrinPal


class Command(BaseCommand):
    help = "Verify Zarinpal unverified payments and finalize orders."

    def handle(self, *args, **options):
        client = ZarrinPal()
        authorities = client.list_unverified()
        if not authorities:
            self.stdout.write("No unverified authorities.")
            return
        
        for item in authorities:
            authority = item.get("authority")
            if not authority:
                continue

            try:
                order = Order.objects.get(payment_gateway_authority=authority)
            except Order.DoesNotExist:
                continue

            if order.status in [Order.STATUS_COMPLETED]:
                continue

            if order.status not in [
                Order.STATUS_AWAITING_GATEWAY_REDIRECT,
                Order.STATUS_PENDING_PAYMENT,
                Order.STATUS_PAYMENT_FAILED,
            ]:
                continue

            result = client.verify_payment(authority=authority, amount=order.total_amount)

            if result.get("status") == "success":
                with transaction.atomic():
                    order.status = Order.STATUS_PROCESSING_ENROLLMENT
                    order.payment_gateway_txn_id = result.get("ref_id")
                    order.paid_at = timezone.now()
                    order.save(update_fields=["status", "payment_gateway_txn_id", "paid_at"])
                    OrderCheckoutView()._process_successful_order(order)
                self.stdout.write(f"Verified & finalized order {order.order_id}")
            else:
                reason = result.get("error") or "verify_failed"
                with transaction.atomic():
                    order.status = Order.STATUS_PAYMENT_FAILED
                    order.save(update_fields=["status"])
                self.stdout.write(f"Verify failed for {order.order_id}: {reason}")