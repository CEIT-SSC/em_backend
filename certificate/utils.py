import uuid
from django.utils import timezone
from django.conf import settings
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from .models import CompetitionCertificate, Certificate


def _render_and_save_svg(cert_object, template_name, context, file_field_name, file_name_pattern):
    file_field = getattr(cert_object, file_field_name)
    storage = file_field.storage
    svg_content = render_to_string(template_name, context)

    timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
    short_uuid = uuid.uuid4().hex[:6]
    candidate_name = file_name_pattern.format(pk=cert_object.pk, ts=timestamp, uuid=short_uuid)
    final_name = storage.get_available_name(candidate_name)

    storage.save(final_name, ContentFile(svg_content.encode('utf-8')))
    setattr(cert_object, file_field_name, final_name)


def generate_presentation_certificate(cert: Certificate):
    presentation = cert.enrollment.presentation
    event_end_date_str = presentation.end_time.strftime('%B %d, %Y')
    verification_link = f"{settings.FRONTEND_URL}/certificates/presentation/{cert.verification_id}"

    context = {
        'name': cert.name_on_certificate,
        'presentation_title': presentation.title,
        'event_end_date': event_end_date_str,
        'verification_link': verification_link,
    }

    _render_and_save_svg(
        cert, 'certificate-en.svg', context, 'file_en',
        'certificates/presentations/pres-en_{pk}_{ts}_{uuid}.svg'
    )
    _render_and_save_svg(
        cert, 'certificate-fa.svg', context, 'file_fa',
        'certificates/presentations/pres-fa_{pk}_{ts}_{uuid}.svg'
    )
    cert.save()


def generate_solo_certificate(cert: CompetitionCertificate):
    if not cert.solo_registration:
        raise ValueError("This certificate does not belong to a solo registration.")

    solo_comp = cert.solo_registration.solo_competition
    event_end_date_str = solo_comp.end_datetime.strftime('%B %d, %Y')
    verification_link = f"{settings.FRONTEND_URL}/certificates/competition/{cert.verification_id}"

    context = {
        'name': cert.name_on_certificate,
        'competition_title': solo_comp.title,
        'ranking': cert.ranking,
        'event_end_date': event_end_date_str,
        'verification_link': verification_link,
    }

    _render_and_save_svg(
        cert, 'competition-certificate-en.svg', context, 'file_en',
        'certificates/competitions/solo-en_{pk}_{ts}_{uuid}.svg'
    )
    _render_and_save_svg(
        cert, 'competition-certificate-fa.svg', context, 'file_fa',
        'certificates/competitions/solo-fa_{pk}_{ts}_{uuid}.svg'
    )
    cert.save()


def generate_group_certificate(cert: CompetitionCertificate):
    if cert.registration_type != "group" or not cert.team:
        raise ValueError("Certificate is not a group competition or missing team reference.")

    team = cert.team
    group_comp = team.group_competition
    event_end_date_str = group_comp.end_datetime.strftime('%B %d, %Y')

    members = [m.user.get_full_name() or m.user.email for m in team.memberships.select_related('user').all()]
    verification_link = f"{settings.FRONTEND_URL}/certificates/competition/{cert.verification_id}"

    context = {
        'team_name': team.name,
        'team_members': members,
        'competition_title': group_comp.title,
        'ranking': cert.ranking,
        'event_end_date': event_end_date_str,
        'verification_link': verification_link,
    }

    _render_and_save_svg(
        cert, 'group-certificate-en.svg', context, 'file_en',
        'certificates/competitions/group-en_{pk}_{ts}_{uuid}.svg'
    )
    _render_and_save_svg(
        cert, 'group-certificate-fa.svg', context, 'file_fa',
        'certificates/competitions/group-fa_{pk}_{ts}_{uuid}.svg'
    )
    cert.save()