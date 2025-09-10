from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound, PermissionDenied
from .utils import generate_certificate, generate_group_certificate, generate_presentation_certificate
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer
from events.models import PresentationEnrollment
from .models import Certificate, CompetitionCertificate
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from drf_spectacular.utils import extend_schema, OpenApiParameter
from events.models import SoloCompetitionRegistration, CompetitionTeam
from .serializers import (
    CertificateRequestSerializer,
    CertificateSerializer,
    CompletedEnrollmentSerializer,
    CompetitionCertificateRequestSerializer,
    CompetitionCertificateSerializer
)
import os
from rest_framework.generics import GenericAPIView
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
    summary="Retrieve Certificate SVG",
    description=(
        "Fetches the SVG file for a verified certificate. "
        "If the certificate is verified but the files haven't been generated, it will create them."
    ),
    responses={
        200: None,  # Will return raw SVG
        404: ApiErrorResponseSerializer,
        403: ApiErrorResponseSerializer,
    }
)
class CertificateDetailView(generics.GenericAPIView):
    # No authentication required, public access
    authentication_classes = []
    permission_classes = []

    lookup_url_kwarg = 'enrollment_pk'

    def get(self, request, *args, **kwargs):
        # Fetch enrollment by PK
        try:
            enrollment = PresentationEnrollment.objects.select_related(
                'presentation', 'certificate'
            ).get(pk=self.kwargs['enrollment_pk'])
        except PresentationEnrollment.DoesNotExist:
            raise NotFound('Enrollment not found.')

        cert = getattr(enrollment, 'certificate', None)
        if not cert:
            raise NotFound('Certificate has not been requested for this enrollment.')

        if not cert.is_verified:
            raise PermissionDenied('Certificate is not verified yet.')

        # Generate files if missing
        if not cert.file_en or not cert.file_fa:
            generate_presentation_certificate(cert, request)
            cert.refresh_from_db()

        # Determine which file to return based on lang
        lang = self.kwargs.get('lang', 'en').lower()
        file_field = cert.file_fa if lang == 'fa' else cert.file_en

        if not file_field:
            raise NotFound('Certificate file not available.')

        # Return raw SVG content
        file_content = file_field.read()
        return HttpResponse(file_content, content_type='image/svg+xml')

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
        "Allows a user (solo competitor) to request a certificate for a completed competition. "
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

        if registration_type != "solo":
            return Response(
                {'error': 'This endpoint only supports solo competition certificates.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch the solo registration
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

        # Create the certificate
        CompetitionCertificate.objects.create(
            registration_type="solo",
            solo_registration=registration,
            name_on_certificate=name,
            is_verified=False
        )

        return Response(
            {'message': 'Competition certificate requested successfully.'},
            status=status.HTTP_201_CREATED
        )



@extend_schema(
    tags=['User - Competition Certificates'],
    summary="List User's Eligible Competition Certificates",
    description=(
        "Lists all solo competitions where the user is eligible for a certificate. "
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

        # Only solo competitions
        solo_regs = SoloCompetitionRegistration.objects.filter(
            user=user,
            status__in=[SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE],
            solo_competition__event__end_date__lt=now
        ).select_related('solo_competition', 'solo_competition__event')

        results = []

        for reg in solo_regs:
            cert = getattr(reg, 'certificate', None)
            if cert and cert.is_verified and (not cert.file_en or not cert.file_fa):
                generate_certificate(cert, request)

            results.append({
                "registration_type": "solo",
                "registration_id": str(reg.pk),
                "certificate_id": str(cert.id) if cert else None,
                "name_on_certificate": cert.name_on_certificate if cert else None,
                "ranking": cert.ranking if cert else None,
                "file_en": cert.file_en.url if cert and cert.file_en else None,
                "file_fa": cert.file_fa.url if cert and cert.file_fa else None,
                "is_verified": cert.is_verified if cert else False,
                "requested_at": cert.requested_at if cert else None,
                "competition_title": reg.solo_competition.title,
                "event_title": reg.solo_competition.event.title
            })

        return Response({
            "success": True,
            "statusCode": 200,
            "message": "Request was successful.",
            "errors": {},
            "data": results
        })


@extend_schema(
    tags=['Competition Certificates'],
    summary="Retrieve Competition Certificate SVG",
    description=(
        "Fetches the SVG certificate file for a **solo competition**. "
        "Anyone can access this. Use `lang=en` or `lang=fa` to get the English or Persian version."
    ),
    parameters=[
        OpenApiParameter('pk', description='ID of the competition certificate', required=True, type=int),
        OpenApiParameter('lang', description="Language of the certificate ('fa' or 'en')", required=True, type=str),
    ],
    responses={
        200: 'image/svg+xml',
        404: 'Certificate not found or file missing',
    }
)
class CompetitionCertificateDetailView(generics.GenericAPIView):
    queryset = CompetitionCertificate.objects.all()
    lookup_field = "pk"

    def get(self, request, *args, **kwargs):
        lang = self.kwargs.get("lang")
        if lang not in ["fa", "en"]:
            raise NotFound("Invalid language. Must be 'fa' or 'en'.")

        try:
            cert = CompetitionCertificate.objects.select_related(
                "solo_registration"
            ).get(pk=self.kwargs["pk"])
        except CompetitionCertificate.DoesNotExist:
            raise NotFound("Solo competition certificate not found.")

        if cert.registration_type != "solo":
            raise NotFound("This endpoint only serves solo competition certificates.")

        # Generate file if verified but missing
        if cert.is_verified and ((lang == "fa" and not cert.file_fa) or (lang == "en" and not cert.file_en)):
            generate_certificate(cert, request)
            cert.refresh_from_db()

        file_field = cert.file_fa if lang == "fa" else cert.file_en
        if not file_field:
            raise Http404("Certificate file not available.")

        return FileResponse(file_field.open("rb"), content_type="image/svg+xml")

@extend_schema(
    tags=['Group Competition Certificates'],
    summary="Request a Group Competition Certificate",
    description=(
        "Allows any member of a team to request a certificate for a completed group competition. "
        "Files are generated later after admin verification."
    ),
    parameters=[
        OpenApiParameter('registration_id', description='ID of the group competition', required=True, type=int),
    ],
    responses={
        201: {"message": "Certificate requested successfully."},
        400: {"detail": "Errors or already requested."},
        404: {"detail": "Team not found."},
    }
)
class GroupCompetitionCertificateRequestView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, registration_id, *args, **kwargs):
        # Find the team of the current user for this group competition
        try:
            team = CompetitionTeam.objects.get(
                group_competition_id=registration_id,
                memberships__user=request.user  # ensures user is a member
            )
        except CompetitionTeam.DoesNotExist:
            return Response(
                {"detail": "Team not found or you are not a member."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if certificate already exists
        if hasattr(team, 'certificate'):
            return Response(
                {"detail": "Certificate already requested for this team."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create certificate
        cert = CompetitionCertificate.objects.create(
            registration_type="group",
            team=team,
            name_on_certificate=team.name,
            is_verified=False
        )

        return Response(
            {"message": "Group competition certificate requested successfully."},
            status=status.HTTP_201_CREATED
        )


@extend_schema(
    tags=['User - Group Competition Certificates'],
    summary="List User's Eligible Group Competition Certificates",
    description=(
        "Lists all group competitions where the user is a member of the team. "
        "Indicates whether a certificate has already been requested. "
        "For verified certificates, SVG files will be generated on the fly if missing."
    ),
    responses={200: CompetitionCertificateSerializer(many=True)}
)
class GroupCompetitionCertificateListView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompetitionCertificateSerializer

    def get(self, request, *args, **kwargs):
        user = request.user
        now = timezone.now()

        # Teams where the user is a member and competition is finished
        teams = CompetitionTeam.objects.filter(
            memberships__user=user,
            status=CompetitionTeam.STATUS_ACTIVE,
            group_competition__event__end_date__lt=now
        ).select_related("group_competition", "group_competition__event")

        results = []

        for team in teams:
            cert = getattr(team, "certificate", None)
            if cert and cert.is_verified and (not cert.file_en or not cert.file_fa):
                generate_group_certificate(cert, request)

            results.append({
                "registration_type": "group",
                "registration_id": str(team.pk),
                "certificate_id": str(cert.id) if cert else None,
                "name_on_certificate": cert.name_on_certificate if cert else None,
                "ranking": cert.ranking if cert else None,
                "file_en": cert.file_en.url if cert and cert.file_en else None,
                "file_fa": cert.file_fa.url if cert and cert.file_fa else None,
                "is_verified": cert.is_verified if cert else False,
                "requested_at": cert.requested_at if cert else None,
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


@extend_schema(
    tags=['Group Competition Certificates'],
    summary="Retrieve Group Competition Certificate SVG",
    description=(
        "Fetches the SVG certificate file for a group competition. "
        "Any team member or public can access this. "
        "Use `lang=en` or `lang=fa` to get the English or Persian version."
    ),
    parameters=[
        OpenApiParameter('pk', description='ID of the group competition', required=True, type=int),
        OpenApiParameter('lang', description="Language of the certificate ('fa' or 'en')", required=True, type=str),
    ],
    responses={
        200: 'image/svg+xml',
        404: 'Certificate not found or file missing',
    }
)
class GroupCompetitionCertificateDetailView(GenericAPIView):
    permission_classes = []  # public access

    def get(self, request, registration_id, lang='en', *args, **kwargs):
        if lang not in ['fa', 'en']:
            raise Http404("Invalid language. Must be 'fa' or 'en'.")

        # Find the team by group competition ID (public, no user filtering)
        try:
            team = CompetitionTeam.objects.get(group_competition_id=registration_id)
        except CompetitionTeam.DoesNotExist:
            raise Http404("Team not found.")

        # Get or create certificate
        cert, _ = CompetitionCertificate.objects.get_or_create(
            registration_type="group",
            team=team,
            defaults={"name_on_certificate": team.name, "is_verified": False}
        )

        # Generate SVG if verified but missing
        if cert.is_verified and ((lang == "fa" and not cert.file_fa) or (lang == "en" and not cert.file_en)):
            generate_group_certificate(cert, request)

        file_field = cert.file_fa if lang == "fa" else cert.file_en
        if not file_field:
            raise Http404("Certificate file not available.")

        return FileResponse(file_field.open("rb"), content_type="image/svg+xml")
