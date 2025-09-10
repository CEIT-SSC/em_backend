import uuid
from django.utils import timezone
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from .models import CompetitionCertificate, Certificate
import os
from django.conf import settings

def generate_certificate(cert: CompetitionCertificate, request):
    """
    Generate certificate SVG(s) for **solo competitions only**.
    Verification links point to the verify endpoint, not storage URLs.
    """
    if not cert.solo_registration:
        raise ValueError("This certificate does not belong to a solo registration.")

    now = timezone.now()
    timestamp = now.strftime('%Y%m%d%H%M%S')
    short_uuid = uuid.uuid4().hex[:6]

    field_en = cert._meta.get_field('file_en')
    field_fa = cert._meta.get_field('file_fa')
    storage = field_en.storage

    # Candidate filenames
    candidate_en = f"certificate-en_{cert.pk}_{timestamp}_{short_uuid}.svg"
    candidate_fa = f"certificate-fa_{cert.pk}_{timestamp}_{short_uuid}.svg"

    # Ask storage for final names
    final_en_name = storage.get_available_name(candidate_en)
    final_fa_name = storage.get_available_name(candidate_fa)

    # Verification links (solo-only)
    verification_link_en = request.build_absolute_uri(
        f"/api/certificates/competition/{cert.pk}/verify/en/"
    )
    verification_link_fa = request.build_absolute_uri(
        f"/api/certificates/competition/{cert.pk}/verify/fa/"
    )

    # Context for SVG template
    ctx = {
        'name': cert.name_on_certificate,
        'registration_type': 'solo',
        'competition_title': cert.solo_registration.solo_competition.title,
        'ranking': cert.ranking,
        'event_title': cert.solo_registration.solo_competition.event.title,
        'event_end_date': cert.solo_registration.solo_competition.event.end_date.strftime('%B %d, %Y'),
        'verification_link_en': verification_link_en,
        'verification_link_fa': verification_link_fa,
    }

    # Render SVGs
    svg_en = render_to_string('competition-certificate-en.svg', ctx)
    svg_fa = render_to_string('competition-certificate-fa.svg', ctx)

    # Ensure local directories exist if needed
    try:
        storage_location = getattr(storage, 'location', None)
        if storage_location:
            os.makedirs(os.path.join(storage_location), exist_ok=True)
    except Exception:
        pass

    # Save files
    storage.save(final_en_name, ContentFile(svg_en.encode('utf-8')))
    storage.save(final_fa_name, ContentFile(svg_fa.encode('utf-8')))

    # Attach to model fields and save
    cert.file_en.name = final_en_name
    cert.file_fa.name = final_fa_name
    cert.save()

    return verification_link_en, verification_link_fa


def generate_presentation_certificate(cert: Certificate, request):
    """
    Generate presentation certificate SVGs and save to storage.
    Verification links point to API endpoint, not storage path.
    """
    now = timezone.now()
    timestamp = now.strftime('%Y%m%d%H%M%S')
    short_uuid = uuid.uuid4().hex[:6]

    field_en = cert._meta.get_field('file_en')
    field_fa = cert._meta.get_field('file_fa')
    storage = field_en.storage

    # Candidate filenames
    final_en_name = f"certificates/{cert.pk}/certificate-en_{timestamp}_{short_uuid}.svg"
    final_fa_name = f"certificates/{cert.pk}/certificate-fa_{timestamp}_{short_uuid}.svg"

    # Build verification links using your API endpoint
    verification_link_en = request.build_absolute_uri(f"/api/certificates/{cert.enrollment.pk}/verify/en/")
    verification_link_fa = request.build_absolute_uri(f"/api/certificates/{cert.enrollment.pk}/verify/fa/")

    ctx = {
        'name': cert.name_on_certificate,
        'presentation_title': getattr(cert.enrollment.presentation, 'title', ''),
        'event_title': getattr(cert.enrollment.presentation.event, 'title', ''),
        'event_end_date': getattr(cert.enrollment.presentation.event.end_date, 'strftime', lambda fmt: '')('%B %d, %Y'),
        'verification_link_en': verification_link_en,
        'verification_link_fa': verification_link_fa,
    }

    # Render SVGs
    svg_en = render_to_string('certificate-en.svg', ctx)
    svg_fa = render_to_string('certificate-fa.svg', ctx)

    # Save directly to storage
    storage.save(final_en_name, ContentFile(svg_en.encode('utf-8')))
    storage.save(final_fa_name, ContentFile(svg_fa.encode('utf-8')))

    # Update model
    cert.file_en.name = final_en_name
    cert.file_fa.name = final_fa_name
    cert.save()

    return verification_link_en, verification_link_fa


def generate_group_certificate(cert, request):
    """
    Generate both English and Persian group competition certificates.
    Verification links point to the group competition verify endpoint using the registration/team ID.
    """
    if cert.registration_type != "group" or not cert.team:
        raise ValueError("Certificate is not a group competition or missing team reference.")

    team = cert.team
    members = team.memberships.select_related('user').all()
    member_names_list = [m.user.get_full_name() or m.user.email for m in members]
    member_names_str = ", ".join(member_names_list)

    now = timezone.now()
    timestamp = now.strftime('%Y%m%d%H%M%S')
    short_uuid = uuid.uuid4().hex[:6]

    field_en = cert._meta.get_field('file_en')
    field_fa = cert._meta.get_field('file_fa')
    storage = field_en.storage

    # Candidate filenames
    candidate_en = f"certificates/group_{cert.pk}_{timestamp}_{short_uuid}_en.svg"
    candidate_fa = f"certificates/group_{cert.pk}_{timestamp}_{short_uuid}_fa.svg"

    final_en_name = storage.get_available_name(candidate_en)
    final_fa_name = storage.get_available_name(candidate_fa)

    links = {}
    for lang, final_name in [('en', final_en_name), ('fa', final_fa_name)]:
        # Verification link uses the registration/team ID
        verification_link = request.build_absolute_uri(
            f"/api/certificates/group-competition/{team.id}/verify/{lang}/"
        )
        links[lang] = verification_link

        # Context for template
        if lang == 'en':
            ctx = {
                'name': f"{team.name} ({member_names_str})",
                'registration_type': 'group',
                'competition_title': team.group_competition.title,
                'ranking': cert.ranking,
                'event_title': team.group_competition.event.title,
                'event_end_date': team.group_competition.event.end_date.strftime('%B %d, %Y'),
                'verification_link_en': verification_link,
                'verification_link_fa': '',
            }
        else:  # Persian
            ctx = {
                'team_name': team.name,
                'team_members': member_names_list,  # list passed for join filter in template
                'registration_type': 'group',
                'competition_title': team.group_competition.title,
                'ranking': cert.ranking,
                'event_title': team.group_competition.event.title,
                'event_end_date': team.group_competition.event.end_date.strftime('%B %d, %Y'),
                'verification_link_en': '',
                'verification_link_fa': verification_link,
            }

        # Render SVG template
        template_name = f"group-certificate-{lang}.svg"
        svg_content = render_to_string(template_name, ctx)

        # Ensure directory exists
        try:
            storage_location = getattr(storage, 'location', None)
            if storage_location:
                os.makedirs(storage_location, exist_ok=True)
        except Exception:
            pass

        # Save file
        storage.save(final_name, ContentFile(svg_content.encode('utf-8')))

        # Attach to model field
        if lang == 'en':
            cert.file_en.name = final_name
        else:
            cert.file_fa.name = final_name

    cert.save()
    return links
