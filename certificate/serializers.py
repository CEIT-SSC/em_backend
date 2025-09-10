from django_typomatic import ts_interface
from rest_framework import serializers
from events.models import PresentationEnrollment, SoloCompetitionRegistration, CompetitionTeam
from .models import Certificate, CompetitionCertificate


@ts_interface()
class CertificateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, help_text="The full name to be printed on the certificate.")


@ts_interface()
class CertificateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Certificate
        fields = [
            'id', 'verification_id', 'enrollment', 'name_on_certificate',
            'file_en', 'file_fa', 'is_verified', 'requested_at', 'grade'
        ]
        read_only_fields = fields


@ts_interface()
class CompletedEnrollmentSerializer(serializers.ModelSerializer):
    presentation_title = serializers.CharField(source='presentation.title', read_only=True)
    certificate_id = serializers.IntegerField(source='certificate.id', read_only=True)
    certificate_verification_id = serializers.UUIDField(source='certificate.verification_id', read_only=True)
    is_certificate_verified = serializers.BooleanField(source='certificate.is_verified', read_only=True)

    class Meta:
        model = PresentationEnrollment
        fields = [
            'id', 'presentation_title', 'certificate_id',
            'certificate_verification_id', 'is_certificate_verified',
        ]


@ts_interface()
class UnifiedCompetitionCertificateRequestSerializer(serializers.Serializer):
    registration_type = serializers.ChoiceField(choices=["solo", "group"])
    registration_id = serializers.IntegerField()
    name = serializers.CharField(max_length=255, required=False, help_text="Required for 'solo' type.")

    def validate(self, attrs):
        if attrs['registration_type'] == 'solo' and not attrs.get('name'):
            raise serializers.ValidationError({"name": "This field is required for solo certificates."})
        return attrs


@ts_interface()
class CompetitionCertificateSerializer(serializers.ModelSerializer):
    competition_title = serializers.SerializerMethodField()
    event_title = serializers.SerializerMethodField()

    class Meta:
        model = CompetitionCertificate
        fields = [
            'id', 'verification_id', 'registration_type', 'name_on_certificate', 'ranking',
            'file_en', 'file_fa', 'is_verified', 'requested_at',
            'competition_title', 'event_title'
        ]
        read_only_fields = fields

    def get_competition_title(self, obj: CompetitionCertificate) -> str | None:
        if obj.solo_registration:
            return obj.solo_registration.solo_competition.title
        if obj.team:
            return obj.team.group_competition.title
        return None

    def get_event_title(self, obj: CompetitionCertificate) -> str | None:
        if obj.solo_registration:
            return obj.solo_registration.solo_competition.event.title
        if obj.team:
            return obj.team.group_competition.event.title
        return None


@ts_interface()
class EligibleSoloCompetitionSerializer(serializers.ModelSerializer):
    competition_title = serializers.CharField(source='solo_competition.title', read_only=True)
    event_title = serializers.CharField(source='solo_competition.event.title', read_only=True)
    certificate = CompetitionCertificateSerializer(read_only=True)

    class Meta:
        model = SoloCompetitionRegistration
        fields = [ 'id', 'competition_title', 'event_title', 'certificate' ]


@ts_interface()
class EligibleGroupCompetitionSerializer(serializers.ModelSerializer):
    competition_title = serializers.CharField(source='group_competition.title', read_only=True)
    event_title = serializers.CharField(source='group_competition.event.title', read_only=True)
    certificate = CompetitionCertificateSerializer(read_only=True)

    class Meta:
        model = CompetitionTeam
        fields = [ 'id', 'name', 'competition_title', 'event_title', 'certificate' ]