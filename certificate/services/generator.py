import uuid
import traceback
from django.utils import timezone
from django.conf import settings
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from certificate.models import Certificate, CompetitionCertificate


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


def generate_presentation_cert(cert: Certificate, force_regenerate: bool = False) -> Certificate:
    """
    Renders and saves presentation certificate SVG files with status management and error logging.
    """
    if not force_regenerate and cert.status == Certificate.STATUS_GENERATED and cert.file_en and cert.file_fa:
        return cert

    try:
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

        cert.status = Certificate.STATUS_GENERATED
        cert.generation_error = None
        cert.save()
        return cert

    except Exception as e:
        cert.status = Certificate.STATUS_FAILED
        cert.generation_error = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        cert.save()
        raise e


def generate_solo_cert(cert: CompetitionCertificate, force_regenerate: bool = False) -> CompetitionCertificate:
    """
    Renders and saves solo competition certificate SVG files with status management and error logging.
    """
    if not force_regenerate and cert.status == CompetitionCertificate.STATUS_GENERATED and cert.file_en and cert.file_fa:
        return cert

    if not cert.solo_registration:
        err_msg = "This certificate does not belong to a solo registration."
        cert.status = CompetitionCertificate.STATUS_FAILED
        cert.generation_error = err_msg
        cert.save()
        raise ValueError(err_msg)

    try:
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

        cert.status = CompetitionCertificate.STATUS_GENERATED
        cert.generation_error = None
        cert.save()
        return cert

    except Exception as e:
        cert.status = CompetitionCertificate.STATUS_FAILED
        cert.generation_error = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        cert.save()
        raise e


def generate_group_cert(cert: CompetitionCertificate, force_regenerate: bool = False) -> CompetitionCertificate:
    """
    Renders and saves group competition certificate SVG files with status management and error logging.
    """
    if not force_regenerate and cert.status == CompetitionCertificate.STATUS_GENERATED and cert.file_en and cert.file_fa:
        return cert

    if cert.registration_type != "group" or not cert.team:
        err_msg = "Certificate is not a group competition or missing team reference."
        cert.status = CompetitionCertificate.STATUS_FAILED
        cert.generation_error = err_msg
        cert.save()
        raise ValueError(err_msg)

    try:
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

        cert.status = CompetitionCertificate.STATUS_GENERATED
        cert.generation_error = None
        cert.save()
        return cert

    except Exception as e:
        cert.status = CompetitionCertificate.STATUS_FAILED
        cert.generation_error = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        cert.save()
        raise e


def generate_cert_for_object(cert_object, force_regenerate: bool = False):
    """
    Unified entry point for certificate generation.
    """
    if isinstance(cert_object, Certificate):
        return generate_presentation_cert(cert_object, force_regenerate=force_regenerate)
    elif isinstance(cert_object, CompetitionCertificate):
        if cert_object.registration_type == "solo":
            return generate_solo_cert(cert_object, force_regenerate=force_regenerate)
        elif cert_object.registration_type == "group":
            return generate_group_cert(cert_object, force_regenerate=force_regenerate)
    raise ValueError(f"Unsupported certificate object type: {type(cert_object)}")
