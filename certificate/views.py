from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse
from rest_framework.exceptions import NotFound
from events.models import PresentationEnrollment
from .models import Certificate
from .serializers import (
    CertificateRequestSerializer,
    CertificateSerializer,
    ErrorResponseSerializer,
    CompletedEnrollmentSerializer,
    MessageResponseSerializer
)


@extend_schema_view(
    post=extend_schema(
        summary="Request a certificate",
        description="Request and generate a certificate after event has passed",
        request=CertificateRequestSerializer,
        responses={
            201: MessageResponseSerializer,
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

        if enrollment.presentation.end_time > timezone.now():
            return Response({'error': 'Presentation has not ended yet.'}, status=status.HTTP_400_BAD_REQUEST)

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
        svg_template = render_to_string('certificate.svg')
        for key, value in ctx.items():
            svg_template = svg_template.replace(f'{{{key}}}', str(value))

        cert = Certificate.objects.create(
            enrollment=enrollment,
            name_on_certificate=name,
        )
        filename = f"certificate_{enrollment.pk}_{timezone.now().strftime('%Y%m%d%H%M%S')}.svg"
        cert.file.save(filename, ContentFile(svg_template.encode('utf-8')))
        cert.save()

        return Response(
            {'message': 'Certificate generated successfully. It is now pending verification.'},
            status=status.HTTP_201_CREATED
        )


@extend_schema_view(
    get=extend_schema(
        summary="Get your verified certificate details",
        description="Retrieve the certificate details, including the file URL, if it has been verified by an admin.",
        responses={
            200: CertificateSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        }
    )
)
class CertificateDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CertificateSerializer
    lookup_url_kwarg = 'enrollment_pk'

    def get_object(self):
        try:
            enrollment = PresentationEnrollment.objects.select_related('certificate').get(
                pk=self.kwargs['enrollment_pk'],
                user=self.request.user
            )
        except PresentationEnrollment.DoesNotExist:
            raise NotFound('Enrollment not found.')

        cert = getattr(enrollment, 'certificate', None)

        if not cert:
            raise NotFound('Certificate has not been requested for this enrollment.')

        if not cert.is_verified:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Certificate is not verified yet.')

        return cert


@extend_schema_view(
    get=extend_schema(
        summary="List completed enrollments for certificates",
        description="List your completed & past enrollments with their certificate status.",
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
            presentation__end_time__lt=now
        ).select_related('presentation', 'certificate').order_by('-presentation__end_time')
