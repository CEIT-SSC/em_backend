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
from .models import Certificate
from .serializers import (
    CertificateRequestSerializer,
    CertificateSerializer,
    CompletedEnrollmentSerializer,
)


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

    @staticmethod
    def _generate_certificate(enrollment):
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
