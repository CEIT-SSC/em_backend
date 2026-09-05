from rest_framework import generics, permissions, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer, get_paginated_response_serializer
from events.models import TeamMembership
from .models import Certificate, CompetitionCertificate
from .utils import generate_solo_certificate, generate_group_certificate, generate_presentation_certificate
from .services import (
    get_user_eligible_presentations,
    check_presentation_eligibility,
    get_user_eligible_solo_competitions,
    check_solo_competition_eligibility,
    get_user_eligible_group_competitions,
    check_group_competition_eligibility,
)
from .serializers import (
    CertificateRequestSerializer, CertificateSerializer, PublicCertificateSerializer,
    CompletedEnrollmentSerializer, CompetitionCertificateSerializer, PublicCompetitionCertificateSerializer,
    EligibleSoloCompetitionSerializer, EligibleGroupCompetitionSerializer,
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
            return obj.team.memberships.filter(
                user=request.user,
                status=TeamMembership.STATUS_ACCEPTED,
            ).exists()
        return False


@extend_schema(
    tags=['Certificates - Presentations'],
    summary="List Eligible Presentation Enrollments",
    description="Retrieves a list of the authenticated user's presentation enrollments that have finished and are eligible for a certificate request. An enrollment is eligible if it's completed and the presentation's end time is in the past.",
    responses={
        200: get_paginated_response_serializer(CompletedEnrollmentSerializer),
        401: ApiErrorResponseSerializer,
    }
)
class CompletedEnrollmentsView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompletedEnrollmentSerializer

    def get_queryset(self):
        return get_user_eligible_presentations(self.request.user)


@extend_schema(
    tags=['Certificates - Presentations'],
    summary="Request a Presentation Certificate",
    description="Allows an authenticated user to request a certificate for a completed and finished presentation enrollment. Repeated requests return the existing certificate.",
    parameters=[
        OpenApiParameter(name='enrollment_pk', description='The primary key of the presentation enrollment.', required=True, type=OpenApiTypes.INT, location=OpenApiParameter.PATH)
    ],
    request=CertificateRequestSerializer,
    responses={
        200: get_api_response_serializer(CertificateSerializer),
        201: get_api_response_serializer(CertificateSerializer),
        400: ApiErrorResponseSerializer,
        401: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CertificateRequestView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CertificateSerializer

    def create(self, request, *args, **kwargs):
        name_serializer = CertificateRequestSerializer(data=request.data)
        name_serializer.is_valid(raise_exception=True)
        enrollment_pk = self.kwargs.get('enrollment_pk')

        existing = Certificate.objects.filter(
            enrollment_id=enrollment_pk,
            enrollment__user=request.user,
        ).first()
        if existing:
            return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)

        enrollment, err_msg = check_presentation_eligibility(
            request.user,
            enrollment_pk,
            reject_existing=False,
        )
        if err_msg:
            if "not found" in err_msg.lower():
                raise NotFound(err_msg)
            raise ValidationError(err_msg)

        certificate, created = Certificate.objects.get_or_create(
            enrollment=enrollment,
            defaults={'name_on_certificate': name_serializer.validated_data['name']},
        )
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(self.get_serializer(certificate).data, status=response_status)


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
        cert = Certificate.objects.get(pk=self.kwargs['pk'])
        self.check_object_permissions(self.request, cert)
        if cert.is_verified and (not cert.file_en or not cert.file_fa or cert.status != Certificate.STATUS_GENERATED):
            cert = generate_presentation_certificate(cert)
        return cert


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="List Eligible Solo Competitions",
    description="Lists all solo competitions the authenticated user has completed and is eligible to request a certificate for. A competition is eligible if the user's registration is complete and the competition's end date is in the past.",
    responses={
        200: get_paginated_response_serializer(EligibleSoloCompetitionSerializer),
        401: ApiErrorResponseSerializer,
    }
)
class CompetitionCertificateListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EligibleSoloCompetitionSerializer

    def get_queryset(self):
        return get_user_eligible_solo_competitions(self.request.user)


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="List Eligible Group Competitions",
    description="Lists all group competitions where the authenticated user is an active team member and is eligible for a certificate. A competition is eligible if the team is active and the competition's end date is in the past.",
    responses={
        200: get_paginated_response_serializer(EligibleGroupCompetitionSerializer),
        401: ApiErrorResponseSerializer,
    }
)
class GroupCompetitionCertificateListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EligibleGroupCompetitionSerializer

    def get_queryset(self):
        return get_user_eligible_group_competitions(self.request.user)


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="Request a Competition Certificate (Unified)",
    description="Allows a user to request a certificate for any completed competition (solo or group). Repeated requests return the existing certificate. For a solo competition, the user provides their name. For a group competition, any accepted team member can request it, and the certificate is issued in the team's name.",
    request=UnifiedCompetitionCertificateRequestSerializer,
    responses={
        200: get_api_response_serializer(CompetitionCertificateSerializer),
        201: get_api_response_serializer(CompetitionCertificateSerializer),
        400: ApiErrorResponseSerializer,
        401: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class UnifiedCompetitionCertificateRequestView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompetitionCertificateSerializer

    def create(self, request, *args, **kwargs):
        request_serializer = UnifiedCompetitionCertificateRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data

        if data['registration_type'] == 'solo':
            existing = CompetitionCertificate.objects.filter(
                solo_registration_id=data['registration_id'],
                solo_registration__user=request.user,
            ).first()
            if existing:
                return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)

            registration, err_msg = check_solo_competition_eligibility(
                request.user,
                data['registration_id'],
                reject_existing=False,
            )
            if err_msg:
                if "not found" in err_msg.lower():
                    raise NotFound(err_msg)
                raise ValidationError(err_msg)
            certificate, created = CompetitionCertificate.objects.get_or_create(
                solo_registration=registration,
                defaults={
                    'registration_type': 'solo',
                    'name_on_certificate': data['name'],
                },
            )

        elif data['registration_type'] == 'group':
            existing = CompetitionCertificate.objects.filter(
                team_id=data['registration_id'],
                team__memberships__user=request.user,
                team__memberships__status=TeamMembership.STATUS_ACCEPTED,
            ).first()
            if existing:
                return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)

            team, err_msg = check_group_competition_eligibility(
                request.user,
                data['registration_id'],
                reject_existing=False,
            )
            if err_msg:
                if "not found" in err_msg.lower():
                    raise NotFound(err_msg)
                raise ValidationError(err_msg)
            certificate, created = CompetitionCertificate.objects.get_or_create(
                team=team,
                defaults={
                    'registration_type': 'group',
                    'name_on_certificate': team.name,
                },
            )

        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(self.get_serializer(certificate).data, status=response_status)


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
        cert = CompetitionCertificate.objects.get(pk=self.kwargs['pk'])
        self.check_object_permissions(self.request, cert)
        if cert.is_verified and (not cert.file_en or not cert.file_fa or cert.status != CompetitionCertificate.STATUS_GENERATED):
            if cert.registration_type == "solo":
                cert = generate_solo_certificate(cert)
            elif cert.registration_type == "group":
                cert = generate_group_certificate(cert)
        return cert


@extend_schema(
    tags=['Certificates - Public Verification'],
    summary="Publicly Verify a Presentation Certificate by UUID",
    description="Fetches details for a single admin-verified presentation certificate using its public, non-guessable verification ID (UUID). This endpoint is open to the public and does not require authentication.",
    parameters=[
        OpenApiParameter(name='verification_id', description='The public UUID of the certificate.', required=True, type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)
    ],
    responses={
        200: get_api_response_serializer(PublicCertificateSerializer),
        404: ApiErrorResponseSerializer,
    }
)
class PublicCertificateVerifyView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicCertificateSerializer
    queryset = Certificate.objects.filter(
        is_verified=True,
        status=Certificate.STATUS_GENERATED,
    )
    lookup_field = 'verification_id'
    lookup_url_kwarg = 'verification_id'

@extend_schema(
    tags=['Certificates - Public Verification'],
    summary="Publicly Verify a Competition Certificate by UUID",
    description="Fetches details for a single admin-verified competition certificate (solo or group) using its public, non-guessable verification ID (UUID). This endpoint is open to the public and does not require authentication.",
    parameters=[
        OpenApiParameter(name='verification_id', description='The public UUID of the certificate.', required=True, type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)
    ],
    responses={
        200: get_api_response_serializer(PublicCompetitionCertificateSerializer),
        404: ApiErrorResponseSerializer,
    }
)
class PublicCompetitionCertificateVerifyView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = PublicCompetitionCertificateSerializer
    queryset = CompetitionCertificate.objects.filter(
        is_verified=True,
        status=CompetitionCertificate.STATUS_GENERATED,
    )
    lookup_field = 'verification_id'
    lookup_url_kwarg = 'verification_id'
