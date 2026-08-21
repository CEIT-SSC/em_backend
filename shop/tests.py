from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from events.models import CompetitionTeam, Event, GroupCompetition, TeamMembership

from .fulfillment import fulfill_order
from .models import CartItem, DiscountCode, DiscountRedemption, Order, OrderItem, Product


class ShopBusinessBehaviorTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='shop-tests@example.com',
            password='test-password',
            is_active=True,
        )
        self.client.force_authenticate(self.user)

    def _create_event(self):
        now = timezone.now()
        return Event.objects.create(
            id=9001,
            title='Test Event',
            description='Test event',
            start_date=now + timedelta(days=1),
            end_date=now + timedelta(days=2),
            is_active=True,
            manager='Test Manager',
        )

    def _create_group_competition(self, **overrides):
        now = timezone.now()
        values = {
            'event': self._create_event(),
            'title': 'Test Competition',
            'description': 'Test competition',
            'start_datetime': now + timedelta(days=1),
            'end_datetime': now + timedelta(days=2),
            'is_active': True,
            'is_paid': False,
            'price_per_member': Decimal('0'),
            'min_group_size': 1,
            'max_group_size': 3,
            'max_teams': 5,
            'requires_admin_approval': False,
        }
        values.update(overrides)
        return GroupCompetition.objects.create(**values)

    def test_fulfillment_is_idempotent_and_clears_only_fulfilled_cart_state(self):
        product = Product.objects.create(
            name='T-shirt',
            description='Event T-shirt',
            price=Decimal('100'),
            capacity=10,
        )
        discount = DiscountCode.objects.create(code='ONCE', amount=Decimal('100'))
        cart = self.user.cart
        cart.applied_discount_code = discount
        cart.save(update_fields=['applied_discount_code'])
        content_type = ContentType.objects.get_for_model(product)
        CartItem.objects.create(cart=cart, content_type=content_type, object_id=product.pk)
        order = Order.objects.create(
            user=self.user,
            subtotal_amount=Decimal('100'),
            discount_code_applied=discount,
            discount_amount=Decimal('100'),
            total_amount=Decimal('0'),
        )
        OrderItem.objects.create(
            order=order,
            content_type=content_type,
            object_id=product.pk,
            description=str(product),
            price=product.price,
        )

        fulfill_order(order)
        fulfill_order(order)

        order.refresh_from_db()
        discount.refresh_from_db()
        cart.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(discount.times_used, 1)
        self.assertEqual(DiscountRedemption.objects.filter(order=order).count(), 1)
        self.assertFalse(cart.items.filter(content_type=content_type, object_id=product.pk).exists())
        self.assertIsNone(cart.applied_discount_code)

    def test_checkout_rejects_an_item_that_has_reached_capacity(self):
        product = Product.objects.create(
            name='Sold out',
            description='No stock',
            price=Decimal('100'),
            capacity=0,
        )
        content_type = ContentType.objects.get_for_model(product)
        CartItem.objects.create(cart=self.user.cart, content_type=content_type, object_id=product.pk)

        response = self.client.post('/api/orders/checkout/', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Order.objects.count(), 0)

    def test_registering_for_a_free_team_competition_activates_the_team(self):
        competition = self._create_group_competition()
        team = CompetitionTeam.objects.create(name='Free Team', leader=self.user)
        TeamMembership.objects.create(
            team=team,
            user=self.user,
            status=TeamMembership.STATUS_ACCEPTED,
        )

        response = self.client.post(
            f'/api/my-teams/{team.pk}/register-competition/{competition.pk}/',
            {},
            format='json',
        )

        team.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(team.status, CompetitionTeam.STATUS_ACTIVE)

    def test_cancelling_an_order_releases_its_team_reservation(self):
        competition = self._create_group_competition(
            is_paid=True,
            price_per_member=Decimal('100'),
        )
        team = CompetitionTeam.objects.create(
            name='Reserved Team',
            leader=self.user,
            group_competition=competition,
            status=CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION,
        )
        TeamMembership.objects.create(
            team=team,
            user=self.user,
            status=TeamMembership.STATUS_ACCEPTED,
        )
        content_type = ContentType.objects.get_for_model(team)
        order = Order.objects.create(
            user=self.user,
            event=competition.event,
            subtotal_amount=Decimal('100'),
            discount_amount=Decimal('0'),
            total_amount=Decimal('100'),
        )
        OrderItem.objects.create(
            order=order,
            content_type=content_type,
            object_id=team.pk,
            description=str(team),
            price=Decimal('100'),
        )

        response = self.client.post(f'/api/orders/{order.order_id}/cancel/', {}, format='json')

        team.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(order.status, Order.STATUS_CANCELLED)
        self.assertEqual(team.status, CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT)
