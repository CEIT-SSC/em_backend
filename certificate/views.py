from django.db import transaction
from django.utils import timezone
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from drf_spectacular.utils import extend_schema
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer
from events.models import PresentationEnrollment, SoloCompetitionRegistration, CompetitionTeam
from .models import Certificate, CompetitionCertificate
from .utils import generate_certificate, generate_group_certificate, generate_presentation_certificate
from .serializers import (
    CertificateRequestSerializer,
    CertificateSerializer,
    CompletedEnrollmentSerializer,
    CompetitionCertificateRequestSerializer,
    CompetitionCertificateSerializer,
    EligibleSoloCompetitionSerializer,
    EligibleGroupCompetitionSerializer,
)


@extend_schema(
    tags=['Certificates - Presentations'],
    summary="List Eligible Presentation Enrollments",
    description="Retrieves a list of the user's presentation enrollments that have finished and are eligible for a certificate request.",
    responses={200: get_api_response_serializer(CompletedEnrollmentSerializer(many=True))}
)
class CompletedEnrollmentsView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompletedEnrollmentSerializer

    def get_queryset(self):
        return PresentationEnrollment.objects.filter(
            user=self.request.user,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
            presentation__end_time__lt=timezone.now()
        ).select_related('presentation', 'certificate').order_by('-presentation__end_time')


@extend_schema(
    tags=['Certificates - Presentations'],
    summary="Request a Presentation Certificate",
    description="Allows a user to request a certificate for a completed and finished presentation enrollment.",
    request=CertificateRequestSerializer,
    responses={
        201: get_api_response_serializer(CertificateSerializer),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CertificateRequestView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CertificateSerializer

    def perform_create(self, serializer):
        enrollment_pk = self.kwargs.get('enrollment_pk')
        try:
            enrollment = PresentationEnrollment.objects.get(
                pk=enrollment_pk,
                user=self.request.user,
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

        serializer.save(
            enrollment=enrollment,
            name_on_certificate=name_serializer.validated_data['name']
        )


@extend_schema(
    tags=['Certificates - Presentations'],
    summary="Retrieve a Presentation Certificate",
    description="Publicly fetches the details for a verified presentation certificate, including file links. If files are missing, they will be generated on-the-fly.",
    responses={
        200: get_api_response_serializer(CertificateSerializer),
        403: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CertificateDetailView(generics.RetrieveAPIView):
    permission_classes = []
    serializer_class = CertificateSerializer
    queryset = Certificate.objects.all()
    lookup_field = 'pk'

    def get_object(self):
        with transaction.atomic():
            cert = super().get_object()
            if not cert.is_verified:
                raise PermissionDenied('This certificate has not been verified by an administrator.')

            if not cert.file_en or not cert.file_fa:
                generate_presentation_certificate(cert, self.request)
                cert.refresh_from_db()

            return cert


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="List Eligible Solo Competitions",
    description="Lists all solo competitions a user has completed and is eligible to request a certificate for.",
    responses={200: get_api_response_serializer(EligibleSoloCompetitionSerializer(many=True))}
)
class CompetitionCertificateListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EligibleSoloCompetitionSerializer

    def get_queryset(self):
        return SoloCompetitionRegistration.objects.filter(
            user=self.request.user,
            status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
            solo_competition__event__end_date__lt=timezone.now()
        ).select_related(
            'solo_competition__event', 'certificate'
        ).order_by('-solo_competition__start_datetime')


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="Request a Solo Competition Certificate",
    description="Allows a user to request a certificate for a completed solo competition.",
    request=CompetitionCertificateRequestSerializer,
    responses={
        201: get_api_response_serializer(CompetitionCertificateSerializer),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CompetitionCertificateRequestView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompetitionCertificateSerializer

    def perform_create(self, serializer):
        request_serializer = CompetitionCertificateRequestSerializer(data=self.request.data)
        request_serializer.is_valid(raise_exception=True)

        registration_id = request_serializer.validated_data['registration_id']
        name = request_serializer.validated_data['name']

        try:
            registration = SoloCompetitionRegistration.objects.get(
                pk=registration_id,
                user=self.request.user,
                status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE
            )
        except SoloCompetitionRegistration.DoesNotExist:
            raise NotFound('Eligible solo competition registration not found.')

        if hasattr(registration, 'certificate'):
            raise ValidationError('A certificate has already been requested for this registration.')

        serializer.save(
            registration_type="solo",
            solo_registration=registration,
            name_on_certificate=name
        )


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="List Eligible Group Competitions",
    description="Lists all group competitions where the user is a team member and is eligible for a certificate.",
    responses={200: get_api_response_serializer(EligibleGroupCompetitionSerializer(many=True))}
)
class GroupCompetitionCertificateListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EligibleGroupCompetitionSerializer

    def get_queryset(self):
        return CompetitionTeam.objects.filter(
            memberships__user=self.request.user,
            status=CompetitionTeam.STATUS_ACTIVE,
            group_competition__event__end_date__lt=timezone.now()
        ).select_related(
            'group_competition__event', 'certificate'
        ).order_by('-group_competition__start_datetime')


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="Request a Group Competition Certificate",
    description="Allows any team member to request a certificate on behalf of their team for a completed group competition.",
    responses={
        201: get_api_response_serializer(CompetitionCertificateSerializer),
        400: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class GroupCompetitionCertificateRequestView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompetitionCertificateSerializer

    def perform_create(self, serializer):
        registration_id = self.kwargs.get('registration_id')
        try:
            team = CompetitionTeam.objects.get(
                group_competition_id=registration_id,
                memberships__user=self.request.user
            )
        except CompetitionTeam.DoesNotExist:
            raise NotFound('Team not found for this competition, or you are not a member.')

        if hasattr(team, 'certificate'):
            raise ValidationError('A certificate has already been requested for this team.')

        serializer.save(
            registration_type="group",
            team=team,
            name_on_certificate=team.name
        )


@extend_schema(
    tags=['Certificates - Competitions'],
    summary="Retrieve a Competition Certificate",
    description="Publicly fetches details for a verified competition certificate (solo or group), including file links. If files are missing, they are generated on-the-fly.",
    responses={
        200: get_api_response_serializer(CompetitionCertificateSerializer),
        403: ApiErrorResponseSerializer,
        404: ApiErrorResponseSerializer,
    }
)
class CompetitionCertificateDetailView(generics.RetrieveAPIView):
    permission_classes = []
    serializer_class = CompetitionCertificateSerializer
    queryset = CompetitionCertificate.objects.all()
    lookup_field = "pk"

    def get_object(self):
        with transaction.atomic():
            cert = super().get_object()
            if not cert.is_verified:
                raise PermissionDenied("This certificate has not been verified by an administrator.")

            if not cert.file_en or not cert.file_fa:
                if cert.registration_type == "solo":
                    generate_certificate(cert, self.request)
                elif cert.registration_type == "group":
                    generate_group_certificate(cert, self.request)
                cert.refresh_from_db()

            return cert