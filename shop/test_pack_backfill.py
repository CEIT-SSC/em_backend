from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from events.models import Event, Presentation, PresentationEnrollment
from shop.fulfillment import grant_current_pack_items_to_previous_purchasers
from shop.models import Order, OrderItem, Pack, PackItem


class PackBackfillTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(
            id=9101,
            title='Backfill event',
            description='Backfill event',
            start_date=now + timedelta(days=1),
            end_date=now + timedelta(days=2),
            is_active=True,
            manager='Test Manager',
        )
        self.presentation = Presentation.objects.create(
            event=self.event,
            title='New pack presentation',
            description='Added after the pack was sold',
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=2),
            is_active=True,
            is_paid=True,
            price=Decimal('100'),
        )
        self.pack = Pack.objects.create(
            name='Historical pack',
            real_price=Decimal('250'),
            event=self.event,
        )
        self.pack_type = ContentType.objects.get_for_model(Pack)
        self.presentation_type = ContentType.objects.get_for_model(Presentation)
        PackItem.objects.create(
            pack=self.pack,
            content_type=self.presentation_type,
            object_id=self.presentation.pk,
        )

    def _completed_pack_order(self, email):
        user = get_user_model().objects.create_user(
            email=email,
            password='test-password',
            is_active=True,
        )
        order = Order.objects.create(
            user=user,
            event=self.event,
            subtotal_amount=Decimal('250'),
            discount_amount=Decimal('0'),
            total_amount=Decimal('250'),
            status=Order.STATUS_COMPLETED,
        )
        pack_order_item = OrderItem.objects.create(
            order=order,
            content_type=self.pack_type,
            object_id=self.pack.pk,
            description=self.pack.name,
            price=self.pack.real_price,
        )
        return user, order, pack_order_item

    def test_backfill_skips_existing_recipient_and_grants_everyone_else(self):
        existing_user, _existing_order, existing_pack_item = self._completed_pack_order(
            'already-has-it@example.com'
        )
        missing_user, missing_order, missing_pack_item = self._completed_pack_order(
            'needs-it@example.com'
        )
        PresentationEnrollment.objects.create(
            user=existing_user,
            presentation=self.presentation,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
            # This reproduces the old manual linkage that can only be used once.
            order_item=existing_pack_item,
        )

        result = grant_current_pack_items_to_previous_purchasers(self.pack)

        self.assertEqual(result.purchasers, 2)
        self.assertEqual(result.granted, 1)
        self.assertEqual(result.already_owned, 1)
        enrollment = PresentationEnrollment.objects.get(
            user=missing_user,
            presentation=self.presentation,
        )
        self.assertEqual(enrollment.status, PresentationEnrollment.STATUS_COMPLETED_OR_FREE)
        self.assertEqual(enrollment.order_item.order, missing_order)
        self.assertEqual(enrollment.order_item.parent_pack, missing_pack_item)
        self.assertEqual(enrollment.order_item.price, Decimal('0'))

    def test_backfill_is_repeatable_and_deduplicates_multiple_pack_orders(self):
        user, _order, _pack_item = self._completed_pack_order('repeat@example.com')
        duplicate_order = Order.objects.create(
            user=user,
            event=self.event,
            subtotal_amount=Decimal('250'),
            discount_amount=Decimal('0'),
            total_amount=Decimal('250'),
            status=Order.STATUS_COMPLETED,
        )
        OrderItem.objects.create(
            order=duplicate_order,
            content_type=self.pack_type,
            object_id=self.pack.pk,
            description=self.pack.name,
            price=self.pack.real_price,
        )

        first = grant_current_pack_items_to_previous_purchasers(self.pack)
        second = grant_current_pack_items_to_previous_purchasers(self.pack)

        self.assertEqual(first.purchasers, 1)
        self.assertEqual(first.granted, 1)
        self.assertEqual(second.purchasers, 1)
        self.assertEqual(second.granted, 0)
        self.assertEqual(second.already_owned, 1)
        self.assertEqual(
            PresentationEnrollment.objects.filter(
                user=user,
                presentation=self.presentation,
            ).count(),
            1,
        )

    def test_backfill_reactivates_cancelled_enrollment(self):
        user, _order, _pack_item = self._completed_pack_order('cancelled@example.com')
        enrollment = PresentationEnrollment.objects.create(
            user=user,
            presentation=self.presentation,
            status=PresentationEnrollment.STATUS_CANCELLED,
        )

        result = grant_current_pack_items_to_previous_purchasers(self.pack)

        enrollment.refresh_from_db()
        self.assertEqual(result.reactivated, 1)
        self.assertEqual(enrollment.status, PresentationEnrollment.STATUS_COMPLETED_OR_FREE)
        self.assertIsNotNone(enrollment.order_item_id)
