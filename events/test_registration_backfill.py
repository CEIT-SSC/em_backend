"""Exercise the production data rewrite without reversing an irreversible migration."""
from importlib import import_module
from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from shop.models import Order, OrderItem
from .models import (CompetitionTeam, CompetitionTeamRegistration, CompetitionRegistrationMember,
                     GroupCompetition, TeamContent, TeamMembership)


class RegistrationBackfillTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='legacy-leader@example.com')
        self.other = get_user_model().objects.create_user(email='legacy-pending@example.com')
        self.competition = GroupCompetition.objects.create(
            title='Legacy competition', description='Test', max_group_size=5, is_paid=True,
            price_per_member=100, start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=2))
        self.team = CompetitionTeam.objects.create(
            name='Legacy team', leader=self.user, group_competition=self.competition,
            status='awaiting_payment_confirmation')
        TeamMembership.objects.create(team=self.team, user=self.user, status='accepted')
        TeamMembership.objects.create(team=self.team, user=self.other, status='pending')
        self.order = Order.objects.create(user=self.user, subtotal_amount=100, total_amount=100)
        self.item = OrderItem.objects.create(order=self.order, content_object=self.team, price=100, description='Legacy entry')
        self.content = TeamContent.objects.create(team=self.team, description='Legacy content')

    def test_preserves_roster_price_order_and_content(self):
        # SQLite schema_editor cannot run inside TestCase's transaction with FK
        # checks, but this data function only needs the connection alias.
        from types import SimpleNamespace
        migration = import_module('events.migrations.0010_backfill_team_registrations')
        migration.backfill(apps, SimpleNamespace(connection=connection))
        registration = CompetitionTeamRegistration.objects.get()
        self.assertEqual(registration.status, 'pending_payment')
        self.assertEqual(registration.price, 100)
        self.assertEqual(registration.order_item_id, self.item.pk)
        self.assertEqual(list(registration.members.values_list('user_id', flat=True)), [self.user.pk])
        self.item.refresh_from_db()
        self.content.refresh_from_db()
        self.assertEqual(self.item.content_object.pk, registration.pk)
        self.assertIsInstance(self.item.content_object, CompetitionTeamRegistration)
        self.assertEqual(self.content.registration_id, registration.pk)
        self.assertIsNone(registration.reviewed_by)
        self.assertIsNone(registration.reviewed_at)
        self.assertEqual(TeamMembership.objects.get(user=self.user).expires_at, None)

    def test_conflicting_historical_rosters_abort_without_rewriting_orders(self):
        from types import SimpleNamespace
        another = CompetitionTeam.objects.create(name='Conflicting legacy team', leader=self.user,
            group_competition=self.competition, status='active')
        TeamMembership.objects.create(team=another, user=self.user, status='accepted')
        migration = import_module('events.migrations.0010_backfill_team_registrations')
        with self.assertRaisesRegex(RuntimeError, 'conflicting accepted members'), transaction.atomic():
            migration.backfill(apps, SimpleNamespace(connection=connection))
        self.assertFalse(CompetitionTeamRegistration.objects.exists())
        self.assertFalse(CompetitionRegistrationMember.objects.exists())
        self.item.refresh_from_db()
        self.assertIsInstance(self.item.content_object, CompetitionTeam)
