"""Competition and team lifecycle operations. No gateway or wallet dependencies."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException

from .models import (
    CompetitionTeam, CompetitionTeamRegistration as Registration,
    CompetitionRegistrationMember, GroupCompetition, SoloCompetition,
    SoloCompetitionRegistration, TeamMembership, invitation_expiry,
)


class CompetitionError(APIException):
    status_code = 400

    def __init__(self, message, code='invalid_transition', status_code=400):
        self.status_code = status_code
        super().__init__({'code': code, 'error': message})


def require_leader(team, actor):
    if team.leader_id != actor.pk:
        raise CompetitionError('Only the team leader can perform this operation.', 'leader_required', 403)


def validate_open(competition):
    if not competition.is_active or (competition.event_id and not competition.event.is_active):
        raise CompetitionError('This competition is not available for registration.', 'registration_closed')
    if timezone.now() > competition.start_datetime:
        raise CompetitionError('Registration for this competition has closed.', 'registration_closed')


LEGACY_TO_REGISTRATION = {
    CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION: Registration.PENDING_APPROVAL,
    CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT: Registration.PENDING_PAYMENT,
    CompetitionTeam.STATUS_IN_CART: Registration.PENDING_PAYMENT,
    CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION: Registration.PENDING_PAYMENT,
    CompetitionTeam.STATUS_PAYMENT_FAILED: Registration.PENDING_PAYMENT,
    CompetitionTeam.STATUS_ACTIVE: Registration.ACTIVE,
    CompetitionTeam.STATUS_REJECTED_BY_ADMIN: Registration.REJECTED,
    CompetitionTeam.STATUS_CANCELLED: Registration.CANCELLED,
}


def sync_legacy(registration, *, payment_started=False):
    """Maintain the old single-competition projection during client migration."""
    status_map = {
        Registration.PENDING_APPROVAL: CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION,
        Registration.PENDING_PAYMENT: CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT,
        Registration.ACTIVE: CompetitionTeam.STATUS_ACTIVE,
        Registration.REJECTED: CompetitionTeam.STATUS_REJECTED_BY_ADMIN,
        Registration.CANCELLED: CompetitionTeam.STATUS_CANCELLED,
    }
    state = status_map[registration.status]
    if payment_started and registration.status == Registration.PENDING_PAYMENT:
        state = CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION
    CompetitionTeam.objects.filter(pk=registration.team_id).update(
        group_competition_id=registration.competition_id, status=state,
        is_approved_by_admin=bool(registration.reviewed_at and registration.status != Registration.REJECTED),
        admin_remarks=registration.admin_remarks,
    )


def legacy_registration(team):
    """Resolve legacy order references; never silently choose among competitions."""
    registrations = list(team.registrations.all())
    if len(registrations) == 1:
        return registrations[0]
    if len(registrations) > 1:
        raise CompetitionError('Specify competition_id for this team.', 'competition_required')
    if not team.group_competition_id or team.status not in LEGACY_TO_REGISTRATION:
        raise CompetitionError('This team is not registered in a competition.')
    # Compatibility for old integrations creating model rows directly. Existing
    # production rows are backfilled by the data migration.
    with transaction.atomic():
        competition = GroupCompetition.objects.select_for_update().get(pk=team.group_competition_id)
        team = CompetitionTeam.objects.select_for_update().get(pk=team.pk)
        existing = team.registrations.first()
        if existing:
            return existing
        user_ids = list(team.memberships.filter(status=TeamMembership.STATUS_ACCEPTED)
                        .values_list('user_id', flat=True))
        state = LEGACY_TO_REGISTRATION[team.status]
        price = (competition.price_per_member or Decimal('0')) * len(user_ids) if competition.requires_payment() else Decimal('0')
        registration = Registration.objects.create(
            team=team, competition=competition, status=state, price=price,
            activated_at=timezone.now() if state == Registration.ACTIVE else None,
        )
        CompetitionRegistrationMember.objects.bulk_create([
            CompetitionRegistrationMember(registration=registration, competition=competition,
                                          user_id=pk, reserved=state in Registration.RESERVED_STATUSES)
            for pk in user_ids
        ])
        return registration


def resolve_registration(team, competition_id=None):
    if competition_id is None:
        return legacy_registration(team)
    try:
        competition_id = int(competition_id)
    except (ValueError, TypeError):
        raise CompetitionError('competition_id must be an integer.', 'invalid_competition')
    return get_object_or_404(Registration, team=team, competition_id=competition_id)


@transaction.atomic
def register_team(team_id, competition_id, actor):
    competition = get_object_or_404(GroupCompetition.objects.select_for_update(), pk=competition_id)
    team = get_object_or_404(CompetitionTeam.objects.select_for_update(), pk=team_id)
    require_leader(team, actor)
    # Authorize before checking availability or returning registration details.
    validate_open(competition)
    existing = Registration.objects.filter(team=team, competition=competition).first()
    if existing:
        raise CompetitionError('This team already has a registration for this competition.', 'already_registered')
    member_ids = list(team.memberships.filter(status=TeamMembership.STATUS_ACCEPTED).values_list('user_id', flat=True))
    if team.leader_id not in member_ids:
        raise CompetitionError('The leader must have an accepted membership.', 'invalid_roster')
    if not competition.min_group_size <= len(member_ids) <= competition.max_group_size:
        raise CompetitionError('Accepted team size is outside the competition limits.', 'invalid_team_size')
    if competition.max_teams is not None and competition.registrations.filter(
        status__in=Registration.RESERVED_STATUSES,
    ).count() >= competition.max_teams:
        raise CompetitionError('This competition has reached its maximum number of teams.', 'capacity_exceeded')
    if CompetitionRegistrationMember.objects.filter(competition=competition, user_id__in=member_ids, reserved=True).exists():
        raise CompetitionError('An accepted member is already registered with another team in this competition.', 'membership_conflict')
    state = (Registration.PENDING_APPROVAL if competition.requires_admin_approval else
             Registration.PENDING_PAYMENT if competition.requires_payment() else Registration.ACTIVE)
    price = (competition.price_per_member or Decimal('0')) * len(member_ids) if competition.requires_payment() else Decimal('0')
    registration = Registration.objects.create(
        team=team, competition=competition, status=state, price=price,
        activated_at=timezone.now() if state == Registration.ACTIVE else None,
    )
    CompetitionRegistrationMember.objects.bulk_create([
        CompetitionRegistrationMember(registration=registration, competition=competition, user_id=pk)
        for pk in member_ids
    ])
    sync_legacy(registration)
    return registration


def lock_registration(registration_id):
    competition_id = Registration.objects.values_list('competition_id', flat=True).get(pk=registration_id)
    GroupCompetition.objects.select_for_update().get(pk=competition_id)
    return Registration.objects.select_for_update().get(pk=registration_id)


@transaction.atomic
def review_registration(registration_id, actor, *, approve, remarks=''):
    if not actor.is_staff:
        raise CompetitionError('Administrative review requires staff access.', 'staff_required', 403)
    registration = lock_registration(registration_id)
    if registration.status != Registration.PENDING_APPROVAL:
        raise CompetitionError('Only pending approval registrations can be reviewed.')
    registration.reviewed_by = actor
    registration.reviewed_at = timezone.now()
    registration.admin_remarks = remarks.strip()
    if approve:
        registration.status = Registration.PENDING_PAYMENT if registration.price > 0 else Registration.ACTIVE
        if registration.status == Registration.ACTIVE:
            registration.activated_at = timezone.now()
    else:
        registration.status = Registration.REJECTED
        registration.members.update(reserved=False)
    registration.save()
    sync_legacy(registration)
    return registration


@transaction.atomic
def cancel_registration(registration_id, actor):
    registration = lock_registration(registration_id)
    require_leader(registration.team, actor)
    if registration.status not in (Registration.PENDING_APPROVAL, Registration.PENDING_PAYMENT):
        raise CompetitionError('Only unpaid pending registrations can be cancelled.')
    if registration.order_item_id:
        raise CompetitionError('Cancel the pending order before cancelling this registration.', 'order_pending')
    registration.status = Registration.CANCELLED
    registration.members.update(reserved=False)
    registration.save()
    sync_legacy(registration)
    return registration


@transaction.atomic
def invite_member(team_id, actor, email):
    team = get_object_or_404(CompetitionTeam.objects.select_for_update(), pk=team_id)
    require_leader(team, actor)
    user = get_object_or_404(get_user_model(), email__iexact=email)
    if user.pk == team.leader_id:
        raise CompetitionError('The leader already belongs to the team.', 'duplicate_membership')
    membership = TeamMembership.objects.filter(team=team, user=user).first()
    now = timezone.now()
    if membership and (membership.status == TeamMembership.STATUS_ACCEPTED or
                       membership.status == TeamMembership.STATUS_PENDING and
                       (membership.expires_at is None or membership.expires_at > now)):
        raise CompetitionError('This user already belongs to the team or has a pending invitation.', 'duplicate_membership')
    membership, _ = TeamMembership.objects.update_or_create(team=team, user=user, defaults={
        'status': TeamMembership.STATUS_PENDING, 'invited_by': actor,
        'expires_at': invitation_expiry(), 'responded_at': None,
    })
    return membership


def respond_to_invitation(team_id, actor, action):
    if action not in ('accept', 'reject'):
        raise CompetitionError('Invitation action must be accept or reject.', 'invalid_action')
    # Save expiry before returning the error, so repeated attempts stay expired.
    expired = False
    with transaction.atomic():
        team = get_object_or_404(CompetitionTeam.objects.select_for_update(), pk=team_id)
        membership = get_object_or_404(TeamMembership.objects.select_for_update(), team=team, user=actor)
        if membership.status != TeamMembership.STATUS_PENDING:
            raise CompetitionError('This invitation has already been responded to.')
        now = timezone.now()
        if membership.expires_at and membership.expires_at <= now:
            membership.status = TeamMembership.STATUS_EXPIRED
            expired = True
        else:
            membership.status = TeamMembership.STATUS_ACCEPTED if action == 'accept' else TeamMembership.STATUS_REJECTED
        membership.responded_at = now
        membership.save(update_fields=['status', 'responded_at'])
    if expired:
        raise CompetitionError('This invitation has expired.', 'invitation_expired')
    return membership


@transaction.atomic
def delete_team(team_id, actor):
    from django.contrib.contenttypes.models import ContentType
    from shop.models import OrderItem
    team = get_object_or_404(CompetitionTeam.objects.select_for_update(), pk=team_id)
    require_leader(team, actor)
    # Preserve registration, review, and purchase history, including terminal rows.
    if team.registrations.exists() or team.group_competition_id or OrderItem.objects.filter(
        content_type=ContentType.objects.get_for_model(CompetitionTeam), object_id=team.pk,
    ).exists():
        raise CompetitionError('Teams with registrations or purchase history cannot be deleted.', 'unsafe_deletion')
    team.delete()


@transaction.atomic
def register_free_solo(competition_id, actor):
    competition = get_object_or_404(SoloCompetition.objects.select_for_update(), pk=competition_id)
    validate_open(competition)
    if competition.is_paid and (competition.price_per_participant or 0) > 0:
        raise CompetitionError('This competition requires checkout.', 'payment_required')
    existing = competition.registrations.filter(user=actor).first()
    if existing and existing.status in (SoloCompetitionRegistration.STATUS_PENDING_PAYMENT,
                                        SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE):
        return existing
    ensure_solo_capacity(competition)
    registration, _ = SoloCompetitionRegistration.objects.update_or_create(
        user=actor, solo_competition=competition,
        defaults={'status': SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE, 'order_item': None},
    )
    return registration


def ensure_solo_capacity(competition, *, exclude_user=None):
    reserved = competition.registrations.filter(status__in=(
        SoloCompetitionRegistration.STATUS_PENDING_PAYMENT,
        SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
    ))
    if exclude_user is not None:
        reserved = reserved.exclude(user_id=exclude_user)
    if competition.max_participants is not None and reserved.count() >= competition.max_participants:
        raise CompetitionError('This competition has reached its maximum number of participants.', 'capacity_exceeded')


@transaction.atomic
def reserve_solo_order_items(order):
    items = [(item, item.content_object) for item in order.items.select_related('content_type')]
    for item, competition in sorted(
        ((item, obj) for item, obj in items if isinstance(obj, SoloCompetition)), key=lambda pair: pair[1].pk,
    ):
        competition = SoloCompetition.objects.select_for_update().get(pk=competition.pk)
        registration = competition.registrations.filter(user=order.user).first()
        if registration and registration.status == SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE:
            raise CompetitionError('Already registered for this solo competition.', 'already_registered')
        if registration and registration.status == SoloCompetitionRegistration.STATUS_PENDING_PAYMENT and registration.order_item_id != item.pk:
            raise CompetitionError('Another order is pending for this solo competition.', 'order_pending')
        ensure_solo_capacity(competition, exclude_user=order.user_id)
        SoloCompetitionRegistration.objects.update_or_create(
            user=order.user, solo_competition=competition,
            defaults={'status': SoloCompetitionRegistration.STATUS_PENDING_PAYMENT, 'order_item': item},
        )
