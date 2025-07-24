from rest_framework import serializers
from .models import CertificateRequest

class CertificateRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = CertificateRequest
        fields = ['id', 'enrollment', 'requested_at', 'is_approved']
        read_only_fields = ['requested_at', 'is_approved']
