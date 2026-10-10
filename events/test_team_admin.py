from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CompetitionTeam, GroupCompetition, TeamMembership
from .services import invite_member, register_team


class TeamAdminTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser(
            email='admin@example.com', password='test-password',
        )
        self.leader = get_user_model().objects.create_user(email='leader@example.com', is_active=True)
        self.member = get_user_model().objects.create_user(email='member@example.com', is_active=True)
        self.team = CompetitionTeam.objects.create(name='Original team', leader=self.leader)
        self.leader_membership = TeamMembership.objects.create(
            team=self.team, user=self.leader,
            status=TeamMembership.STATUS_ACCEPTED, expires_at=None,
        )
        self.client.force_login(self.staff)
        self.team_url = reverse('admin:events_competitionteam_change', args=[self.team.pk])
        self.add_url = reverse('admin:events_teammembership_add')

    def membership_url(self, membership):
        return reverse('admin:events_teammembership_change', args=[membership.pk])

    def team_data(self, **changes):
        memberships = list(self.team.memberships.order_by('-joined_at'))
        data = {
            'name': self.team.name,
            'leader': self.team.leader_id,
            'memberships-TOTAL_FORMS': len(memberships),
            'memberships-INITIAL_FORMS': len(memberships),
            'memberships-MIN_NUM_FORMS': 0,
            'memberships-MAX_NUM_FORMS': 1000,
            'content_submissions-TOTAL_FORMS': 0,
            'content_submissions-INITIAL_FORMS': 0,
            'content_submissions-MIN_NUM_FORMS': 0,
            'content_submissions-MAX_NUM_FORMS': 1000,
            '_save': 'Save',
        }
        for index, membership in enumerate(memberships):
            data.update({
                f'memberships-{index}-id': membership.pk,
                f'memberships-{index}-team': self.team.pk,
                f'memberships-{index}-user': membership.user_id,
                f'memberships-{index}-status': membership.status,
            })
        data.update(changes)
        return data

    def test_team_page_offers_member_addition_and_editable_leader(self):
        response = self.client.get(self.team_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="leader"')
        inline = response.context['inline_admin_formsets'][0]
        self.assertTrue(inline.has_add_permission)
        self.assertEqual(inline.formset.empty_form.initial['status'], 'accepted')

    def test_admin_adds_member_inline_without_invitation(self):
        response = self.client.post(self.team_url, self.team_data(**{
            'memberships-TOTAL_FORMS': 2,
            'memberships-1-user': self.member.pk,
            'memberships-1-status': 'accepted',
        }))
        self.assertEqual(response.status_code, 302)
        membership = self.team.memberships.get(user=self.member)
        self.assertEqual(membership.status, 'accepted')
        self.assertEqual(membership.invited_by, self.staff)
        self.assertIsNone(membership.expires_at)
        self.assertIsNotNone(membership.responded_at)

    def test_admin_adds_member_from_membership_page(self):
        response = self.client.get(self.add_url)
        self.assertEqual(response.context['adminform'].form.initial['status'], 'accepted')
        response = self.client.post(self.add_url, {
            'team': self.team.pk, 'user': self.member.pk, 'status': 'accepted',
        })
        self.assertEqual(response.status_code, 302)
        membership = self.team.memberships.get(user=self.member)
        self.assertEqual(membership.status, 'accepted')
        self.assertEqual(membership.invited_by, self.staff)
        self.assertIsNone(membership.expires_at)
        self.assertIsNotNone(membership.responded_at)

    def test_admin_accepts_existing_invitation_without_losing_history(self):
        membership = invite_member(self.team.pk, self.leader, self.member.email)
        response = self.client.post(self.membership_url(membership), {
            'team': self.team.pk, 'user': self.member.pk, 'status': 'accepted',
        })
        self.assertEqual(response.status_code, 302)
        joined_at = membership.joined_at
        membership.refresh_from_db()
        self.assertEqual(membership.status, 'accepted')
        self.assertEqual(membership.invited_by, self.leader)
        self.assertEqual(membership.joined_at, joined_at)
        self.assertIsNone(membership.expires_at)
        self.assertIsNotNone(membership.responded_at)

    def test_duplicate_member_is_a_form_error(self):
        response = self.client.post(self.add_url, {
            'team': self.team.pk, 'user': self.leader.pk, 'status': 'accepted',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')
        self.assertEqual(self.team.memberships.count(), 1)

    def test_admin_can_rename_and_transfer_leadership(self):
        TeamMembership.objects.create(team=self.team, user=self.member, status='accepted')
        response = self.client.post(self.team_url, self.team_data(
            name='Renamed team', leader=self.member.pk,
        ))
        self.assertEqual(response.status_code, 302)
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'Renamed team')
        self.assertEqual(self.team.leader, self.member)
        self.assertEqual(self.team.memberships.filter(status='accepted').count(), 2)

    def test_leader_must_be_an_accepted_member(self):
        invite_member(self.team.pk, self.leader, self.member.email)
        response = self.client.post(self.team_url, self.team_data(leader=self.member.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn('leader', response.context['adminform'].form.errors)
        self.team.refresh_from_db()
        self.assertEqual(self.team.leader, self.leader)

    def test_new_leader_cannot_be_rejected_during_transfer(self):
        TeamMembership.objects.create(team=self.team, user=self.member, status='accepted')
        response = self.client.post(self.team_url, self.team_data(**{
            'leader': self.member.pk,
            'memberships-0-status': 'rejected',
        }))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'leader must remain an accepted member')
        self.team.refresh_from_db()
        self.assertEqual(self.team.leader, self.leader)
        self.assertEqual(self.team.memberships.get(user=self.member).status, 'accepted')

    def test_leader_cannot_be_rejected_from_either_admin_page(self):
        response = self.client.post(self.team_url, self.team_data(**{
            'memberships-0-status': 'rejected',
        }))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(self.membership_url(self.leader_membership), {
            'team': self.team.pk, 'user': self.leader.pk, 'status': 'rejected',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'leader must remain an accepted member')
        self.leader_membership.refresh_from_db()
        self.assertEqual(self.leader_membership.status, 'accepted')

    def test_membership_identity_cannot_be_changed(self):
        other_team = CompetitionTeam.objects.create(name='Another team', leader=self.member)
        response = self.client.post(self.membership_url(self.leader_membership), {
            'team': other_team.pk, 'user': self.member.pk, 'status': 'accepted',
        })
        self.assertEqual(response.status_code, 302)
        self.leader_membership.refresh_from_db()
        self.assertEqual(self.leader_membership.team, self.team)
        self.assertEqual(self.leader_membership.user, self.leader)

    def test_team_names_remain_case_insensitively_unique(self):
        CompetitionTeam.objects.create(name='Another team', leader=self.member)
        response = self.client.post(self.team_url, self.team_data(name='ANOTHER TEAM'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A team with this name already exists.')
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'Original team')

    def test_roster_edits_preserve_registered_members_and_price(self):
        competition = GroupCompetition.objects.create(
            title='Paid competition', description='Test', is_paid=True,
            price_per_member=100, max_group_size=5,
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=2),
        )
        registration = register_team(self.team.pk, competition.pk, self.leader)
        self.client.post(self.add_url, {
            'team': self.team.pk, 'user': self.member.pk, 'status': 'accepted',
        })
        response = self.client.post(self.team_url, self.team_data(**{
            'name': 'Renamed registered team', 'leader': self.member.pk,
            'memberships-1-status': 'rejected',
        }))
        self.assertEqual(response.status_code, 302)
        registration.refresh_from_db()
        self.assertEqual(list(registration.members.values_list('user_id', flat=True)), [self.leader.pk])
        self.assertEqual(registration.price, Decimal('100'))
        self.assertEqual(self.team.memberships.get(user=self.leader).status, 'rejected')

    def test_staff_without_membership_permissions_can_still_rename_team(self):
        staff = get_user_model().objects.create_user(
            email='team-editor@example.com', is_staff=True, is_active=True,
        )
        staff.user_permissions.add(Permission.objects.get(codename='change_competitionteam'))
        self.client.force_login(staff)
        response = self.client.post(self.team_url, {'name': 'Edited by staff', 'leader': self.leader.pk})
        self.assertEqual(response.status_code, 302)
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'Edited by staff')

    def test_admin_permissions_are_required(self):
        for user in (self.leader, get_user_model().objects.create_user(
            email='staff-no-permissions@example.com', is_staff=True, is_active=True,
        )):
            self.client.force_login(user)
            response = self.client.post(self.add_url, {
                'team': self.team.pk, 'user': self.member.pk, 'status': 'accepted',
            })
            self.assertEqual(response.status_code, 403 if user.is_staff else 302)
            response = self.client.post(self.team_url, self.team_data(name='Unauthorized'))
            self.assertEqual(response.status_code, 403 if user.is_staff else 302)
        self.assertFalse(self.team.memberships.filter(user=self.member).exists())
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'Original team')
