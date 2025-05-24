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
        fields = ['id', 'name', 'email', 'bio', 'presenter_picture', 'created_at']
        read_only_fields = ['created_at']


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
            'created_at'
        ]
        read_only_fields = ['event_title', 'created_at']


class SoloCompetitionSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source='event.title', read_only=True)

    class Meta:
        model = SoloCompetition
        fields = [
            'id', 'event', 'event_title', 'title', 'description', 'start_datetime', 'end_datetime',
            'rules', 'is_paid', 'price_per_participant', 'prize_details', 'is_active',
            'max_participants', 'created_at'
        ]
        read_only_fields = ['event_title', 'created_at']


class GroupCompetitionSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source='event.title', read_only=True)

    class Meta:
        model = GroupCompetition
        fields = [
            'id', 'event', 'event_title', 'title', 'description', 'start_datetime', 'end_datetime',
            'rules', 'is_paid', 'price_per_group', 'prize_details', 'is_active',
            'min_group_size', 'max_group_size', 'max_teams',
            'requires_admin_approval', 'member_verification_instructions',
            'created_at'
        ]
        read_only_fields = ['event_title', 'created_at']


class TeamMembershipUserDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'email', 'first_name', 'last_name']


class TeamMembershipSerializer(serializers.ModelSerializer):
    user_details = TeamMembershipUserDetailSerializer(source='user', read_only=True)

    class Meta:
        model = TeamMembership
        fields = ['id', 'user_details', 'government_id_picture', 'joined_at']
        read_only_fields = ['joined_at']


class CompetitionTeamSerializer(serializers.ModelSerializer):  # For Read operations
    leader_details = TeamMembershipUserDetailSerializer(source='leader', read_only=True)
    group_competition_title = serializers.CharField(source='group_competition.title', read_only=True)
    memberships = TeamMembershipSerializer(many=True, read_only=True)

    class Meta:
        model = CompetitionTeam
        fields = [
            'id', 'name', 'leader_details', 'group_competition_title',
            'status', 'is_approved_by_admin', 'admin_remarks',
            'memberships', 'created_at'
        ]
        read_only_fields = fields


class MemberDetailSubmitSerializer(serializers.Serializer):
    email = serializers.EmailField()
    government_id_picture = serializers.ImageField(required=False, allow_empty_file=True, allow_null=True)


class CompetitionTeamSubmitSerializer(serializers.Serializer):
    team_name = serializers.CharField(max_length=255)
    member_details = MemberDetailSubmitSerializer(many=True, required=True)

    def validate(self, attrs):
        team_name = attrs.get('team_name')
        member_details_list = attrs.get('member_details')

        group_competition = self.context.get('group_competition')
        if not group_competition:
            raise serializers.ValidationError("Group competition context is required.")

        leader_user = self.context.get('request').user  # Leader is the one submitting

        if CompetitionTeam.objects.filter(group_competition=group_competition, name__iexact=team_name).exists():
            raise serializers.ValidationError(
                {"team_name": f"A team with the name '{team_name}' already exists for this competition."})

        num_total_members = len(member_details_list) + 1  # +1 for the leader
        if not (group_competition.min_group_size <= num_total_members <= group_competition.max_group_size):
            raise serializers.ValidationError(
                {
                    "member_details": f"Team size (including leader) must be between {group_competition.min_group_size} and {group_competition.max_group_size}. Submitted: {num_total_members}"}
            )

        all_proposed_emails_for_check = [leader_user.email]
        validated_member_users = []

        for index, member_data in enumerate(member_details_list):
            email = member_data.get('email')
            gov_id_pic = member_data.get('government_id_picture')

            if group_competition.requires_admin_approval:
                if not gov_id_pic:
                    raise serializers.ValidationError({
                                                          f"member_details.[{index}].government_id_picture": "Government ID picture is required for this competition."})

            try:
                user = CustomUser.objects.get(email__iexact=email)
                if user == leader_user:
                    raise serializers.ValidationError(
                        {f"member_details.[{index}].email": "Leader cannot be listed as an additional member."})
                validated_member_users.append({'user_instance': user, 'government_id_picture': gov_id_pic})
                all_proposed_emails_for_check.append(user.email)
            except CustomUser.DoesNotExist:
                raise serializers.ValidationError(
                    {f"member_details.[{index}].email": f"User with email '{email}' not found."})

        if len(all_proposed_emails_for_check) != len(set(map(lambda x: x.lower(), all_proposed_emails_for_check))):
            raise serializers.ValidationError(
                {"member_details": "Duplicate emails provided for team members or leader."})

        attrs['validated_member_users'] = validated_member_users  # Pass resolved user objects to the view
        return attrs


class EventDetailSerializer(serializers.ModelSerializer):
    presentations = PresentationSerializer(many=True, read_only=True)
    solo_competitions = SoloCompetitionSerializer(many=True, read_only=True, source='solocompetition_set')
    group_competitions = GroupCompetitionSerializer(many=True, read_only=True, source='groupcompetition_set')

    class Meta:
        model = Event
        fields = [
            'id', 'title', 'description', 'start_date', 'end_date', 'is_active',
            'presentations', 'solo_competitions', 'group_competitions',
            'created_at'
        ]
        read_only_fields = ['created_at']


class EventListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ['id', 'title', 'start_date', 'end_date', 'is_active']


class PresentationEnrollmentSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    presentation_title = serializers.CharField(source='presentation.title', read_only=True)

    class Meta:
        model = PresentationEnrollment
        fields = ['id', 'user_email', 'presentation_title', 'status', 'enrolled_at', 'order_item']
        read_only_fields = fields


class SoloCompetitionRegistrationSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    solo_competition_title = serializers.CharField(source='solo_competition.title', read_only=True)

    class Meta:
        model = SoloCompetitionRegistration
        fields = ['id', 'user_email', 'solo_competition_title', 'status', 'registered_at', 'order_item']
        read_only_fields = fields
