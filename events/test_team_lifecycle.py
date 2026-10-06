import threading
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from payment_core.models import PaymentIntent
from shop.fulfillment import fulfill_order, release_order_reservations, OrderFulfillmentError
from shop.models import Cart, CartItem, Order, OrderItem
from shop.pricing import get_item_price
from shop.team_checkout import prepare_team_order
from wallet.services import WalletService
from wallet.tests.helpers import credit

from .fulfillment import activate_team_registration
from .models import (CompetitionTeam, CompetitionTeamRegistration as Registration,
                     CompetitionRegistrationMember, GroupCompetition, SoloCompetition,
                     SoloCompetitionRegistration, TeamMembership)
from .services import (CompetitionError, cancel_registration, delete_team, invite_member,
                       register_team, register_free_solo, reserve_solo_order_items,
                       respond_to_invitation, review_registration)


class LifecycleFixtures:
    def user(self, email=None, **kwargs):
        return get_user_model().objects.create_user(
            email=email or f'user-{get_user_model().objects.count()}@example.com',
            is_active=True, **kwargs)

    def competition(self, **kwargs):
        return GroupCompetition.objects.create(title='Team competition', description='Test',
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=2), max_group_size=5, **kwargs)

    def solo(self, **kwargs):
        return SoloCompetition.objects.create(title='Solo competition', description='Test',
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=2), **kwargs)

    def team(self, leader=None, members=()):
        leader = leader or self.user()
        team = CompetitionTeam.objects.create(name=f'Team {CompetitionTeam.objects.count()}', leader=leader)
        for user in (leader, *members):
            TeamMembership.objects.create(team=team, user=user, status='accepted', expires_at=None)
        return team

    def solo_order(self, competition, user):
        order = Order.objects.create(user=user, subtotal_amount=100, total_amount=100)
        OrderItem.objects.create(order=order, content_object=competition, price=100, description='Solo')
        return order


class TeamLifecycleTests(LifecycleFixtures, TestCase):
    def setUp(self):
        self.leader = self.user()
        self.member = self.user()
        self.staff = self.user(is_staff=True)
        self.team_record = self.team(self.leader)
        self.client = APIClient()
        self.client.force_authenticate(self.leader)

    def register(self, competition):
        return register_team(self.team_record.pk, competition.pk, self.leader)

    def test_create_team_without_invitations(self):
        response = self.client.post('/api/my-teams/', {'team_name': 'Empty invites'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['accepted_member_count'], 1)
        self.assertEqual(response.data['registrations'], [])

    def test_duplicate_invitation_and_membership_are_prevented(self):
        membership = invite_member(self.team_record.pk, self.leader, self.member.email)
        self.assertEqual(membership.invited_by, self.leader)
        self.assertGreater(membership.expires_at, timezone.now())
        with self.assertRaises(CompetitionError):
            invite_member(self.team_record.pk, self.leader, self.member.email.upper())
        with self.assertRaises(IntegrityError), transaction.atomic():
            TeamMembership.objects.create(team=self.team_record, user=self.member)

    def test_only_leader_can_invite(self):
        with self.assertRaises(CompetitionError) as error:
            invite_member(self.team_record.pk, self.member, self.member.email)
        self.assertEqual(error.exception.status_code, 403)

    def test_accept_reject_and_reinvite_preserve_history(self):
        membership = invite_member(self.team_record.pk, self.leader, self.member.email)
        rejected = respond_to_invitation(self.team_record.pk, self.member, 'reject')
        self.assertEqual(rejected.pk, membership.pk)
        self.assertEqual(rejected.status, 'rejected')
        self.assertIsNotNone(rejected.responded_at)
        self.assertEqual(invite_member(self.team_record.pk, self.leader, self.member.email).pk, membership.pk)
        accepted = respond_to_invitation(self.team_record.pk, self.member, 'accept')
        self.assertEqual(accepted.status, 'accepted')
        with self.assertRaises(CompetitionError):
            respond_to_invitation(self.team_record.pk, self.member, 'reject')

    def test_expired_invitation_cannot_be_accepted_and_is_not_listed(self):
        membership = invite_member(self.team_record.pk, self.leader, self.member.email)
        TeamMembership.objects.filter(pk=membership.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get('/api/my-invitations/').data['count'], 0)
        response = self.client.post(f'/api/my-invitations/{self.team_record.pk}/respond/', {'action': 'accept'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'invitation_expired')
        membership.refresh_from_db()
        self.assertEqual(membership.status, 'expired')
        self.assertIsNotNone(membership.responded_at)

    def test_expiration_command(self):
        membership = invite_member(self.team_record.pk, self.leader, self.member.email)
        TeamMembership.objects.filter(pk=membership.pk).update(expires_at=timezone.now() - timedelta(days=1))
        call_command('expire_team_invitations', stdout=StringIO())
        membership.refresh_from_db()
        self.assertEqual(membership.status, 'expired')

    def test_pending_and_rejected_members_do_not_count_toward_roster_or_price(self):
        invite_member(self.team_record.pk, self.leader, self.member.email)
        TeamMembership.objects.create(team=self.team_record, user=self.user(), status='rejected')
        competition = self.competition(is_paid=True, price_per_member=100)
        registration = self.register(competition)
        self.assertEqual(registration.price, Decimal('100'))
        self.assertEqual(list(registration.members.values_list('user_id', flat=True)), [self.leader.pk])
        self.team_record.refresh_from_db()
        self.assertEqual(get_item_price(self.team_record), Decimal('100'))
        respond_to_invitation(self.team_record.pk, self.member, 'accept')
        self.assertEqual(registration.members.count(), 1)
        self.assertEqual(get_item_price(registration), Decimal('100'))

    def test_same_team_can_register_in_multiple_competitions(self):
        first = self.register(self.competition())
        second = self.register(self.competition(is_paid=True, price_per_member=30))
        self.assertEqual(first.status, Registration.ACTIVE)
        self.assertEqual(second.status, Registration.PENDING_PAYMENT)
        response = self.client.get(f'/api/my-teams/{self.team_record.pk}/')
        self.assertEqual(len(response.data['registrations']), 2)
        ambiguous = self.client.post(f'/api/teams/{self.team_record.pk}/initiate-payment/', {}, format='json')
        self.assertEqual(ambiguous.status_code, 400)
        self.assertEqual(ambiguous.data['code'], 'competition_required')

    def test_one_registration_per_team_and_competition(self):
        competition = self.competition()
        self.register(competition)
        with self.assertRaises(CompetitionError):
            self.register(competition)
        self.assertEqual(competition.registrations.count(), 1)

    def test_conflicts_apply_to_accepted_roster_in_same_competition_only(self):
        competition = self.competition()
        TeamMembership.objects.create(team=self.team_record, user=self.member, status='accepted')
        self.register(competition)
        another = self.team(self.member)
        with self.assertRaises(CompetitionError) as error:
            register_team(another.pk, competition.pk, self.member)
        self.assertEqual(error.exception.detail['code'], 'membership_conflict')
        register_team(another.pk, self.competition().pk, self.member)

    def test_pending_invitation_does_not_conflict(self):
        competition = self.competition()
        invite_member(self.team_record.pk, self.leader, self.member.email)
        self.register(competition)
        another = self.team(self.member)
        register_team(another.pk, competition.pk, self.member)

    def test_capacity_includes_pending_approval_and_payment(self):
        competition = self.competition(max_teams=1, requires_admin_approval=True, is_paid=True, price_per_member=100)
        registration = self.register(competition)
        another = self.team(self.member)
        with self.assertRaises(CompetitionError):
            register_team(another.pk, competition.pk, self.member)
        review_registration(registration.pk, self.staff, approve=True)
        order = prepare_team_order(registration.pk, self.leader)
        self.assertEqual(prepare_team_order(registration.pk, self.leader).pk, order.pk)
        with self.assertRaises(CompetitionError):
            register_team(another.pk, competition.pk, self.member)
        remaining = self.client.get(f'/api/group-competitions/{competition.pk}/').data['remaining_capacity']
        self.assertEqual(remaining, 0)

    def test_approval_and_rejection_are_audited_and_transition_checked(self):
        registration = self.register(self.competition(requires_admin_approval=True))
        with self.assertRaises(CompetitionError):
            review_registration(registration.pk, self.leader, approve=True)
        approved = review_registration(registration.pk, self.staff, approve=True, remarks='Roster verified')
        self.assertEqual(approved.status, Registration.ACTIVE)
        self.assertEqual(approved.reviewed_by, self.staff)
        self.assertIsNotNone(approved.reviewed_at)
        self.assertEqual(approved.admin_remarks, 'Roster verified')
        with self.assertRaises(CompetitionError):
            review_registration(registration.pk, self.staff, approve=False)
        with self.assertRaises(CompetitionError):
            cancel_registration(registration.pk, self.leader)

    def test_rejection_releases_capacity_and_member_conflicts(self):
        competition = self.competition(requires_admin_approval=True, max_teams=1)
        registration = self.register(competition)
        rejected = review_registration(registration.pk, self.staff, approve=False, remarks='Invalid entry')
        self.assertEqual(rejected.status, Registration.REJECTED)
        self.assertFalse(registration.members.filter(reserved=True).exists())
        another = self.team(self.leader)
        register_team(another.pk, competition.pk, self.leader)

    def test_leader_only_registration_and_cancellation(self):
        competition = self.competition(requires_admin_approval=True)
        with self.assertRaises(CompetitionError):
            register_team(self.team_record.pk, competition.pk, self.member)
        registration = self.register(competition)
        with self.assertRaises(CompetitionError):
            cancel_registration(registration.pk, self.member)
        cancelled = cancel_registration(registration.pk, self.leader)
        self.assertEqual(cancelled.status, Registration.CANCELLED)
        self.assertFalse(cancelled.members.filter(reserved=True).exists())

    def test_closed_and_invalid_size_return_controlled_errors(self):
        for competition in (self.competition(is_active=False), self.competition(min_group_size=2)):
            response = self.client.post(f'/api/my-teams/{self.team_record.pk}/register-competition/{competition.pk}/')
            self.assertEqual(response.status_code, 400)
            self.assertIn('code', response.data)
        response = self.client.post(f'/api/my-teams/{self.team_record.pk}/register-competition/not-an-id/')
        self.assertEqual(response.status_code, 400)

    def test_free_registration_has_no_order_or_gateway_intent(self):
        registration = self.register(self.competition())
        self.assertEqual(registration.status, Registration.ACTIVE)
        self.assertIsNone(registration.order_item)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(PaymentIntent.objects.exists())

    def test_two_competition_payments_are_independent_and_idempotent(self):
        registrations = [self.register(self.competition(is_paid=True, price_per_member=100)) for _ in range(2)]
        credit(self.leader, '200', 'multi-team-payment', actor=self.staff)
        for registration in registrations:
            response = self.client.post(f'/api/teams/{self.team_record.pk}/initiate-payment/',
                {'competition_id': registration.competition_id}, format='json')
            self.assertEqual(response.status_code, 200)
            registration.refresh_from_db()
            self.assertEqual(registration.status, Registration.ACTIVE)
            activated_at = registration.activated_at
            fulfilled, created = fulfill_order(registration.order_item.order)
            self.assertFalse(created)
            self.assertFalse(activate_team_registration(registration.pk, registration.order_item)[1])
            registration.refresh_from_db()
            self.assertEqual(registration.activated_at, activated_at)
        self.assertEqual(WalletService.get_balance(self.leader), Decimal('0'))
        self.assertEqual(Order.objects.count(), 2)

    def test_payment_cannot_activate_pending_approval_or_unsettled_order(self):
        pending = self.register(self.competition(requires_admin_approval=True, is_paid=True, price_per_member=100))
        with self.assertRaises(CompetitionError):
            prepare_team_order(pending.pk, self.leader)
        registration = self.register(self.competition(is_paid=True, price_per_member=100))
        order = prepare_team_order(registration.pk, self.leader)
        with self.assertRaises(OrderFulfillmentError):
            fulfill_order(order)
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.PENDING_PAYMENT)

    def test_cancellation_releases_order_and_late_settlement_is_rejected(self):
        registration = self.register(self.competition(is_paid=True, price_per_member=100))
        order = prepare_team_order(registration.pk, self.leader)
        item = order.items.get()
        cancel_registration(registration.pk, self.leader)
        order.refresh_from_db()
        registration.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_CANCELLED)
        self.assertIsNone(registration.order_item_id)
        self.assertFalse(registration.members.filter(reserved=True).exists())
        with self.assertRaises(CompetitionError):
            activate_team_registration(registration.pk, item)

    def test_paid_order_cannot_be_cancelled_or_rejected(self):
        registration = self.register(self.competition(is_paid=True, price_per_member=100))
        order = prepare_team_order(registration.pk, self.leader)
        order.status = Order.STATUS_COMPLETED
        order.paid_at = timezone.now()
        order.save(update_fields=['status', 'paid_at'])
        with self.assertRaises(CompetitionError):
            cancel_registration(registration.pk, self.leader)
        with self.assertRaises(CompetitionError):
            review_registration(registration.pk, self.staff, approve=False)
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.PENDING_PAYMENT)
        self.assertEqual(registration.order_item.order.status, Order.STATUS_COMPLETED)

    def test_leader_can_cancel_unpaid_checkout_through_api(self):
        competition = self.competition(is_paid=True, price_per_member=100, max_teams=1)
        registration = self.register(competition)
        order = prepare_team_order(registration.pk, self.leader)
        response = self.client.post(
            f'/api/my-teams/{self.team_record.pk}/cancel-registration/{competition.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], Registration.CANCELLED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_CANCELLED)
        self.assertEqual(self.client.get(f'/api/group-competitions/{competition.pk}/').data['remaining_capacity'], 1)

    def test_staff_can_reject_approved_unpaid_registration_through_api(self):
        competition = self.competition(is_paid=True, price_per_member=100, max_teams=1)
        registration = self.register(competition)
        order = prepare_team_order(registration.pk, self.leader)
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            f'/api/my-teams/{self.team_record.pk}/review-registration/{competition.pk}/',
            {'approve': False, 'remarks': 'Withdrawn by admin'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], Registration.REJECTED)
        registration.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_CANCELLED)
        self.assertIsNone(registration.order_item_id)
        self.assertEqual(registration.reviewed_by, self.staff)
        self.assertEqual(registration.admin_remarks, 'Withdrawn by admin')
        self.assertFalse(registration.members.filter(reserved=True).exists())

    def test_delete_forming_team_only_and_preserve_history(self):
        with self.assertRaises(CompetitionError):
            delete_team(self.team_record.pk, self.member)
        delete_team(self.team_record.pk, self.leader)
        team = self.team(self.leader)
        registration = register_team(team.pk, self.competition(requires_admin_approval=True).pk, self.leader)
        for state in (Registration.PENDING_APPROVAL, Registration.PENDING_PAYMENT, Registration.ACTIVE,
                      Registration.REJECTED, Registration.CANCELLED):
            Registration.objects.filter(pk=registration.pk).update(status=state)
            with self.assertRaises(CompetitionError):
                delete_team(team.pk, self.leader)
            with self.assertRaises(ProtectedError):
                CompetitionTeam.objects.filter(pk=team.pk).delete()

    def test_database_rejects_invalid_registration_state_and_duplicate_reserved_user(self):
        registration = self.register(self.competition())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.filter(pk=registration.pk).update(status='payment_failed')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.filter(pk=registration.pk).update(price=-1)
        other = Registration.objects.create(team=self.team(self.member), competition=registration.competition,
                                            status=Registration.PENDING_PAYMENT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CompetitionRegistrationMember.objects.create(registration=other, competition=registration.competition,
                                                         user=self.leader)

    def test_staff_review_api_exposes_audit_fields(self):
        registration = self.register(self.competition(requires_admin_approval=True))
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            f'/api/my-teams/{self.team_record.pk}/review-registration/{registration.competition_id}/',
            {'approve': False, 'remarks': 'Evidence missing'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['reviewed_by'], self.staff.pk)
        self.assertEqual(response.data['status'], 'rejected')
        self.assertIsNotNone(response.data['reviewed_at'])

    def test_content_is_scoped_to_each_competition(self):
        from .models import TeamContent
        registrations = [self.register(self.competition(allow_content_submission=True)) for _ in range(2)]
        for registration in registrations:
            GroupCompetition.objects.filter(pk=registration.competition_id).update(
                start_datetime=timezone.now() - timedelta(hours=1))
            response = self.client.post(
                f'/api/my-teams/{self.team_record.pk}/submit-content/?competition_id={registration.competition_id}',
                {'description': f'Content {registration.pk}'}, format='json')
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.data['registration'], registration.pk)
        self.assertEqual(TeamContent.objects.count(), 2)
        details = self.client.get(f'/api/my-teams/{self.team_record.pk}/')
        self.assertEqual(details.status_code, 200)
        submissions = {entry['competition_details']['id']: entry['content_submission']
                       for entry in details.data['registrations']}
        self.assertEqual({competition_id: content['description'] for competition_id, content in submissions.items()},
                         {registration.competition_id: f'Content {registration.pk}' for registration in registrations})
        self.assertTrue(all(content['registration'] in [registration.pk for registration in registrations]
                            for content in submissions.values()))
        response = self.client.get(f'/api/group-competitions/{registrations[0].competition_id}/list-content/')
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['registration'], registrations[0].pk)

    def test_registration_without_content_exposes_null_submission(self):
        registration = self.register(self.competition(allow_content_submission=True))
        details = self.client.get(f'/api/my-teams/{self.team_record.pk}/')
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.data['registrations'][0]['id'], registration.pk)
        self.assertIsNone(details.data['registrations'][0]['content_submission'])

    def test_purchases_include_an_active_registration_when_another_is_unpaid(self):
        active = self.register(self.competition())
        self.register(self.competition(is_paid=True, price_per_member=100))
        response = self.client.get('/api/purchases/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['competition_teams']), 1)
        self.assertIn(active.pk, [r['id'] for r in response.data['competition_teams'][0]['registrations']])

    def test_team_query_count_does_not_grow_with_registration_count(self):
        from .models import TeamContent
        from .queries import teams_for_api
        from .serializers import CompetitionTeamDetailSerializer
        registration = self.register(self.competition())
        TeamContent.objects.create(team=self.team_record, registration=registration, description='First')
        with CaptureQueriesContext(connection) as first:
            CompetitionTeamDetailSerializer(teams_for_api(), many=True).data
        for _ in range(4):
            registration = self.register(self.competition())
            TeamContent.objects.create(team=self.team_record, registration=registration,
                                       description=f'Submission {registration.pk}')
        with CaptureQueriesContext(connection) as several:
            CompetitionTeamDetailSerializer(teams_for_api(), many=True).data
        self.assertEqual(len(first), len(several))
        self.assertLessEqual(len(several), 10)


class SoloLifecycleTests(LifecycleFixtures, TestCase):
    def test_free_capacity_and_idempotency(self):
        competition = self.solo(max_participants=1)
        first, second = self.user(), self.user()
        registration = register_free_solo(competition.pk, first)
        self.assertEqual(register_free_solo(competition.pk, first).pk, registration.pk)
        with self.assertRaises(CompetitionError):
            register_free_solo(competition.pk, second)
        self.assertEqual(competition.registrations.count(), 1)
        self.assertFalse(PaymentIntent.objects.exists())

    def test_paid_reservation_includes_pending_and_is_released_on_cancel(self):
        competition = self.solo(max_participants=1, is_paid=True, price_per_participant=100)
        first, second = self.user(), self.user()
        order = self.solo_order(competition, first)
        reserve_solo_order_items(order)
        reserve_solo_order_items(order)
        another = self.solo_order(competition, second)
        with self.assertRaises(CompetitionError):
            reserve_solo_order_items(another)
        release_order_reservations(order)
        reserve_solo_order_items(another)
        self.assertEqual(competition.registrations.filter(status='pending_payment').count(), 1)

    def test_paid_checkout_reserves_the_last_seat_and_replays(self):
        user = self.user()
        competition = self.solo(max_participants=1, is_paid=True, price_per_participant=100)
        cart, _ = Cart.objects.get_or_create(user=user)
        CartItem.objects.create(cart=cart, content_object=competition)
        # Use the existing payment fake; a pending gateway flow reserves capacity.
        from unittest.mock import patch
        client = APIClient()
        client.force_authenticate(user)
        payment = {'payment_required': True, 'payment_url': 'https://example.com/pay', 'topup': None, 'balance': 0}
        with patch.object(WalletService, 'pay_or_start_order_payment', return_value=payment):
            self.assertEqual(client.post('/api/orders/checkout/').status_code, 201)
            self.assertEqual(client.post('/api/orders/checkout/').status_code, 201)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(competition.registrations.get().status, 'pending_payment')


@skipUnless(connection.vendor == 'postgresql', 'Row-lock concurrency must be tested on PostgreSQL.')
class ConcurrentCompetitionTests(LifecycleFixtures, TransactionTestCase):
    def race(self, actions, successes=1):
        barrier = threading.Barrier(len(actions), timeout=10)
        results, failures = [], []

        def run(action):
            close_old_connections()
            try:
                barrier.wait()
                results.append(action())
            except Exception as exc:
                failures.append(exc)
            finally:
                connection.close()
        threads = [threading.Thread(target=run, args=(action,)) for action in actions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive(), 'Competition operation deadlocked.')
        self.assertEqual(len(results), successes)
        self.assertEqual(len(failures), len(actions) - successes)
        self.assertTrue(all(isinstance(exc, CompetitionError) for exc in failures), failures)
        return results

    def test_simultaneous_team_registrations_cannot_overbook(self):
        competition = self.competition(max_teams=1)
        teams = [self.team() for _ in range(3)]
        self.race([lambda team=team: register_team(team.pk, competition.pk, team.leader) for team in teams])
        self.assertEqual(competition.registrations.count(), 1)

    def test_simultaneous_free_solo_registrations_cannot_overbook(self):
        competition = self.solo(max_participants=1)
        users = [self.user() for _ in range(3)]
        self.race([lambda user=user: register_free_solo(competition.pk, user) for user in users])
        self.assertEqual(competition.registrations.count(), 1)

    def test_simultaneous_paid_solo_reservations_cannot_overbook(self):
        competition = self.solo(max_participants=1, is_paid=True, price_per_participant=100)
        orders = [self.solo_order(competition, self.user()) for _ in range(3)]
        self.race([lambda order=order: reserve_solo_order_items(order) for order in orders])
        self.assertEqual(competition.registrations.count(), 1)

    def test_simultaneous_conflicting_rosters_cannot_both_register(self):
        competition = self.competition()
        member = self.user()
        teams = [self.team(members=(member,)) for _ in range(2)]
        self.race([lambda team=team: register_team(team.pk, competition.pk, team.leader) for team in teams])
        self.assertEqual(competition.registrations.count(), 1)

    def test_simultaneous_settlement_activates_once(self):
        team = self.team()
        registration = register_team(team.pk, self.competition(is_paid=True, price_per_member=100).pk, team.leader)
        order = prepare_team_order(registration.pk, team.leader)
        order.paid_at = timezone.now()
        order.save(update_fields=['paid_at'])
        item_id = order.items.get().pk
        def settle():
            return activate_team_registration(registration.pk, OrderItem.objects.get(pk=item_id))[1]
        outcomes = self.race([settle, settle], successes=2)
        self.assertCountEqual(outcomes, [True, False])
