from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from rest_framework.test import APIClient
from events.models import Event, Presentation, PresentationEnrollment, SoloCompetition, SoloCompetitionRegistration
from certificate.models import Certificate, CompetitionCertificate
from certificate.services.eligibility import (
    check_presentation_eligibility,
    check_solo_competition_eligibility,
)
from certificate.services.generator import (
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
