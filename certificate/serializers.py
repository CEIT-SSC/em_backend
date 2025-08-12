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
        fields = ['id', 'name_on_certificate', 'file_en', 'file_fa', 'is_verified', 'requested_at']
        read_only_fields = ['id', 'file', 'requested_at']


@ts_interface()
class CompletedEnrollmentSerializer(serializers.ModelSerializer):
    presentation_title = serializers.CharField(source='presentation.title', read_only=True)
    has_certificate = serializers.SerializerMethodField()
    is_certificate_verified = serializers.SerializerMethodField()

    class Meta:
        model = PresentationEnrollment
        fields = [
            'id',
            'presentation_title',
            'has_certificate',
            'is_certificate_verified',
        ]

    def get_has_certificate(self, obj: PresentationEnrollment) -> bool:
        return hasattr(obj, 'certificate')

    def get_is_certificate_verified(self, obj: PresentationEnrollment) -> bool:
        if hasattr(obj, 'certificate'):
            return obj.certificate.is_verified
        return False