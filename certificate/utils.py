import os
import uuid
from django.utils import timezone
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from .models import CompetitionCertificate, Certificate

def _resolve_upload_to(field, instance, filename):
    """
    Return a relative path (no leading slash) like
    'certificates/competitions/2025/09/filename.svg'.
    Handles callable upload_to or string with %Y/%m.
    """
    upload_to = field.upload_to
    now = timezone.now()
    if callable(upload_to):
        rel = upload_to(instance, filename)
    else:
        rel = upload_to.replace('%Y', now.strftime('%Y')).replace('%m', now.strftime('%m'))
        rel = os.path.join(rel, filename)
    return rel.lstrip('/').replace('\\', '/')

def generate_certificate(cert: CompetitionCertificate, request):
    """
    Generate certificate SVG(s) once, embedding a correct verification URL.
    Writes file to storage directly and sets the FileField.name to the final path.
    """
    now = timezone.now()
    timestamp = now.strftime('%Y%m%d%H%M%S')
    short_uuid = uuid.uuid4().hex[:6]

    field_en = CompetitionCertificate._meta.get_field('file_en')
    field_fa = CompetitionCertificate._meta.get_field('file_fa')

    # candidate filenames
    candidate_en = f"certificate-en_{cert.pk}_{timestamp}_{short_uuid}.svg"
    candidate_fa = f"certificate-fa_{cert.pk}_{timestamp}_{short_uuid}.svg"

    storage = field_en.storage  # storage is storage backend (local or S3)

    # desired relative paths (include upload_to folder)
    desired_en_rel = _resolve_upload_to(field_en, cert, candidate_en)
    desired_fa_rel = _resolve_upload_to(field_fa, cert, candidate_fa)

    # Ask storage for final name (handles collisions and renaming behavior)
    final_en_name = storage.get_available_name(desired_en_rel)
    final_fa_name = storage.get_available_name(desired_fa_rel)

    # Build verification URLs with storage.url(final_name)
    verification_link_en = request.build_absolute_uri(storage.url(final_en_name))
    verification_link_fa = request.build_absolute_uri(storage.url(final_fa_name))

    ctx = {
        'name': cert.name_on_certificate,
        'registration_type': cert.registration_type,
        'competition_title': cert.solo_registration.solo_competition.title
            if cert.registration_type == "solo"
            else cert.team.group_competition.title,
        'ranking': cert.ranking,
        'event_title': cert.solo_registration.solo_competition.event.title
            if cert.registration_type == "solo"
            else cert.team.group_competition.event.title,
        'event_end_date': cert.solo_registration.solo_competition.event.end_date.strftime('%B %d, %Y')
            if cert.registration_type == "solo"
            else cert.team.group_competition.event.end_date.strftime('%B %d, %Y'),
        'verification_link_en': verification_link_en,
        'verification_link_fa': verification_link_fa,
    }

    # Render final SVGs (links already correct)
    svg_en = render_to_string('competition-certificate-en.svg', ctx)
    svg_fa = render_to_string('competition-certificate-fa.svg', ctx)

    # If local filesystem storage, ensure directory exists
    try:
        storage_location = getattr(storage, 'location', None)
        if storage_location:
            en_dir = os.path.join(storage_location, os.path.dirname(final_en_name))
            if en_dir and not os.path.exists(en_dir):
                os.makedirs(en_dir, exist_ok=True)
    except Exception:
        # ignore; remote storages don't need it
        pass

    # Save directly to storage (not via Field.save to avoid re-applying upload_to)
    storage.save(final_en_name, ContentFile(svg_en.encode('utf-8')))
    storage.save(final_fa_name, ContentFile(svg_fa.encode('utf-8')))

    # Attach to model fields (set the relative name that storage used)
    cert.file_en.name = final_en_name
    cert.file_fa.name = final_fa_name

    # Save model once
    cert.save()

    # Return final public URLs (optional)
    return request.build_absolute_uri(cert.file_en.url), request.build_absolute_uri(cert.file_fa.url)

def _resolve_upload_to(field, instance, filename):
    """
    Return a relative path (no leading slash) like
    'certificates/2025/09/filename.svg'.
    Handles callable upload_to or string with %Y/%m placeholders.
    """
    upload_to = field.upload_to
    now = timezone.now()
    if callable(upload_to):
        rel = upload_to(instance, filename)
    else:
        rel = upload_to.replace('%Y', now.strftime('%Y')).replace('%m', now.strftime('%m'))
        rel = os.path.join(rel, filename)
    return rel.lstrip('/').replace('\\', '/')


def generate_presentation_certificate(cert: Certificate, request):
    """
    Generate presentation certificate SVG(s) once, embedding the final verification URL.
    - Uses storage.get_available_name(...) to discover final name.
    - Renders SVG with storage.url(final_name) and saves content directly to storage.
    - Sets cert.file_en.name / cert.file_fa.name and saves the model once.
    Returns (url_en, url_fa).
    """
    now = timezone.now()
    timestamp = now.strftime('%Y%m%d%H%M%S')
    short_uuid = uuid.uuid4().hex[:6]

    # Get field objects from the model instance
    field_en = cert._meta.get_field('file_en')
    field_fa = cert._meta.get_field('file_fa')

    # Candidate filenames
    candidate_en = f"certificate-en_{getattr(cert, 'pk')}_{timestamp}_{short_uuid}.svg"
    candidate_fa = f"certificate-fa_{getattr(cert, 'pk')}_{timestamp}_{short_uuid}.svg"

    storage = field_en.storage  # use field storage (works for local or remote)

    # Desired relative paths (include upload_to folder pattern resolved)
    desired_en_rel = _resolve_upload_to(field_en, cert, candidate_en)
    desired_fa_rel = _resolve_upload_to(field_fa, cert, candidate_fa)

    # Ask storage for final names (handles collisions and provider-specific renaming)
    final_en_name = storage.get_available_name(desired_en_rel)
    final_fa_name = storage.get_available_name(desired_fa_rel)

    # Build public verification URLs using storage.url(final_name)
    verification_link_en = request.build_absolute_uri(storage.url(final_en_name))
    verification_link_fa = request.build_absolute_uri(storage.url(final_fa_name))

    # Build template context matching your SVG template
    ctx = {
        'name': cert.name_on_certificate,
        'presentation_type': 'course' if cert.grade is not None else 'presentation',
        'presentation_title': getattr(cert, 'enrollment', None) and getattr(cert.enrollment.presentation, 'title', '') or '',
        'grade': cert.grade if hasattr(cert, 'grade') else None,
        'event_title': getattr(cert, 'enrollment', None) and getattr(cert.enrollment.presentation.event, 'title', '') or '',
        'event_end_date': getattr(cert, 'enrollment', None) and getattr(cert.enrollment.presentation.end_time, 'strftime', lambda fmt: '')('%B %d, %Y') or '',
        'verification_link_en': verification_link_en,
        'verification_link_fa': verification_link_fa,
    }

    # Render SVG templates (adjust template names if yours differ)
    svg_en = render_to_string('certificate-en.svg', ctx)
    svg_fa = render_to_string('certificate-fa.svg', ctx)

    # Ensure local directory exists if using FileSystemStorage
    try:
        storage_location = getattr(storage, 'location', None)
        if storage_location:
            en_dir = os.path.join(storage_location, os.path.dirname(final_en_name))
            if en_dir and not os.path.exists(en_dir):
                os.makedirs(en_dir, exist_ok=True)
    except Exception:
        # remote storages don't require local dirs, ignore failures
        pass

    # Save content directly to storage (avoid Field.save which reapplies upload_to)
    storage.save(final_en_name, ContentFile(svg_en.encode('utf-8')))
    storage.save(final_fa_name, ContentFile(svg_fa.encode('utf-8')))

    # Attach final relative names to model fields and persist once
    cert.file_en.name = final_en_name
    cert.file_fa.name = final_fa_name
    cert.save()

    # Return final absolute URLs
    return request.build_absolute_uri(cert.file_en.url), request.build_absolute_uri(cert.file_fa.url)