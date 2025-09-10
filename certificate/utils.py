# in certificates/utils.py (Optional Refined Version)

import os
import uuid
from django.utils import timezone
from django.conf import settings
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from .models import CompetitionCertificate, Certificate


def _render_and_save_svg(cert_object, template_name, context, file_field_name, file_name_pattern):
    """
    Private helper to render an SVG, save it, and attach it to the model.
    """
    file_field = getattr(cert_object, file_field_name)
    storage = file_field.storage
    svg_content = render_to_string(template_name, context)

    timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
    short_uuid = uuid.uuid4().hex[:6]
    candidate_name = file_name_pattern.format(pk=cert_object.pk, ts=timestamp, uuid=short_uuid)
    final_name = storage.get_available_name(candidate_name)

    storage.save(final_name, ContentFile(svg_content.encode('utf-8')))
    setattr(cert_object, file_field_name, final_name)


def generate_certificate(cert: CompetitionCertificate):
    if not cert.solo_registration:
        raise ValueError("This certificate does not belong to a solo registration.")

    verification_link = f"{settings.FRONTEND_URL}/certificates/competition/{cert.verification_id}"
    context = {
        'name': cert.name_on_certificate,
        'competition_title': cert.solo_registration.solo_competition.title,
        'ranking': cert.ranking,
        'event_title': cert.solo_registration.solo_competition.event.title,
        'event_end_date': cert.solo_registration.solo_competition.event.end_date.strftime('%B %d, %Y'),
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


def generate_presentation_certificate(cert: Certificate):
    verification_link = f"{settings.FRONTEND_URL}/certificates/presentation/{cert.verification_id}"
    context = {
        'name': cert.name_on_certificate,
        'presentation_title': cert.enrollment.presentation.title,
        'event_title': cert.enrollment.presentation.event.title,
        'event_end_date': cert.enrollment.presentation.event.end_date.strftime('%B %d, %Y'),
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


def generate_group_certificate(cert: CompetitionCertificate):
    if cert.registration_type != "group" or not cert.team:
        raise ValueError("Certificate is not a group competition or missing team reference.")

    team = cert.team
    members = [m.user.get_full_name() or m.user.email for m in team.memberships.select_related('user').all()]
    verification_link = f"{settings.FRONTEND_URL}/certificates/competition/{cert.verification_id}"
    base_context = {
        'team_name': team.name,
        'team_members': members,
        'competition_title': team.group_competition.title,
        'ranking': cert.ranking,
        'event_title': team.group_competition.event.title,
        'event_end_date': team.group_competition.event.end_date.strftime('%B %d, %Y'),
        'verification_link': verification_link,
    }

    _render_and_save_svg(
        cert, 'group-certificate-en.svg', base_context, 'file_en',
        'certificates/competitions/group-en_{pk}_{ts}_{uuid}.svg'
    )
    _render_and_save_svg(
        cert, 'group-certificate-fa.svg', base_context, 'file_fa',
        'certificates/competitions/group-fa_{pk}_{ts}_{uuid}.svg'
    )
    cert.save()