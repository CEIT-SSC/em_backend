from unittest.mock import Mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from shop.eligibility import is_already_owned
from shop.models import Order, OrderItem

from .admin import PresentationEnrollmentAdmin
from .models import Event, Presentation, PresentationEnrollment


class PresentationEnrollmentAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='revoked-enrollment@example.com',
            password='test-password',
            is_active=True,
        )
        now = timezone.now()
        self.event = Event.objects.create(
            id=9101,
            title='Revocation Test Event',
            description='Test event',
            start_date=now,
            end_date=now,
            is_active=True,
            manager='Test Manager',
        )
        self.presentation = Presentation.objects.create(
            event=self.event,
            title='Revocation Test Presentation',
            description='Test presentation',
            start_time=now,
            end_time=now,
            is_active=True,
            is_paid=True,
            price='100.00',
        )
        self.order = Order.objects.create(
            user=self.user,
            event=self.event,
            subtotal_amount='100.00',
            discount_amount='0.00',
            total_amount='100.00',
            status=Order.STATUS_COMPLETED,
            paid_at=now,
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            content_object=self.presentation,
            description=str(self.presentation),
            price='100.00',
        )
        self.enrollment = PresentationEnrollment.objects.create(
            user=self.user,
            presentation=self.presentation,
            order_item=self.order_item,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
        )

    def test_revoke_action_preserves_purchase_and_removes_ownership(self):
        model_admin = PresentationEnrollmentAdmin(PresentationEnrollment, admin.site)
        model_admin.message_user = Mock()
        model_admin.log_change = Mock()
        request = RequestFactory().post('/admin/events/presentationenrollment/')

        model_admin.revoke_enrollments(
            request,
            PresentationEnrollment.objects.filter(pk=self.enrollment.pk),
        )

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, PresentationEnrollment.STATUS_CANCELLED)
        self.assertTrue(Order.objects.filter(pk=self.order.pk, status=Order.STATUS_COMPLETED).exists())
        self.assertTrue(OrderItem.objects.filter(pk=self.order_item.pk).exists())
        self.assertFalse(is_already_owned(self.user, self.presentation))
        model_admin.log_change.assert_called_once()

    def test_missing_enrollment_still_falls_back_to_completed_purchase(self):
        self.enrollment.delete()

        self.assertTrue(is_already_owned(self.user, self.presentation))

    def test_admin_disables_hard_delete(self):
        model_admin = PresentationEnrollmentAdmin(PresentationEnrollment, admin.site)

        self.assertFalse(model_admin.has_delete_permission(RequestFactory().get('/'), self.enrollment))
