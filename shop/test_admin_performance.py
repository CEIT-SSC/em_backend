from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from shop.models import Order, OrderItem


class OrderAdminPerformanceTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            email='order-admin@example.com',
            password='test-password',
        )
        self.client.force_login(self.admin)
        self.order = self._order()
        self.order_item = OrderItem.objects.create(
            order=self.order,
            description='Target item',
            price=Decimal('10.00'),
        )
        self.url = reverse('admin:shop_order_change', args=[self.order.pk])

    def _order(self):
        return Order.objects.create(
            user=self.admin,
            subtotal_amount=Decimal('10.00'),
            discount_amount=Decimal('0.00'),
            total_amount=Decimal('10.00'),
        )

    def _query_count(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
        return response, len(queries)

    def test_unrelated_order_items_do_not_expand_inline_queries_or_choices(self):
        _response, baseline_queries = self._query_count()
        for index in range(25):
            OrderItem.objects.create(
                order=self._order(),
                description=f'Unrelated item {index}',
                price=Decimal('10.00'),
            )

        response, expanded_queries = self._query_count()

        self.assertLessEqual(expanded_queries, baseline_queries + 5)
        self.assertNotContains(response, 'Unrelated item 24')
        self.assertContains(response, 'name="items-0-parent_pack"')
        self.assertContains(response, 'class="admin-autocomplete"')

    def test_parent_pack_remains_searchable_by_order_uuid(self):
        response = self.client.get(reverse('admin:autocomplete'), {
            'app_label': 'shop',
            'model_name': 'orderitem',
            'field_name': 'parent_pack',
            'term': str(self.order.order_id),
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            str(self.order_item.pk),
            {result['id'] for result in response.json()['results']},
        )
