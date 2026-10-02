"""Shared eager-loading for private team and invitation responses."""
from django.db.models import Count, Prefetch, Q

from .models import CompetitionTeam, CompetitionTeamRegistration as Registration


def registrations_for_api():
    return Registration.objects.select_related(
        'competition__event', 'content_submission__team', 'content_submission__registration',
    ).annotate(
        reserved_count=Count('competition__registrations', filter=Q(
            competition__registrations__status__in=Registration.RESERVED_STATUSES)),
    ).prefetch_related('members', 'content_submission__images',
                       'content_submission__likes', 'content_submission__comments')


def teams_for_api():
    return CompetitionTeam.objects.select_related('leader', 'group_competition__event').prefetch_related(
        'memberships__user', Prefetch('registrations', queryset=registrations_for_api()),
        'content_submissions',
    )
