from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import (
    Presenter, Event, Presentation,
    SoloCompetition, GroupCompetition, CompetitionTeam, TeamMembership,
    PresentationEnrollment, SoloCompetitionRegistration
)

CustomUser = get_user_model()


class PresenterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Presenter
        fields = ['id', 'name', 'email', 'bio', 'presenter_picture', 'created_at',]
        read_only_fields = ['created_at',]


class PresentationSerializer(serializers.ModelSerializer):
    presenters_details = PresenterSerializer(source='presenters', many=True, read_only=True)
    presenter_ids = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Presenter.objects.all(), source='presenters', write_only=True, required=False
    )
    event_title = serializers.CharField(source='event.title', read_only=True)

    class Meta:
        model = Presentation
        fields = [
            'id', 'event', 'event_title', 'title', 'description',
            'presenters_details', 'presenter_ids', 
            'type', 'is_online', 'location', 'online_link',
            'start_time', 'end_time', 'is_paid', 'price', 'capacity',
            'created_at',
        ]
        read_only_fields = ['event_title', 'created_at',]


class SoloCompetitionSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source='event.title', read_only=True)

    class Meta:
        model = SoloCompetition
        fields = [
            'id', 'event', 'event_title', 'title', 'description', 'start_datetime', 'end_datetime',
            'rules', 'is_paid', 'price_per_participant', 'prize_details', 'is_active',
            'max_participants', 'created_at', 
        ]
        read_only_fields = ['event_title', 'created_at', ]


class GroupCompetitionSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source='event.title', read_only=True)

    class Meta:
        model = GroupCompetition
        fields = [
            'id', 'event', 'event_title', 'title', 'description', 'start_datetime', 'end_datetime',
            'rules', 'is_paid', 'price_per_group', 'prize_details', 'is_active',
            'min_group_size', 'max_group_size', 'max_teams',
            'requires_admin_approval', 'member_verification_instructions',
            'created_at', 
        ]
        read_only_fields = ['event_title', 'created_at', ]


class TeamMembershipUserDetailSerializer(serializers.ModelSerializer):
    # Simplified user details for team listings
    class Meta:
        model = CustomUser
        fields = ['id', 'email', 'first_name', 'last_name']


class TeamMembershipSerializer(serializers.ModelSerializer):
    user_details = TeamMembershipUserDetailSerializer(source='user', read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), source='user', write_only=True
    )

    class Meta:
        model = TeamMembership
        fields = ['id', 'user_id', 'user_details', 'team', 'government_id_picture', 'joined_at']
        read_only_fields = ['joined_at']
        # 'team' is often set by parent serializer context or view logic

    def validate(self, attrs):
        team = attrs.get('team') or (self.instance and self.instance.team)
        if team and team.needs_admin_approval() and not attrs.get('government_id_picture'):
            # This validation might be better placed in a higher-level serializer
            # if government_id_picture is submitted as part of a larger team creation payload.
            # For now, if 'government_id_picture' is part of this serializer's direct input:
            # raise serializers.ValidationError({"government_id_picture": "Government ID picture is required for this competition."})
            pass  # Let view handle this based on overall submission context
        return attrs


class CompetitionTeamSerializer(serializers.ModelSerializer):
    leader_details = TeamMembershipUserDetailSerializer(source='leader', read_only=True)
    group_competition_title = serializers.CharField(source='group_competition.title', read_only=True)
    memberships = TeamMembershipSerializer(many=True, read_only=True)

    class Meta:
        model = CompetitionTeam
        fields = [
            'id', 'name', 'leader', 'leader_details', 'group_competition', 'group_competition_title',
            'status', 'is_approved_by_admin', 'admin_remarks',
            'memberships', 'created_at', 
            # 'member_emails_snapshot' is an internal field, not usually exposed directly unless needed for specific client logic
        ]
        read_only_fields = [
            'leader', 'leader_details', 'group_competition_title',
            'is_approved_by_admin', 'admin_remarks', 'memberships',
            'created_at', 'status'  # Status is usually managed by backend logic/actions
        ]


class StandardCompetitionTeamCreateSerializer(serializers.ModelSerializer):
    member_emails = serializers.ListField(
        child=serializers.EmailField(), write_only=True, required=False, allow_empty=True
    )
    name = serializers.CharField(max_length=255)

    class Meta:
        model = CompetitionTeam
        fields = ['name', 'member_emails']  # group_competition and leader will be set in the view


class VerifiedCompetitionTeamSubmitMemberDetailSerializer(serializers.Serializer):
    email = serializers.EmailField()
    government_id_picture = serializers.ImageField(required=True, allow_empty_file=False)


class VerifiedCompetitionTeamSubmitSerializer(serializers.Serializer):
    team_name = serializers.CharField(max_length=255)
    member_details = VerifiedCompetitionTeamSubmitMemberDetailSerializer(many=True, write_only=True)

    def validate_member_details(self, value):
        if not value:
            raise serializers.ValidationError("At least one member detail must be provided.")
        return value


class EventDetailSerializer(serializers.ModelSerializer):
    presentations = PresentationSerializer(many=True, read_only=True)
    solo_competitions = SoloCompetitionSerializer(many=True, read_only=True, source='solocompetition_set')
    group_competitions = GroupCompetitionSerializer(many=True, read_only=True, source='groupcompetition_set')

    class Meta:
        model = Event
        fields = [
            'id', 'title', 'description', 'start_date', 'end_date', 'is_active',
            'presentations', 'solo_competitions', 'group_competitions',
            'created_at', 
        ]
        read_only_fields = ['created_at', ]


class EventListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ['id', 'title', 'start_date', 'end_date', 'is_active']


class PresentationEnrollmentSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    presentation_title = serializers.CharField(source='presentation.title', read_only=True)

    class Meta:
        model = PresentationEnrollment
        fields = ['id', 'user', 'user_email', 'presentation', 'presentation_title', 'status', 'enrolled_at',
                  'order_item']
        read_only_fields = ['user', 'user_email', 'presentation_title', 'enrolled_at', 'order_item', 'status']


class SoloCompetitionRegistrationSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    solo_competition_title = serializers.CharField(source='solo_competition.title', read_only=True)

    class Meta:
        model = SoloCompetitionRegistration
        fields = ['id', 'user', 'user_email', 'solo_competition', 'solo_competition_title', 'status', 'registered_at',
                  'order_item']
        read_only_fields = ['user', 'user_email', 'solo_competition_title', 'registered_at', 'order_item', 'status']
