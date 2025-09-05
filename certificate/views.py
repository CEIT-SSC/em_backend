from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound, PermissionDenied

from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer
from events.models import PresentationEnrollment
from .models import Certificate, CompetitionCertificate
from events.models import SoloCompetitionRegistration, CompetitionTeam
from .serializers import (
    CertificateRequestSerializer,
    CertificateSerializer,
    ErrorResponseSerializer,
    CompletedEnrollmentSerializer,
    MessageResponseSerializer,
    CompetitionCertificateRequestSerializer,
    CompetitionCertificateSerializer
)
import os
from django.conf import settings

@extend_schema(
    tags=['User - Certificates'],
    summary="Request a Certificate",
    description="Allows an authenticated user to request a certificate for a completed and finished presentation enrollment.",
    request=CertificateRequestSerializer,
    responses={
        201: get_api_response_serializer(None),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CertificateRequestView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CertificateRequestSerializer

    def post(self, request, enrollment_pk):
        try:
            enrollment = PresentationEnrollment.objects.get(
                pk=enrollment_pk,
                user=request.user,
                status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE
            )
        except PresentationEnrollment.DoesNotExist:
            return Response({'error': 'Completed enrollment not found.'}, status=status.HTTP_404_NOT_FOUND)

        if enrollment.presentation.end_time > timezone.now():
            return Response({'error': 'Presentation has not ended yet.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data['name']

        if hasattr(enrollment, 'certificate'):
            return Response({'error': 'Certificate already requested.'}, status=status.HTTP_400_BAD_REQUEST)

        Certificate.objects.create(
            enrollment=enrollment,
            name_on_certificate=name,
            is_verified=False,  # default not verified until admin approves
        )

        return Response(
            {'message': 'Certificate requested. SVG files will be generated on certificate detail fetch.'},
            status=status.HTTP_201_CREATED
        )


@extend_schema(
    tags=['User - Certificates'],
    summary="Retrieve Certificate Details",
    description="Fetches the details of a specific certificate. If the certificate is verified and the files haven't been generated, it will create them on the fly.",
    responses={
        200: get_api_response_serializer(CertificateSerializer),
        403: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CertificateDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CertificateSerializer
    lookup_url_kwarg = 'enrollment_pk'

    def get_object(self):
        try:
            enrollment = PresentationEnrollment.objects.select_related('presentation', 'certificate', 'user').get(
                pk=self.kwargs['enrollment_pk'],
                user=self.request.user
            )
        except PresentationEnrollment.DoesNotExist:
            raise NotFound('Enrollment not found.')

        cert = getattr(enrollment, 'certificate', None)

        if not cert:
            raise NotFound('Certificate has not been requested for this enrollment.')

        if not cert.is_verified:
            raise PermissionDenied('Certificate is not verified yet.')

        if not cert.file_en or not cert.file_fa:
            cert = self._generate_certificate(enrollment)

        return cert

    def _generate_certificate(self, enrollment):
        cert = enrollment.certificate
        name = cert.name_on_certificate or enrollment.user.get_full_name() or enrollment.user.username

        ctx = {
            'grade': cert.grade,
            'presentation_type': enrollment.presentation.type,
            'name': name,
            'presentation_title': enrollment.presentation.title,
            'event_title': enrollment.presentation.event.title,
            'event_end_date': enrollment.presentation.event.end_date.strftime('%B %d, %Y'),
            'verification_link_en': cert.file_en.url if cert.file_en else '',
            'verification_link_fa': cert.file_fa.url if cert.file_fa else '',
        }

        svg_template1 = render_to_string('certificate-en.svg', ctx)
        svg_template2 = render_to_string('certificate-fa.svg', ctx)

        cert.file_en.save(
            f"certificate-en_{enrollment.pk}_{timezone.now().strftime('%Y%m%d%H%M%S')}.svg",
            ContentFile(svg_template1.encode('utf-8')),
            save=False
        )
        cert.file_fa.save(
            f"certificate-fa_{enrollment.pk}_{timezone.now().strftime('%Y%m%d%H%M%S')}.svg",
            ContentFile(svg_template2.encode('utf-8')),
            save=False
        )
        cert.save()

        return cert


@extend_schema(
    tags=['User - Certificates'],
    summary="List Completed Enrollments for Certificates",
    description="Retrieves a list of the user's presentation enrollments that are completed, finished, and eligible for a certificate request.",
    responses={
        200: get_api_response_serializer(CompletedEnrollmentSerializer)
    }
)
class CompletedEnrollmentsView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompletedEnrollmentSerializer

    def get_queryset(self):
        now = timezone.now()
        return PresentationEnrollment.objects.filter(
            user=self.request.user,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
            presentation__end_time__lt=now
        ).select_related('presentation', 'certificate').order_by('-presentation__end_time')



@extend_schema(
    tags=['User - Competition Certificates'],
    summary="Request a Competition Certificate",
    description="Allows a user (solo competitor or team leader) to request a certificate for a completed competition. "
                "Files are generated later after admin enters ranking and verifies.",
    request=CompetitionCertificateRequestSerializer,
    responses={
        201: MessageResponseSerializer,
        400: ErrorResponseSerializer,
        404: ErrorResponseSerializer,
    }
)
class CompetitionCertificateRequestView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompetitionCertificateRequestSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        registration_type = serializer.validated_data['registration_type']
        name = serializer.validated_data['name']

        if registration_type == "solo":
            try:
                registration = SoloCompetitionRegistration.objects.get(
                    pk=serializer.validated_data['registration_id'],
                    user=request.user,
                    status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE
                )
            except SoloCompetitionRegistration.DoesNotExist:
                return Response({'error': 'Completed solo competition registration not found.'},
                                status=status.HTTP_404_NOT_FOUND)

            if hasattr(registration, 'certificate'):
                return Response({'error': 'Certificate already requested.'}, status=status.HTTP_400_BAD_REQUEST)

            CompetitionCertificate.objects.create(
                registration_type="solo",
                solo_registration=registration,
                name_on_certificate=name,
                is_verified=False
            )

        elif registration_type == "group":
            try:
                team = CompetitionTeam.objects.get(
                    pk=serializer.validated_data['registration_id'],
                    leader=request.user,  # only team leader can request
                    status=CompetitionTeam.STATUS_ACTIVE
                )
            except CompetitionTeam.DoesNotExist:
                return Response({'error': 'Active team not found or you are not the leader.'},
                                status=status.HTTP_404_NOT_FOUND)

            if hasattr(team, 'certificate'):
                return Response({'error': 'Certificate already requested.'}, status=status.HTTP_400_BAD_REQUEST)

            CompetitionCertificate.objects.create(
                registration_type="group",
                team=team,
                name_on_certificate=name,
                is_verified=False
            )

        else:
            return Response({'error': 'Invalid registration type.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'message': 'Competition certificate requested.'}, status=status.HTTP_201_CREATED)

@extend_schema(
    tags=['User - Competition Certificates'],
    summary="Retrieve Competition Certificate Details",
    description="Fetches a competition certificate. Generates the SVG with correct verification link if missing.",
    responses={200: CompetitionCertificateSerializer, 403: CompetitionCertificateSerializer}
)
class CompetitionCertificateDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompetitionCertificateSerializer
    lookup_field = "id"
    queryset = CompetitionCertificate.objects.all()

    def get_object(self):
        cert = super().get_object()

        # Ownership check
        if cert.registration_type == "solo" and cert.solo_registration.user != self.request.user:
            raise PermissionDenied("You are not allowed to access this certificate.")
        if cert.registration_type == "group" and cert.team.leader != self.request.user:
            raise PermissionDenied("Only the team leader can view this certificate.")

        if not cert.is_verified:
            raise PermissionDenied("Certificate is not verified yet.")

        # Generate SVG if missing
        if not cert.file_en or not cert.file_fa:
            self._generate_certificate(cert)

        return cert

    def _generate_certificate(self, cert: CompetitionCertificate):
        """
        Generates certificate SVG files with correct verification link.
        Uses the upload_to path in the FileField to avoid duplicates.
        """
        timestamp = timezone.now().strftime('%Y%m%d%H%M%S')

        # Only the filename; Django handles upload_to
        en_filename = f"certificate-en_{cert.pk}_{timestamp}.svg"
        fa_filename = f"certificate-fa_{cert.pk}_{timestamp}.svg"

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
        }

        # Render SVG without verification link first
        svg_en = render_to_string('competition-certificate-en.svg', ctx)
        svg_fa = render_to_string('competition-certificate-fa.svg', ctx)

        cert.file_en.save(en_filename, ContentFile(svg_en.encode('utf-8')), save=False)
        cert.file_fa.save(fa_filename, ContentFile(svg_fa.encode('utf-8')), save=False)
        cert.save()  # now cert.file_en.url exists

        # Add absolute verification link to context
        ctx['verification_link_en'] = self.request.build_absolute_uri(cert.file_en.url)
        ctx['verification_link_fa'] = self.request.build_absolute_uri(cert.file_fa.url)

        # Re-render SVG with verification link embedded
        svg_en = render_to_string('competition-certificate-en.svg', ctx)
        svg_fa = render_to_string('competition-certificate-fa.svg', ctx)

        cert.file_en.save(en_filename, ContentFile(svg_en.encode('utf-8')), save=False)
        cert.file_fa.save(fa_filename, ContentFile(svg_fa.encode('utf-8')), save=False)
        cert.save()


@extend_schema(
    tags=['User - Competition Certificates'],
    summary="List User's Competition Certificates",
    description="Lists all competition certificates where the user is a solo participant or team leader. Verified certificates will generate SVG files if missing.",
    responses={200: CompetitionCertificateSerializer(many=True)}
)
class CompetitionCertificateListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompetitionCertificateSerializer

    def get_queryset(self):
        # Solo certificates
        solo_qs = CompetitionCertificate.objects.filter(
            registration_type="solo",
            solo_registration__user=self.request.user
        ).select_related(
            'solo_registration', 'solo_registration__solo_competition', 'solo_registration__solo_competition__event'
        )

        # Group certificates
        group_qs = CompetitionCertificate.objects.filter(
            registration_type="group",
            team__leader=self.request.user
        ).select_related(
            'team', 'team__group_competition', 'team__group_competition__event'
        )

        queryset = (solo_qs | group_qs).order_by('-id')

        # Generate missing SVGs for verified certificates
        for cert in queryset:
            if cert.is_verified and (not cert.file_en or not cert.file_fa):
                self._generate_certificate(cert)

        return queryset

    def _generate_certificate(self, cert: CompetitionCertificate):
        # Same as detail view to avoid duplication
        timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
        en_filename = f"certificate-en_{cert.pk}_{timestamp}.svg"
        fa_filename = f"certificate-fa_{cert.pk}_{timestamp}.svg"

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
        }

        # Render SVGs
        svg_en = render_to_string('competition-certificate-en.svg', ctx)
        svg_fa = render_to_string('competition-certificate-fa.svg', ctx)

        cert.file_en.save(en_filename, ContentFile(svg_en.encode('utf-8')), save=False)
        cert.file_fa.save(fa_filename, ContentFile(svg_fa.encode('utf-8')), save=False)
        cert.save()

        # Add verification link and re-render once more
        ctx['verification_link_en'] = self.request.build_absolute_uri(cert.file_en.url)
        ctx['verification_link_fa'] = self.request.build_absolute_uri(cert.file_fa.url)

        svg_en = render_to_string('competition-certificate-en.svg', ctx)
        svg_fa = render_to_string('competition-certificate-fa.svg', ctx)

        cert.file_en.save(en_filename, ContentFile(svg_en.encode('utf-8')), save=False)
        cert.file_fa.save(fa_filename, ContentFile(svg_fa.encode('utf-8')), save=False)
        cert.save()