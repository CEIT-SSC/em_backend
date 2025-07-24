from django_typomatic import ts_interface
from rest_framework import serializers
from events.models import PresentationEnrollment
from .models import Certificate


@ts_interface()
class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()


@ts_interface()
class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


@ts_interface()
class CertificateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)


@ts_interface()
class CertificateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Certificate
        fields = ['id', 'name_on_certificate', 'file', 'is_verified', 'requested_at']
        read_only_fields = ['id', 'file', 'requested_at']


@ts_interface()
class CompletedEnrollmentSerializer(serializers.ModelSerializer):
    certificate = CertificateSerializer(read_only=True)
    presentation_title = serializers.CharField(source='presentation.title', read_only=True)
    can_request_certificate = serializers.BooleanField()

    class Meta:
        model = PresentationEnrollment
        fields = [
            'id',
            'presentation_title',
            'can_request_certificate',
            'certificate',
        ]