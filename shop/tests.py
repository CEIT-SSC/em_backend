from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from events.models import CompetitionTeam, Event, GroupCompetition, TeamMembership

from .fulfillment import fulfill_order
from .models import CartItem, DiscountCode, DiscountRedemption, Order, OrderItem, Product
from wallet.models import WalletEntry, WalletTopUp
from wallet.services import WalletService
from wallet.tests.helpers import FakePaymentClient, credit


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

    def _create_payable_team(self, *, competition=None, team_status=None, accepted=(), rejected=()):
        competition = competition or self._create_group_competition(
            is_paid=True,
            price_per_member=Decimal('100'),
            max_group_size=5,
        )
        team = CompetitionTeam.objects.create(
            name=f'Paid Team {CompetitionTeam.objects.count() + 1}',
            leader=self.user,
            group_competition=competition,
            status=team_status or CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT,
        )
        TeamMembership.objects.create(
            team=team,
            user=self.user,
            status=TeamMembership.STATUS_ACCEPTED,
        )
        for member in accepted:
            TeamMembership.objects.create(
                team=team,
                user=member,
                status=TeamMembership.STATUS_ACCEPTED,
            )
        for member in rejected:
            TeamMembership.objects.create(
                team=team,
                user=member,
                status=TeamMembership.STATUS_REJECTED,
            )
        return team

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

    def test_paid_team_checkout_uses_wallet_charges_accepted_members_and_replays_safely(self):
        accepted_member = get_user_model().objects.create_user(
            email='accepted-team-member@example.com',
            password='test-password',
            is_active=True,
        )
        rejected_member = get_user_model().objects.create_user(
            email='rejected-team-member@example.com',
            password='test-password',
            is_active=True,
        )
        team = self._create_payable_team(
            accepted=(accepted_member,),
            rejected=(rejected_member,),
        )
        credit(self.user, '250.00', 'team-wallet-checkout', actor=self.user)

        first = self.client.post(f'/api/teams/{team.pk}/initiate-payment/', {}, format='json')
        replay = self.client.post(f'/api/teams/{team.pk}/initiate-payment/', {}, format='json')

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertFalse(first.data['payment_required'])
        self.assertEqual(Decimal(str(first.data['order']['total_amount'])), Decimal('200.00'))
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(replay.data['order']['order_id'], first.data['order']['order_id'])
        team.refresh_from_db()
        order = Order.objects.get(order_id=first.data['order']['order_id'])
        self.assertEqual(team.status, CompetitionTeam.STATUS_ACTIVE)
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('50.00'))
        self.assertEqual(
            WalletEntry.objects.filter(
                order=order,
                entry_type=WalletEntry.TYPE_PURCHASE,
            ).count(),
            1,
        )

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    @patch('wallet.services.ZarrinPal')
    def test_paid_team_checkout_returns_topup_and_callback_activates_team(self, mock_client_cls):
        fake = FakePaymentClient()
        mock_client_cls.return_value = fake
        team = self._create_payable_team()

        response = self.client.post(
            f'/api/teams/{team.pk}/initiate-payment/',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['payment_required'])
        self.assertEqual(response.data['payment_url'], fake.create_result['link'])
        topup = WalletTopUp.objects.get(public_id=response.data['topup_id'])
        order = topup.order
        self.assertEqual(topup.amount, Decimal('100.00'))
        team.refresh_from_db()
        self.assertEqual(team.status, CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION)
        self.assertEqual(order.status, Order.STATUS_PENDING_PAYMENT)

        callback = self.client.get('/api/wallet/top-ups/callback/', {
            'Authority': topup.gateway_authority,
            'Status': 'OK',
        })

        self.assertEqual(callback.status_code, status.HTTP_302_FOUND)
        team.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(team.status, CompetitionTeam.STATUS_ACTIVE)
        self.assertEqual(order.status, Order.STATUS_COMPLETED)
        self.assertEqual(WalletService.get_balance(self.user), Decimal('0.00'))

    @override_settings(WALLET_PAYMENT_CALLBACK_URL='https://example.com/api/wallet/top-ups/callback/')
    @patch('wallet.services.ZarrinPal')
    def test_retrying_team_checkout_reuses_order_and_creates_a_fresh_topup(self, mock_client_cls):
        fake = FakePaymentClient()
        mock_client_cls.return_value = fake
        team = self._create_payable_team()

        first = self.client.post(f'/api/teams/{team.pk}/initiate-payment/', {}, format='json')
        second = self.client.post(f'/api/teams/{team.pk}/initiate-payment/', {}, format='json')

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data['order']['order_id'], second.data['order']['order_id'])
        self.assertNotEqual(first.data['topup_id'], second.data['topup_id'])
        self.assertEqual(Order.objects.filter(user=self.user).count(), 1)
        self.assertEqual(WalletTopUp.objects.filter(order__user=self.user).count(), 2)
        team.refresh_from_db()
        self.assertEqual(team.status, CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION)

    def test_only_the_team_leader_can_start_team_payment(self):
        team = self._create_payable_team()
        other_user = get_user_model().objects.create_user(
            email='not-team-leader@example.com',
            password='test-password',
            is_active=True,
        )
        self.client.force_authenticate(other_user)

        response = self.client.post(f'/api/teams/{team.pk}/initiate-payment/', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(WalletTopUp.objects.exists())

    def test_team_payment_rejects_a_team_that_is_not_approved(self):
        team = self._create_payable_team(team_status=CompetitionTeam.STATUS_FORMING)

        response = self.client.post(f'/api/teams/{team.pk}/initiate-payment/', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Order.objects.exists())
        team.refresh_from_db()
        self.assertEqual(team.status, CompetitionTeam.STATUS_FORMING)
