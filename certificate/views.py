from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse

from events.models import PresentationEnrollment
from .models import Certificate
from .serializers import (
    CertificateRequestSerializer,
    CertificateSerializer,
    ErrorResponseSerializer, CompletedEnrollmentSerializer,
)

@extend_schema_view(
    post=extend_schema(
        summary="Request a certificate",
        description="Request and generate a certificate after event has passed",
        request=CertificateRequestSerializer,
        responses={
            201: CertificateSerializer,
            400: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        }
    )
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

        if enrollment.presentation.event.end_date > timezone.now():
            return Response({'error': 'Event has not ended yet.'}, status=status.HTTP_400_BAD_REQUEST)

        if hasattr(enrollment, 'certificate'):
            return Response({'error': 'Certificate already requested.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data['name']

        ctx = {
            'name': name,
            'presentation_title': enrollment.presentation.title,
            'event_title': enrollment.presentation.event.title,
            'event_end_date': enrollment.presentation.event.end_date.strftime('%B %d, %Y'),
        }
        svg_content = render_to_string('certificate.svg', ctx)

        cert = Certificate.objects.create(
            enrollment=enrollment,
            name_on_certificate=name,
        )
        filename = f"certificate_{enrollment.pk}_{timezone.now().strftime('%Y%m%d%H%M%S')}.svg"
        cert.file.save(filename, ContentFile(svg_content.encode('utf-8')))
        cert.save()

        return Response(CertificateSerializer(cert).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        summary="Get your verified certificate",
        description="Retrieve the certificate if it has been verified by admin",
        responses={
            200: CertificateSerializer,
            404: ErrorResponseSerializer,
        }
    )
)
class CertificateDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CertificateSerializer
    lookup_url_kwarg = 'enrollment_pk'

    def get_object(self):
        enrollment = PresentationEnrollment.objects.get(
            pk=self.kwargs['enrollment_pk'],
            user=self.request.user
        )
        cert = getattr(enrollment, 'certificate', None)
        if not cert or not cert.is_verified:
            from rest_framework.exceptions import NotFound
            raise NotFound('Certificate not available or not verified.')
        return cert


@extend_schema_view(
    get=extend_schema(
        summary="List completed enrollments",
        description="List your completed & past enrollments with certificate status",
        responses=CompletedEnrollmentSerializer(many=True)
    )
)
class CompletedEnrollmentsView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompletedEnrollmentSerializer

    def get_queryset(self):
        now = timezone.now()
        return PresentationEnrollment.objects.filter(
            user=self.request.user,
            status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
            presentation__end_date__lt=now
        ).select_related('presentation', 'certificate')
