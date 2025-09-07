from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from shop.models import Order, PaymentBatch
from shop.views import OrderCheckoutView
from shop.payments import ZarrinPal

class Command(BaseCommand):
    help = "Verify Zarinpal unverified payments for single orders and batches."

    def handle(self, *args, **options):
        client = ZarrinPal()
        authorities = client.list_unverified() or []
        if not authorities:
            self.stdout.write("No unverified authorities.")
            return

        for rec in authorities:
            authority = rec.get("authority")
            if not authority:
                continue

            batch = PaymentBatch.objects.filter(payment_gateway_authority=authority).first()
            if batch:
                if batch.status == PaymentBatch.STATUS_COMPLETED:
                    continue
                vr = client.verify_payment(authority=authority, amount=batch.total_amount)
                if vr.get("status") == "success":
                    with transaction.atomic():
                        batch.status = PaymentBatch.STATUS_VERIFIED
                        batch.payment_gateway_txn_id = vr.get("ref_id")
                        batch.paid_at = timezone.now()
                        batch.save(update_fields=["status", "payment_gateway_txn_id", "paid_at"])

                        member_orders = list(batch.orders.all())
                        for o in member_orders:
                            o.payment_gateway_txn_id = vr.get("ref_id")
                            o.paid_at = batch.paid_at
                            o.save(update_fields=["payment_gateway_txn_id", "paid_at"])
                            OrderCheckoutView()._process_successful_order(o)

                        batch.status = PaymentBatch.STATUS_COMPLETED
                        batch.save(update_fields=["status"])
                    self.stdout.write(f"[BATCH] Verified & finalized: {batch.batch_id}")
                else:
                    batch.status = PaymentBatch.STATUS_PAYMENT_FAILED
                    batch.save(update_fields=["status"])
                    batch.orders.update(status=Order.STATUS_PAYMENT_FAILED)
                    self.stdout.write(f"[BATCH] Verify failed: {batch.batch_id} -> {vr.get('error')}")
                continue

            qs = Order.objects.filter(payment_gateway_authority=authority)
            if not qs.exists():
                continue
            for order in qs:
                if order.status == Order.STATUS_COMPLETED:
                    continue
                vr = client.verify_payment(authority=authority, amount=order.total_amount)
                if vr.get("status") == "success":
                    with transaction.atomic():
                        order.status = Order.STATUS_PROCESSING_ENROLLMENT
                        order.payment_gateway_txn_id = vr.get('ref_id')
                        order.paid_at = timezone.now()
                        order.save(update_fields=["status", "payment_gateway_txn_id", "paid_at"])
                        OrderCheckoutView()._process_successful_order(order)
                    self.stdout.write(f"[ORDER] Verified & finalized: {order.order_id}")
                else:
                    order.status = Order.STATUS_PAYMENT_FAILED
                    order.save(update_fields=["status"])
                    self.stdout.write(f"[ORDER] Verify failed: {order.order_id} -> {vr.get('error')}")