from django.utils import timezone
from events.models import (
    CompetitionTeam,
    PresentationEnrollment,
    SoloCompetitionRegistration,
    TeamMembership,
)


def get_user_eligible_presentations(user):
    """
    Returns queryset of completed presentation enrollments for presentations that have ended.
    """
    return PresentationEnrollment.objects.filter(
        user=user,
        status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
        presentation__end_time__lt=timezone.now()
    ).select_related('presentation', 'certificate').order_by('-presentation__end_time')


def check_presentation_eligibility(user, enrollment_pk, reject_existing=True):
    """
    Validates if a presentation enrollment exists, belongs to user, is completed and presentation has ended.
    Returns (enrollment, error_message).
    """
    try:
        enrollment = PresentationEnrollment.objects.select_related('presentation').get(
            pk=enrollment_pk,
            user=user,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE
        )
    except PresentationEnrollment.DoesNotExist:
        return None, "Eligible enrollment not found."

    if enrollment.presentation.end_time > timezone.now():
        return None, "This presentation has not ended yet."

    if reject_existing and hasattr(enrollment, 'certificate'):
        return None, "A certificate has already been requested for this enrollment."

    return enrollment, None


def get_user_eligible_solo_competitions(user):
    """
    Returns queryset of completed solo competition registrations for competitions that have ended.
    """
    return SoloCompetitionRegistration.objects.filter(
        user=user,
        status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
        solo_competition__end_datetime__lt=timezone.now()
    ).select_related('solo_competition__event', 'certificate').order_by('-solo_competition__start_datetime')


def check_solo_competition_eligibility(user, registration_id, reject_existing=True):
    """
    Validates if a solo competition registration exists, belongs to user, and is completed.
    Returns (registration, error_message).
    """
    try:
        registration = SoloCompetitionRegistration.objects.select_related(
            'solo_competition'
        ).get(
            pk=registration_id,
            user=user
        )
    except SoloCompetitionRegistration.DoesNotExist:
        return None, "Eligible solo competition registration not found."

    if registration.status != SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE:
        return None, "This competition registration is not completed."

    if registration.solo_competition.end_datetime > timezone.now():
        return None, "This competition has not ended yet."

    if reject_existing and hasattr(registration, 'certificate'):
        return None, "Certificate already requested for this registration."

    return registration, None


def get_user_eligible_group_competitions(user):
    """
    Returns queryset of active competition teams where user is a member and competition has ended.
    """
    return CompetitionTeam.objects.filter(
        memberships__user=user,
        memberships__status=TeamMembership.STATUS_ACCEPTED,
        status=CompetitionTeam.STATUS_ACTIVE,
        group_competition__end_datetime__lt=timezone.now()
    ).select_related('group_competition__event', 'certificate').order_by('-group_competition__start_datetime')


def check_group_competition_eligibility(user, team_id, reject_existing=True):
    """
    Validates if a group competition team exists, user is a member, and team is active.
    Returns (team, error_message).
    """
    try:
        team = CompetitionTeam.objects.select_related('group_competition').get(
            pk=team_id,
            memberships__user=user,
            memberships__status=TeamMembership.STATUS_ACCEPTED,
        )
    except CompetitionTeam.DoesNotExist:
        return None, "Team not found for this competition, or you are not a member."

    if team.status != CompetitionTeam.STATUS_ACTIVE:
        return None, "This team is not active for the competition."

    if not team.group_competition:
        return None, "This team is not registered for a competition."

    if team.group_competition.end_datetime > timezone.now():
        return None, "This competition has not ended yet."

    if reject_existing and hasattr(team, 'certificate'):
        return None, "Certificate already requested for this team."

    return team, None
