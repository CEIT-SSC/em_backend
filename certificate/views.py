from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound, PermissionDenied
from .utils import generate_certificate
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer
from events.models import PresentationEnrollment
from .models import Certificate, CompetitionCertificate
from django.db.models import Q
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
            enrollment = PresentationEnrollment.objects.select_related(
                'presentation', 'certificate', 'user'
            ).get(
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

        # If files missing, generate them using the presentation certificate util
        if not cert.file_en or not cert.file_fa:
            # Import inside method to avoid potential circular imports
            from .utils import generate_presentation_certificate
            generate_presentation_certificate(cert, self.request)

            # Refresh instance so serializer sees new file names/urls
            cert.refresh_from_db()

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
    description=(
        "Allows a user (solo competitor or team leader) to request a certificate for a completed competition. "
        "Files are generated later after admin enters ranking and verifies."
    ),
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
        registration_id = serializer.validated_data['registration_id']
        name = serializer.validated_data['name']
        now = timezone.now()

        if registration_type == "solo":
            try:
                registration = SoloCompetitionRegistration.objects.get(
                    pk=registration_id,
                    user=request.user,
                    status__in=[SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE]
                )
            except SoloCompetitionRegistration.DoesNotExist:
                return Response(
                    {'error': 'Eligible solo competition registration not found.'},
                    status=status.HTTP_404_NOT_FOUND
                )

            if hasattr(registration, 'certificate'):
                return Response(
                    {'error': 'Certificate already requested for this registration.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            CompetitionCertificate.objects.create(
                registration_type="solo",
                solo_registration=registration,
                name_on_certificate=name,
                is_verified=False
            )

        elif registration_type == "group":
            try:
                team = CompetitionTeam.objects.get(
                    pk=registration_id,
                    leader=request.user,
                    status=CompetitionTeam.STATUS_ACTIVE
                )
            except CompetitionTeam.DoesNotExist:
                return Response(
                    {'error': 'Eligible team not found or you are not the leader.'},
                    status=status.HTTP_404_NOT_FOUND
                )

            if hasattr(team, 'certificate'):
                return Response(
                    {'error': 'Certificate already requested for this team.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            CompetitionCertificate.objects.create(
                registration_type="group",
                team=team,
                name_on_certificate=name,
                is_verified=False
            )

        else:
            return Response(
                {'error': 'Invalid registration type.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {'message': 'Competition certificate requested successfully.'},
            status=status.HTTP_201_CREATED
        )


@extend_schema(
    tags=['User - Competition Certificates'],
    summary="List User's Eligible Competition Certificates",
    description=(
        "Lists all competitions where the user is eligible for a certificate. "
        "Indicates whether a certificate has already been requested. "
        "For verified certificates, SVG files will be generated on the fly if missing."
    ),
    responses={200: CompetitionCertificateSerializer(many=True)}
)
class CompetitionCertificateListView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompetitionCertificateSerializer

    def get(self, request, *args, **kwargs):
        user = request.user
        now = timezone.now()

        # Solo competitions completed by the user
        solo_regs = SoloCompetitionRegistration.objects.filter(
            user=user,
            status__in=[SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE],
            solo_competition__event__end_date__lt=now
        ).select_related('solo_competition', 'solo_competition__event')

        # Group competitions where the user is team leader
        group_teams = CompetitionTeam.objects.filter(
            leader=user,
            status=CompetitionTeam.STATUS_ACTIVE,
            group_competition__event__end_date__lt=now
        ).select_related('group_competition', 'group_competition__event')

        results = []

        # Handle solo registrations
        for reg in solo_regs:
            cert = getattr(reg, 'certificate', None)
            if cert and cert.is_verified and (not cert.file_en or not cert.file_fa):
                from .utils import generate_certificate
                generate_certificate(cert, request)

            results.append({
                "registration_type": "solo",
                "registration_id": str(reg.pk),  # For POST requests
                "certificate_id": str(cert.id) if cert else None,
                "name_on_certificate": cert.name_on_certificate if cert else None,
                "ranking": cert.ranking if cert else None,
                "file_en": cert.file_en.url if cert and cert.file_en else None,
                "file_fa": cert.file_fa.url if cert and cert.file_fa else None,
                "is_verified": cert.is_verified if cert else False,
                "requested_at": cert.requested_at if cert else None,  # FIXED: use requested_at
                "competition_title": reg.solo_competition.title,
                "event_title": reg.solo_competition.event.title
            })

        # Handle group teams
        for team in group_teams:
            cert = getattr(team, 'certificate', None)
            if cert and cert.is_verified and (not cert.file_en or not cert.file_fa):
                from .utils import generate_certificate
                generate_certificate(cert, request)

            results.append({
                "registration_type": "group",
                "registration_id": str(team.pk),  # For POST requests
                "certificate_id": str(cert.id) if cert else None,
                "name_on_certificate": cert.name_on_certificate if cert else None,
                "ranking": cert.ranking if cert else None,
                "file_en": cert.file_en.url if cert and cert.file_en else None,
                "file_fa": cert.file_fa.url if cert and cert.file_fa else None,
                "is_verified": cert.is_verified if cert else False,
                "requested_at": cert.requested_at if cert else None,  # FIXED: use requested_at
                "competition_title": team.group_competition.title,
                "event_title": team.group_competition.event.title
            })

        return Response({
            "success": True,
            "statusCode": 200,
            "message": "Request was successful.",
            "errors": {},
            "data": results
        })
