from decimal import Decimal

from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    Team = apps.get_model('events', 'CompetitionTeam')
    Membership = apps.get_model('events', 'TeamMembership')
    Registration = apps.get_model('events', 'CompetitionTeamRegistration')
    Member = apps.get_model('events', 'CompetitionRegistrationMember')
    Content = apps.get_model('events', 'TeamContent')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    OrderItem = apps.get_model('shop', 'OrderItem')
    CartItem = apps.get_model('shop', 'CartItem')
    team_ct = ContentType.objects.using(alias).filter(app_label='events', model='competitionteam').first()
    registration_ct, _ = ContentType.objects.using(alias).get_or_create(
        app_label='events', model='competitionteamregistration')
    statuses = {
        'forming': 'pending_approval', 'pending_admin_verification': 'pending_approval',
        'rejected_by_admin': 'rejected', 'active': 'active', 'cancelled': 'cancelled',
        'approved_awaiting_payment': 'pending_payment', 'in_cart': 'pending_payment',
        'awaiting_payment_confirmation': 'pending_payment', 'payment_failed': 'pending_payment',
    }
    for team in Team.objects.using(alias).exclude(group_competition_id=None).select_related('group_competition').iterator():
        competition = team.group_competition
        ids = list(Membership.objects.using(alias).filter(team_id=team.pk, status='accepted').values_list('user_id', flat=True))
        if team.leader_id not in ids:
            raise RuntimeError(f'Team {team.pk} has no accepted leader membership. Repair this roster before migrating.')
        state = statuses[team.status]
        reserved = state in ('pending_approval', 'pending_payment', 'active')
        conflicts = Member.objects.using(alias).filter(competition_id=competition.pk, user_id__in=ids, reserved=True)
        if reserved and conflicts.exists():
            raise RuntimeError(f'Team {team.pk} has conflicting accepted members in competition {competition.pk}. Resolve the conflicting registrations before migrating.')
        items = OrderItem.objects.using(alias).filter(content_type=team_ct, object_id=team.pk) if team_ct else OrderItem.objects.none()
        payable = items.filter(order__status__in=['pending_payment', 'processing_enrollment', 'payment_failed']).order_by('-pk').first()
        completed = items.filter(order__status='completed').order_by('-pk').first()
        linked = completed if state == 'active' else payable
        price = ((competition.price_per_member or Decimal('0')) * len(ids)
                 if competition.is_paid else Decimal('0'))
        registration = Registration.objects.using(alias).create(
            team_id=team.pk, competition_id=competition.pk, status=state,
            price=linked.price if linked else price, order_item=linked,
            admin_remarks=team.admin_remarks or '',
            activated_at=(linked.order.paid_at if linked else None) or team.created_at if state == 'active' else None,
        )
        Registration.objects.using(alias).filter(pk=registration.pk).update(created_at=team.created_at)
        Member.objects.using(alias).bulk_create([
            Member(registration_id=registration.pk, competition_id=competition.pk, user_id=pk, reserved=reserved)
            for pk in ids
        ])
        items.update(content_type=registration_ct, object_id=registration.pk)
        if team_ct:
            CartItem.objects.using(alias).filter(content_type=team_ct, object_id=team.pk).update(
                content_type=registration_ct, object_id=registration.pk)
        Content.objects.using(alias).filter(team_id=team.pk).update(registration=registration)
    # Accepted members are not expiring invitations.
    Membership.objects.using(alias).exclude(status='pending').update(expires_at=None)


class Migration(migrations.Migration):
    dependencies = [('events', '0009_competition_registration_lifecycle')]
    # Order references are rewritten, so an automatic reverse would lose history.
    operations = [migrations.RunPython(backfill)]
