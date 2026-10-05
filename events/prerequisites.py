"""Registration prerequisite checks shared by event and shop flows."""

from .models import (
    CompetitionRegistrationMember, CompetitionTeamRegistration,
    GroupCompetition, Presentation, PresentationEnrollment,
    SoloCompetition, SoloCompetitionRegistration,
)


def missing_prerequisites(item, user_ids):
    """Return requirements missing for at least one participant.

    Team prerequisites use the registered roster snapshot, not current team
    membership. Only confirmed registrations count.
    """
    if not isinstance(item, (Presentation, SoloCompetition, GroupCompetition)):
        return []
    user_ids = set(user_ids)
    missing = []
    for requirement in item.registration_prerequisites.select_related(
        'required_presentation', 'required_solo_competition', 'required_group_competition'
    ):
        required_item = requirement.required_item
        if isinstance(required_item, Presentation):
            registered = set(PresentationEnrollment.objects.filter(
                presentation=required_item,
                status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
                user_id__in=user_ids,
            ).values_list('user_id', flat=True))
        elif isinstance(required_item, SoloCompetition):
            registered = set(SoloCompetitionRegistration.objects.filter(
                solo_competition=required_item,
                status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
                user_id__in=user_ids,
            ).values_list('user_id', flat=True))
        else:
            registered = set(CompetitionRegistrationMember.objects.filter(
                competition=required_item,
                registration__status=CompetitionTeamRegistration.ACTIVE,
                user_id__in=user_ids,
            ).values_list('user_id', flat=True))
        if registered != user_ids:
            missing.append(required_item)
    return missing


def prerequisite_error(item, user_ids):
    missing = missing_prerequisites(item, user_ids)
    if missing:
        names = ', '.join(str(required) for required in missing)
        return f'Confirmed registration in {names} is required before registering for {item}.'
    return None
