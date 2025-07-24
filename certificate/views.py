from rest_framework import generics, permissions, status
from rest_framework.response import Response
from .models import CertificateRequest
from .serializers import CertificateRequestSerializer
from events.models import PresentationEnrollment
from .utils import generate_certificate_image
from django.http import FileResponse
from io import BytesIO
from django.contrib.staticfiles import finders
import os
from em_backend import settings

# Create certificate request
class CertificateRequestCreateView(generics.CreateAPIView):
    serializer_class = CertificateRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        enrollment_id = self.request.data.get('enrollment')
        enrollment = PresentationEnrollment.objects.get(id=enrollment_id, user=self.request.user)
        serializer.save(enrollment=enrollment)


# List all requests for admin
class CertificateRequestListView(generics.ListAPIView):
    queryset = CertificateRequest.objects.all()
    serializer_class = CertificateRequestSerializer
    permission_classes = [permissions.IsAdminUser]


# Approve or reject
class CertificateRequestApproveView(generics.UpdateAPIView):
    serializer_class = CertificateRequestSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = CertificateRequest.objects.all()

    def patch(self, request, *args, **kwargs):
        obj = self.get_object()
        obj.is_approved = request.data.get('is_approved')
        obj.save()
        return Response({'status': 'updated'})


# Download certificate image (if approved)
class CertificateDownloadView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        cert_request = CertificateRequest.objects.get(id=pk, enrollment__user=request.user)
        if cert_request.is_approved:
            image_bytes = generate_certificate_image(cert_request)
            return FileResponse(image_bytes, content_type='image/png', filename='certificate.png')
        return Response({'detail': 'Not approved yet'}, status=status.HTTP_403_FORBIDDEN)
