import base64
import logging
import mimetypes
import traceback
import uuid
from functools import lru_cache

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from certificate.models import Certificate, CompetitionCertificate
from events.models import TeamMembership


logger = logging.getLogger(__name__)

EMBEDDED_STATIC_IMAGES = (
    'certificates/AUT-CESSC-logo.png',
    'certificates/signature.png',
)


@lru_cache(maxsize=None)
def _static_image_data_uri(asset_name):
    content_type = mimetypes.guess_type(asset_name)[0] or 'application/octet-stream'
    source_path = finders.find(asset_name)
    if source_path:
        with open(source_path, 'rb') as asset_file:
            asset_bytes = asset_file.read()
    else:
        with staticfiles_storage.open(asset_name, 'rb') as asset_file:
            asset_bytes = asset_file.read()
    encoded = base64.b64encode(asset_bytes).decode('ascii')
    return f'data:{content_type};base64,{encoded}'


def _render_self_contained_svg(template_name, context):
    svg_content = render_to_string(template_name, context)
    for asset_name in EMBEDDED_STATIC_IMAGES:
        svg_content = svg_content.replace(
            staticfiles_storage.url(asset_name),
            _static_image_data_uri(asset_name),
        )
    return svg_content


def _candidate_name(cert_object, file_name_pattern):
    timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
    short_uuid = uuid.uuid4().hex[:6]
    return file_name_pattern.format(
        pk=cert_object.pk,
        ts=timestamp,
        uuid=short_uuid,
    )


def _render_and_save_svg(cert_object, template_name, context, file_field_name, file_name_pattern):
    """Compatibility helper for callers that save one SVG file at a time."""
    file_field = getattr(cert_object, file_field_name)
    svg_content = _render_self_contained_svg(template_name, context)
    saved_name = file_field.storage.save(
        _candidate_name(cert_object, file_name_pattern),
        ContentFile(svg_content.encode('utf-8')),
    )
    setattr(cert_object, file_field_name, saved_name)
    return file_field.storage, saved_name


def _delete_files(files):
    for storage, name in files:
        if not name:
            continue
        try:
            storage.delete(name)
        except Exception:
            logger.exception("Could not delete obsolete certificate file %s", name)


def _record_failure(model, cert_object, error):
    error_details = f"{type(error).__name__}: {error}\n{traceback.format_exc()}"
    model.objects.filter(pk=cert_object.pk).update(
        status=model.STATUS_FAILED,
        generation_error=error_details,
    )
    cert_object.status = model.STATUS_FAILED
    cert_object.generation_error = error_details


def _generate_certificate_files(
    cert_object,
    model,
    related_fields,
    build_render_plan,
    force_regenerate=False,
):
    saved_files = []

    try:
        with transaction.atomic():
            cert = model.objects.select_for_update().select_related(
                *related_fields
            ).get(pk=cert_object.pk)

            if (
                not force_regenerate
                and cert.status == model.STATUS_GENERATED
                and cert.file_en
                and cert.file_fa
            ):
                return cert

            cert.status = model.STATUS_PENDING
            cert.generation_error = None
            cert.save(update_fields=['status', 'generation_error'])

            context, render_specs = build_render_plan(cert)

            # Render every language before writing anything to storage. A template
            # error therefore cannot leave a half-generated certificate behind.
            rendered_files = [
                (
                    file_field_name,
                    file_name_pattern,
                    _render_self_contained_svg(template_name, context).encode('utf-8'),
                )
                for template_name, file_field_name, file_name_pattern in render_specs
            ]

            old_files = []
            for file_field_name, _, _ in rendered_files:
                old_field = getattr(cert, file_field_name)
                if old_field.name:
                    old_files.append((old_field.storage, old_field.name))

            for file_field_name, file_name_pattern, svg_content in rendered_files:
                file_field = getattr(cert, file_field_name)
                saved_name = file_field.storage.save(
                    _candidate_name(cert, file_name_pattern),
                    ContentFile(svg_content),
                )
                saved_files.append((file_field.storage, saved_name))
                setattr(cert, file_field_name, saved_name)

            cert.status = model.STATUS_GENERATED
            cert.generation_error = None
            cert.save()

            new_names = {name for _, name in saved_files}
            replaced_files = [
                (storage, name)
                for storage, name in old_files
                if name not in new_names
            ]
            transaction.on_commit(lambda: _delete_files(replaced_files))

        return cert
    except Exception as error:
        # Storage is not transactional, so explicitly undo any files saved by a
        # failed attempt before recording the failure in a separate transaction.
        _delete_files(saved_files)
        _record_failure(model, cert_object, error)
        raise


def _presentation_render_plan(cert):
    presentation = cert.enrollment.presentation
    context = {
        'name': cert.name_on_certificate,
        'presentation_title': presentation.title,
        'event_end_date': presentation.end_time.strftime('%B %d, %Y'),
        'verification_link': (
            f"{settings.FRONTEND_URL}/certificates/presentation/"
            f"{cert.verification_id}"
        ),
    }
    return context, [
        (
            'certificate-en.svg',
            'file_en',
            'certificates/presentations/pres-en_{pk}_{ts}_{uuid}.svg',
        ),
        (
            'certificate-fa.svg',
            'file_fa',
            'certificates/presentations/pres-fa_{pk}_{ts}_{uuid}.svg',
        ),
    ]


def generate_presentation_cert(cert: Certificate, force_regenerate: bool = False) -> Certificate:
    """Generate both presentation SVGs with locking and failure recording."""
    return _generate_certificate_files(
        cert,
        Certificate,
        ('enrollment__presentation',),
        _presentation_render_plan,
        force_regenerate=force_regenerate,
    )


def _solo_render_plan(cert):
    if cert.registration_type != 'solo' or not cert.solo_registration:
        raise ValueError("This certificate does not belong to a solo registration.")

    solo_competition = cert.solo_registration.solo_competition
    context = {
        'name': cert.name_on_certificate,
        'competition_title': solo_competition.title,
        'ranking': cert.ranking,
        'event_end_date': solo_competition.end_datetime.strftime('%B %d, %Y'),
        'verification_link': (
            f"{settings.FRONTEND_URL}/certificates/competition/"
            f"{cert.verification_id}"
        ),
    }
    return context, [
        (
            'competition-certificate-en.svg',
            'file_en',
            'certificates/competitions/solo-en_{pk}_{ts}_{uuid}.svg',
        ),
        (
            'competition-certificate-fa.svg',
            'file_fa',
            'certificates/competitions/solo-fa_{pk}_{ts}_{uuid}.svg',
        ),
    ]


def generate_solo_cert(
    cert: CompetitionCertificate,
    force_regenerate: bool = False,
) -> CompetitionCertificate:
    """Generate both solo-competition SVGs with locking and failure recording."""
    return _generate_certificate_files(
        cert,
        CompetitionCertificate,
        ('solo_registration__solo_competition',),
        _solo_render_plan,
        force_regenerate=force_regenerate,
    )


def _group_render_plan(cert):
    if cert.registration_type != 'group' or not cert.team:
        raise ValueError(
            "Certificate is not a group competition or missing team reference."
        )

    team = cert.team
    group_competition = team.group_competition
    if not group_competition:
        raise ValueError("The certificate team has no competition.")

    members = [
        membership.user.get_full_name() or membership.user.email
        for membership in team.memberships.filter(
            status=TeamMembership.STATUS_ACCEPTED,
        ).select_related('user')
    ]
    context = {
        'team_name': team.name,
        'team_members': members,
        'competition_title': group_competition.title,
        'ranking': cert.ranking,
        'event_end_date': group_competition.end_datetime.strftime('%B %d, %Y'),
        'verification_link': (
            f"{settings.FRONTEND_URL}/certificates/competition/"
            f"{cert.verification_id}"
        ),
    }
    return context, [
        (
            'group-certificate-en.svg',
            'file_en',
            'certificates/competitions/group-en_{pk}_{ts}_{uuid}.svg',
        ),
        (
            'group-certificate-fa.svg',
            'file_fa',
            'certificates/competitions/group-fa_{pk}_{ts}_{uuid}.svg',
        ),
    ]


def generate_group_cert(
    cert: CompetitionCertificate,
    force_regenerate: bool = False,
) -> CompetitionCertificate:
    """Generate both group-competition SVGs with locking and failure recording."""
    return _generate_certificate_files(
        cert,
        CompetitionCertificate,
        ('team__group_competition',),
        _group_render_plan,
        force_regenerate=force_regenerate,
    )


def generate_cert_for_object(cert_object, force_regenerate: bool = False):
    """Unified entry point for certificate generation."""
    if isinstance(cert_object, Certificate):
        return generate_presentation_cert(
            cert_object,
            force_regenerate=force_regenerate,
        )
    if isinstance(cert_object, CompetitionCertificate):
        if cert_object.registration_type == 'solo':
            return generate_solo_cert(
                cert_object,
                force_regenerate=force_regenerate,
            )
        if cert_object.registration_type == 'group':
            return generate_group_cert(
                cert_object,
                force_regenerate=force_regenerate,
            )
    raise ValueError(f"Unsupported certificate object type: {type(cert_object)}")
