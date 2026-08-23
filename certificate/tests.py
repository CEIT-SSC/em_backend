import importlib
import tempfile
import xml.etree.ElementTree as ElementTree
from unittest.mock import patch

from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from rest_framework.test import APIClient
from events.models import (
    CompetitionTeam,
    Event,
    GroupCompetition,
    Presentation,
    PresentationEnrollment,
    SoloCompetition,
    SoloCompetitionRegistration,
    TeamMembership,
)
from certificate.models import Certificate, CompetitionCertificate
from certificate.services.eligibility import (
    check_presentation_eligibility,
    check_solo_competition_eligibility,
)
from certificate.services.generator import (
    _render_self_contained_svg,
    generate_presentation_cert,
    generate_solo_cert,
)
from certificate.admin import CertificateAdmin
from django.contrib.admin.sites import AdminSite

User = get_user_model()


class CertificateServicesTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(email="student@aut.ac.ir", password="password123")
        self.event = Event.objects.create(
            id=1,
            title="Tech Conference",
            description="Event description",
            manager="Manager Name",
            start_date=timezone.now() - timedelta(days=10),
            end_date=timezone.now() + timedelta(days=10)
        )
        
        # Ended presentation
        self.past_presentation = Presentation.objects.create(
            event=self.event,
            title="AI Workshop",
            description="Workshop description",
            start_time=timezone.now() - timedelta(days=2),
            end_time=timezone.now() - timedelta(days=1),
            capacity=50,
            price=0
        )
        self.enrollment = PresentationEnrollment.objects.create(
            user=self.user,
            presentation=self.past_presentation,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE
        )

    def test_presentation_eligibility_and_status(self):
        enrollment, err = check_presentation_eligibility(self.user, self.enrollment.pk)
        self.assertIsNone(err)
        self.assertEqual(enrollment, self.enrollment)

        cert = Certificate.objects.create(
            enrollment=self.enrollment,
            name_on_certificate="Ali Rezaei",
            is_verified=True
        )
        self.assertEqual(cert.status, Certificate.STATUS_PENDING)
        self.assertIsNone(cert.generation_error)

        generated_cert = generate_presentation_cert(cert)
        self.assertEqual(generated_cert.status, Certificate.STATUS_GENERATED)
        self.assertIsNone(generated_cert.generation_error)
        self.assertTrue(bool(generated_cert.file_en))
        self.assertTrue(bool(generated_cert.file_fa))

        _, err_duplicate = check_presentation_eligibility(self.user, self.enrollment.pk)
        self.assertIn("already been requested", err_duplicate)

    def test_competition_certificate_status(self):
        solo_comp = SoloCompetition.objects.create(
            event=self.event,
            title="Coding Challenge",
            description="Coding competition",
            start_datetime=timezone.now() - timedelta(days=5),
            end_datetime=timezone.now() - timedelta(days=3),
            max_participants=100
        )
        registration = SoloCompetitionRegistration.objects.create(
            user=self.user,
            solo_competition=solo_comp,
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE
        )

        reg, err = check_solo_competition_eligibility(self.user, registration.pk)
        self.assertIsNone(err)

        comp_cert = CompetitionCertificate.objects.create(
            registration_type="solo",
            solo_registration=registration,
            name_on_certificate="Ali Rezaei",
            is_verified=True,
            ranking=1
        )
        self.assertEqual(comp_cert.status, CompetitionCertificate.STATUS_PENDING)

        generated_comp_cert = generate_solo_cert(comp_cert)
        self.assertEqual(generated_comp_cert.status, CompetitionCertificate.STATUS_GENERATED)
        self.assertIsNone(generated_comp_cert.generation_error)

    def test_public_verification_view(self):
        cert = Certificate.objects.create(
            enrollment=self.enrollment,
            name_on_certificate="Ali Rezaei",
            is_verified=True
        )
        cert = generate_presentation_cert(cert)
        url = reverse('certificates:public-presentation-certificate-verify', kwargs={'verification_id': cert.verification_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name_on_certificate'], "Ali Rezaei")
        self.assertEqual(response.data['presentation_title'], "AI Workshop")
        # Ensure sensitive enrollment ID is NOT exposed in public endpoint
        self.assertNotIn('enrollment', response.data)

    def test_admin_actions(self):
        cert = Certificate.objects.create(
            enrollment=self.enrollment,
            name_on_certificate="Ali Rezaei",
            is_verified=False
        )
        site = AdminSite()
        admin_obj = CertificateAdmin(Certificate, site)
        from django.contrib.messages.storage.cookie import CookieStorage

        request = self.factory.post('/admin/certificate/certificate/')
        setattr(request, '_messages', CookieStorage(request))

        qs = Certificate.objects.filter(pk=cert.pk)
        admin_obj.verify_and_generate_certificates(request, qs)
        cert.refresh_from_db()
        self.assertTrue(cert.is_verified)
        self.assertEqual(cert.status, Certificate.STATUS_GENERATED)


class CertificateAcceptanceTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="acceptance@aut.ac.ir",
            password="password123",
        )
        self.event = Event.objects.create(
            id=2,
            title="Acceptance Event",
            description="Certificate acceptance tests",
            manager="Manager",
            start_date=timezone.now() - timedelta(days=10),
            end_date=timezone.now() + timedelta(days=10),
        )
        self.client.force_authenticate(self.user)

    def create_past_enrollment(self):
        presentation = Presentation.objects.create(
            event=self.event,
            title="Completed Workshop",
            description="Completed presentation",
            start_time=timezone.now() - timedelta(days=2),
            end_time=timezone.now() - timedelta(days=1),
            capacity=20,
            price=0,
        )
        return PresentationEnrollment.objects.create(
            user=self.user,
            presentation=presentation,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
        )

    def test_repeated_presentation_request_returns_existing_certificate(self):
        enrollment = self.create_past_enrollment()
        url = reverse(
            'certificates:presentation-certificate-request',
            kwargs={'enrollment_pk': enrollment.pk},
        )

        first = self.client.post(url, {'name': 'Acceptance User'}, format='json')
        second = self.client.post(url, {'name': 'Acceptance User'}, format='json')

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(
            Certificate.objects.filter(enrollment=enrollment).count(),
            1,
        )

    def test_solo_request_requires_completion_and_ended_competition(self):
        competition = SoloCompetition.objects.create(
            event=self.event,
            title="Future Solo",
            description="Future solo competition",
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=2),
        )
        registration = SoloCompetitionRegistration.objects.create(
            user=self.user,
            solo_competition=competition,
            status=SoloCompetitionRegistration.STATUS_PENDING_PAYMENT,
        )
        url = reverse('certificates:competition-certificate-request')
        payload = {
            'registration_type': 'solo',
            'registration_id': registration.pk,
            'name': 'Acceptance User',
        }

        pending_response = self.client.post(url, payload, format='json')
        self.assertEqual(pending_response.status_code, 400)

        registration.status = SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE
        registration.save(update_fields=['status'])
        future_response = self.client.post(url, payload, format='json')
        self.assertEqual(future_response.status_code, 400)
        self.assertFalse(CompetitionCertificate.objects.exists())

    def test_group_request_requires_accepted_member_and_ended_competition(self):
        competition = GroupCompetition.objects.create(
            event=self.event,
            title="Future Group",
            description="Future group competition",
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=2),
            max_group_size=4,
        )
        team = CompetitionTeam.objects.create(
            name="Acceptance Team",
            leader=self.user,
            group_competition=competition,
            status=CompetitionTeam.STATUS_ACTIVE,
        )
        membership = TeamMembership.objects.create(
            user=self.user,
            team=team,
            status=TeamMembership.STATUS_PENDING,
        )
        url = reverse('certificates:competition-certificate-request')
        payload = {
            'registration_type': 'group',
            'registration_id': team.pk,
        }

        pending_member_response = self.client.post(url, payload, format='json')
        self.assertEqual(pending_member_response.status_code, 404)

        membership.status = TeamMembership.STATUS_ACCEPTED
        membership.save(update_fields=['status'])
        future_response = self.client.post(url, payload, format='json')
        self.assertEqual(future_response.status_code, 400)

        competition.end_datetime = timezone.now() - timedelta(minutes=1)
        competition.save(update_fields=['end_datetime'])
        eligible_response = self.client.post(url, payload, format='json')
        self.assertEqual(eligible_response.status_code, 201)

        repeated_response = self.client.post(url, payload, format='json')
        self.assertEqual(repeated_response.status_code, 200)
        self.assertEqual(CompetitionCertificate.objects.count(), 1)

    def test_failed_generation_can_retry_without_partial_files(self):
        enrollment = self.create_past_enrollment()
        certificate = Certificate.objects.create(
            enrollment=enrollment,
            name_on_certificate="Acceptance User",
            is_verified=True,
        )

        with patch(
            'certificate.services.generator.render_to_string',
            side_effect=[b'<svg/>'.decode(), RuntimeError('Persian render failed')],
        ):
            with self.assertRaises(RuntimeError):
                generate_presentation_cert(certificate)

        certificate.refresh_from_db()
        self.assertEqual(certificate.status, Certificate.STATUS_FAILED)
        self.assertFalse(certificate.file_en)
        self.assertFalse(certificate.file_fa)
        self.assertIn('Persian render failed', certificate.generation_error)

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                certificate = generate_presentation_cert(certificate)
                self.assertEqual(certificate.status, Certificate.STATUS_GENERATED)
                self.assertTrue(certificate.file_en.storage.exists(certificate.file_en.name))
                self.assertTrue(certificate.file_fa.storage.exists(certificate.file_fa.name))

    def test_regeneration_removes_replaced_files_after_commit(self):
        enrollment = self.create_past_enrollment()
        certificate = Certificate.objects.create(
            enrollment=enrollment,
            name_on_certificate="Acceptance User",
            is_verified=True,
        )

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                certificate = generate_presentation_cert(certificate)
                old_names = (certificate.file_en.name, certificate.file_fa.name)
                storage = certificate.file_en.storage

                with self.captureOnCommitCallbacks(execute=True):
                    regenerated = generate_presentation_cert(
                        certificate,
                        force_regenerate=True,
                    )

                self.assertNotEqual(old_names[0], regenerated.file_en.name)
                self.assertNotEqual(old_names[1], regenerated.file_fa.name)
                self.assertFalse(storage.exists(old_names[0]))
                self.assertFalse(storage.exists(old_names[1]))
                self.assertTrue(storage.exists(regenerated.file_en.name))
                self.assertTrue(storage.exists(regenerated.file_fa.name))

    def test_generated_svg_embeds_static_images(self):
        enrollment = self.create_past_enrollment()
        certificate = Certificate.objects.create(
            enrollment=enrollment,
            name_on_certificate="Acceptance User",
            is_verified=True,
        )

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                certificate = generate_presentation_cert(certificate)
                with certificate.file_en.storage.open(
                    certificate.file_en.name,
                    'rb',
                ) as certificate_file:
                    svg_content = certificate_file.read().decode('utf-8')

                self.assertNotIn('/static/certificates/', svg_content)
                self.assertEqual(svg_content.count('data:image/png;base64,'), 3)

    def test_all_certificate_templates_render_as_valid_xml(self):
        template_contexts = {
            'certificate-en.svg': {
                'name': 'Moein & Test',
                'presentation_title': 'AI <Security>',
            },
            'certificate-fa.svg': {
                'name': 'معین عنایتی',
                'presentation_title': 'هوش مصنوعی و امنیت',
            },
            'competition-certificate-en.svg': {
                'name': 'Moein & Test',
                'competition_title': 'AI <Challenge>',
                'ranking': 1,
            },
            'competition-certificate-fa.svg': {
                'name': 'معین عنایتی',
                'competition_title': 'مسابقه هوش مصنوعی',
                'ranking': 1,
            },
            'group-certificate-en.svg': {
                'team_name': 'Team & Friends',
                'team_members': ['Moein', 'Ali'],
                'competition_title': 'AI <Challenge>',
                'ranking': 1,
            },
            'group-certificate-fa.svg': {
                'team_name': 'تیم آزمون',
                'team_members': ['معین', 'علی'],
                'competition_title': 'مسابقه هوش مصنوعی',
                'ranking': 1,
            },
        }

        common_context = {
            'event_end_date': 'August 23, 2026',
            'verification_link': 'https://example.test/verify/123',
        }
        for template_name, context in template_contexts.items():
            with self.subTest(template=template_name):
                svg_content = _render_self_contained_svg(
                    template_name,
                    {**common_context, **context},
                )
                root = ElementTree.fromstring(svg_content)
                images = root.findall('.//{http://www.w3.org/2000/svg}image')

                self.assertEqual(len(images), 3)
                self.assertNotIn('/static/certificates/', svg_content)

    def test_second_storage_failure_cleans_first_new_file(self):
        enrollment = self.create_past_enrollment()
        certificate = Certificate.objects.create(
            enrollment=enrollment,
            name_on_certificate="Acceptance User",
            is_verified=True,
        )

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                storage = certificate.file_en.storage
                real_save = storage.save
                saved_names = []

                def fail_second_save(name, content, max_length=None):
                    if saved_names:
                        raise OSError('Second storage write failed')
                    saved_name = real_save(name, content, max_length=max_length)
                    saved_names.append(saved_name)
                    return saved_name

                with patch.object(storage, 'save', side_effect=fail_second_save):
                    with self.assertRaises(OSError):
                        generate_presentation_cert(certificate)

                certificate.refresh_from_db()
                self.assertEqual(certificate.status, Certificate.STATUS_FAILED)
                self.assertFalse(certificate.file_en)
                self.assertFalse(certificate.file_fa)
                self.assertEqual(len(saved_names), 1)
                self.assertFalse(storage.exists(saved_names[0]))

    def test_status_migration_backfills_certificates_with_both_files(self):
        enrollment = self.create_past_enrollment()
        certificate = Certificate.objects.create(
            enrollment=enrollment,
            name_on_certificate="Existing Certificate",
            file_en='certificates/existing-en.svg',
            file_fa='certificates/existing-fa.svg',
            status=Certificate.STATUS_PENDING,
        )
        migration = importlib.import_module(
            'certificate.migrations.'
            '0004_backfill_certificate_statuses'
        )

        migration.backfill_generated_status(
            importlib.import_module('django.apps').apps,
            None,
        )

        certificate.refresh_from_db()
        self.assertEqual(certificate.status, Certificate.STATUS_GENERATED)

    def test_public_verification_hides_failed_and_unverified_certificates(self):
        enrollment = self.create_past_enrollment()
        certificate = Certificate.objects.create(
            enrollment=enrollment,
            name_on_certificate="Acceptance User",
            is_verified=True,
            status=Certificate.STATUS_FAILED,
            generation_error="Internal renderer detail",
        )
        url = reverse(
            'certificates:public-presentation-certificate-verify',
            kwargs={'verification_id': certificate.verification_id},
        )

        failed_response = self.client.get(url)
        self.assertEqual(failed_response.status_code, 404)
        self.assertNotIn(b'Internal renderer detail', failed_response.content)

        certificate.status = Certificate.STATUS_GENERATED
        certificate.is_verified = False
        certificate.save(update_fields=['status', 'is_verified'])
        unverified_response = self.client.get(url)
        self.assertEqual(unverified_response.status_code, 404)
