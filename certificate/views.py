from django.db import transaction
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import NotFound, ValidationError
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer
from events.models import PresentationEnrollment, SoloCompetitionRegistration, CompetitionTeam
from .models import Certificate, CompetitionCertificate
from .utils import generate_solo_certificate, generate_group_certificate, generate_presentation_certificate
from .serializers import (
    CertificateRequestSerializer, CertificateSerializer, CompletedEnrollmentSerializer,
    CompetitionCertificateSerializer, EligibleSoloCompetitionSerializer, EligibleGroupCompetitionSerializer,
    UnifiedCompetitionCertificateRequestSerializer,
)


class IsCertificateOwnerForEnrollment(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.enrollment.user == request.user


class IsCertificateOwnerForCompetition(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if obj.solo_registration:
            return obj.solo_registration.user == request.user
        if obj.team:
            return obj.team.memberships.filter(user=request.user).exists()
        return False


@extend_schema(
    tags=['Certificates - Presentations'],
    summary="List Eligible Presentation Enrollments",
    description="Retrieves a list of the authenticated user's presentation enrollments that have finished and are eligible for a certificate request. An enrollment is eligible if it's completed and the presentation's end time is in the past.",
    responses={
        200: get_api_response_serializer(CompletedEnrollmentSerializer(many=True)),
        401: ApiErrorResponseSerializer,
    }
)
class CompletedEnrollmentsView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompletedEnrollmentSerializer

    def get_queryset(self):
        return PresentationEnrollment.objects.filter(
            user=self.request.user, status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
            presentation__end_time__lt=timezone.now()
        ).select_related('presentation', 'certificate').order_by('-presentation__end_time')


@extend_schema(
    tags=['Certificates - Presentations'],
    summary="Request a Presentation Certificate",
    description="Allows an authenticated user to request a certificate for a completed and finished presentation enrollment. A certificate can only be requested once per enrollment.",
    parameters=[
        OpenApiParameter(name='enrollment_pk', description='The primary key of the presentation enrollment.', required=True, type=OpenApiTypes.INT, location=OpenApiParameter.PATH)
    ],
    request=CertificateRequestSerializer,
    responses={
        201: get_api_response_serializer(CertificateSerializer),
        400: ApiErrorResponseSerializer,
        401: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CertificateRequestView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CertificateSerializer

    def perform_create(self, serializer):
        enrollment_pk = self.kwargs.get('enrollment_pk')
        try:
            enrollment = PresentationEnrollment.objects.select_related('presentation').get(
                pk=enrollment_pk, user=self.request.user,
                status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE
            )
        except PresentationEnrollment.DoesNotExist:
            raise NotFound('Eligible enrollment not found.')
        if enrollment.presentation.end_time > timezone.now():
            raise ValidationError('This presentation has not ended yet.')
        if hasattr(enrollment, 'certificate'):
            raise ValidationError('A certificate has already been requested for this enrollment.')
        name_serializer = CertificateRequestSerializer(data=self.request.data)
        name_serializer.is_valid(raise_exception=True)
        serializer.save(enrollment=enrollment, name_on_certificate=name_serializer.validated_data['name'])


@extend_schema(
    tags=['Certificates - Presentations'],
    summary="Retrieve a User's Presentation Certificate",
    description="Fetches details for a presentation certificate by its internal ID. The user must be the owner to access this endpoint. Triggers SVG file generation on the first view if the certificate has been admin-verified.",
    parameters=[
        OpenApiParameter(name='pk', description='The primary key of the certificate.', required=True, type=OpenApiTypes.INT, location=OpenApiParameter.PATH)
    ],
    responses={
        200: get_api_response_serializer(CertificateSerializer),
        401: ApiErrorResponseSerializer,
        403: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CertificateDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsCertificateOwnerForEnrollment]
    serializer_class = CertificateSerializer
    queryset = Certificate.objects.all()
    lookup_field = 'pk'

    def get_object(self):
        with transaction.atomic():
            cert = Certificate.objects.select_for_update().get(pk=self.kwargs['pk'])
            self.check_object_permissions(self.request, cert)
            if cert.is_verified and (not cert.file_en or not cert.file_fa):
                generate_presentation_certificate(cert)
                cert.refresh_from_db()
            return cert


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="List Eligible Solo Competitions",
    description="Lists all solo competitions the authenticated user has completed and is eligible to request a certificate for. A competition is eligible if the user's registration is complete and the competition's end date is in the past.",
    responses={
        200: get_api_response_serializer(EligibleSoloCompetitionSerializer(many=True)),
        401: ApiErrorResponseSerializer,
    }
)
class CompetitionCertificateListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EligibleSoloCompetitionSerializer

    def get_queryset(self):
        return SoloCompetitionRegistration.objects.filter(
            user=self.request.user, status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
            solo_competition__end_datetime__lt=timezone.now()
        ).select_related('solo_competition__event', 'certificate').order_by('-solo_competition__start_datetime')


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="List Eligible Group Competitions",
    description="Lists all group competitions where the authenticated user is an active team member and is eligible for a certificate. A competition is eligible if the team is active and the competition's end date is in the past.",
    responses={
        200: get_api_response_serializer(EligibleGroupCompetitionSerializer(many=True)),
        401: ApiErrorResponseSerializer,
    }
)
class GroupCompetitionCertificateListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EligibleGroupCompetitionSerializer

    def get_queryset(self):
        return CompetitionTeam.objects.filter(
            memberships__user=self.request.user, status=CompetitionTeam.STATUS_ACTIVE,
            group_competition__end_datetime__lt=timezone.now()
        ).select_related('group_competition__event', 'certificate').order_by('-group_competition__start_datetime')


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="Request a Competition Certificate (Unified)",
    description="Allows a user to request a certificate for any completed competition (solo or group). For a solo competition, the user provides their name. For a group competition, any team member can request it, and the certificate is issued in the team's name.",
    request=UnifiedCompetitionCertificateRequestSerializer,
    responses={
        201: get_api_response_serializer(CompetitionCertificateSerializer),
        400: ApiErrorResponseSerializer,
        401: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class UnifiedCompetitionCertificateRequestView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompetitionCertificateSerializer

    def perform_create(self, serializer):
        request_serializer = UnifiedCompetitionCertificateRequestSerializer(data=self.request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data

        if data['registration_type'] == 'solo':
            try:
                registration = SoloCompetitionRegistration.objects.get(pk=data['registration_id'],
                                                                       user=self.request.user)
            except SoloCompetitionRegistration.DoesNotExist:
                raise NotFound('Eligible solo competition registration not found.')
            if hasattr(registration, 'certificate'):
                raise ValidationError('Certificate already requested for this registration.')
            serializer.save(registration_type="solo", solo_registration=registration, name_on_certificate=data['name'])

        elif data['registration_type'] == 'group':
            try:
                team = CompetitionTeam.objects.get(pk=data['registration_id'], memberships__user=self.request.user)
            except CompetitionTeam.DoesNotExist:
                raise NotFound('Team not found for this competition, or you are not a member.')
            if hasattr(team, 'certificate'):
                raise ValidationError('Certificate already requested for this team.')
            serializer.save(registration_type="group", team=team, name_on_certificate=team.name)


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="Retrieve a User's Competition Certificate",
    description="Fetches details for a competition certificate by its internal ID. The user must be the owner (for solo) or a team member (for group). Triggers SVG file generation on the first view if the certificate has been admin-verified.",
    parameters=[
        OpenApiParameter(name='pk', description='The primary key of the competition certificate.', required=True, type=OpenApiTypes.INT, location=OpenApiParameter.PATH)
    ],
    responses={
        200: get_api_response_serializer(CompetitionCertificateSerializer),
        401: ApiErrorResponseSerializer,
        403: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CompetitionCertificateDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, IsCertificateOwnerForCompetition]
    serializer_class = CompetitionCertificateSerializer
    queryset = CompetitionCertificate.objects.all()
    lookup_field = "pk"

    def get_object(self):
        with transaction.atomic():
            cert = CompetitionCertificate.objects.select_for_update().get(pk=self.kwargs['pk'])
            self.check_object_permissions(self.request, cert)
            if cert.is_verified and (not cert.file_en or not cert.file_fa):
                if cert.registration_type == "solo":
                    generate_solo_certificate(cert)
                elif cert.registration_type == "group":
                    generate_group_certificate(cert)
                cert.refresh_from_db()
            return cert


@extend_schema(
    tags=['Certificates - Public Verification'],
    summary="Publicly Verify a Presentation Certificate by UUID",
    description="Fetches details for a single admin-verified presentation certificate using its public, non-guessable verification ID (UUID). This endpoint is open to the public and does not require authentication.",
    parameters=[
        OpenApiParameter(name='verification_id', description='The public UUID of the certificate.', required=True, type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)
    ],
    responses={
        200: get_api_response_serializer(CertificateSerializer),
        404: ApiErrorResponseSerializer,
    }
)
class PublicCertificateVerifyView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = CertificateSerializer
    queryset = Certificate.objects.filter(is_verified=True)
    lookup_field = 'verification_id'
    lookup_url_kwarg = 'verification_id'

    def get_object(self):
        cert = super().get_object()

        if cert.is_verified and (not cert.file_en or not cert.file_fa):
            generate_presentation_certificate(cert)
            cert.refresh_from_db()

        return cert


@extend_schema(
    tags=['Certificates - Public Verification'],
    summary="Publicly Verify a Competition Certificate by UUID",
    description="Fetches details for a single admin-verified competition certificate (solo or group) using its public, non-guessable verification ID (UUID). This endpoint is open to the public and does not require authentication.",
    parameters=[
        OpenApiParameter(name='verification_id', description='The public UUID of the certificate.', required=True, type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)
    ],
    responses={
        200: get_api_response_serializer(CompetitionCertificateSerializer),
        404: ApiErrorResponseSerializer,
    }
)
class PublicCompetitionCertificateVerifyView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = CompetitionCertificateSerializer
    queryset = CompetitionCertificate.objects.filter(is_verified=True)
    lookup_field = 'verification_id'
    lookup_url_kwarg = 'verification_id'

    def get_object(self):
        cert = super().get_object()

        if cert.is_verified and (not cert.file_en or not cert.file_fa):
            if cert.registration_type == "solo":
                generate_solo_certificate(cert)
            elif cert.registration_type == "group":
                generate_group_certificate(cert)

            cert.refresh_from_db()

        return cert
