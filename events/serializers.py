from django_typomatic import ts_interface
from rest_framework import serializers
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field, OpenApiTypes
from .models import (
    Presenter, Event, Presentation,
    SoloCompetition, GroupCompetition, CompetitionTeam, TeamMembership,
    TeamContent, ContentImage, ContentLike, ContentComment,
    PresentationEnrollment, SoloCompetitionRegistration, Post, CompetitionTeamRegistration
)

CustomUser = get_user_model()


@ts_interface()
class PresenterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Presenter
        fields = ['id', 'name', 'email', 'bio', 'presenter_picture', 'created_at', ]
        read_only_fields = ['created_at', ]


@ts_interface()
class PresentationSerializer(serializers.ModelSerializer):
    presenters_details = PresenterSerializer(source='presenters', many=True, read_only=True)
    presenter_ids = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Presenter.objects.all(), source='presenters', write_only=True, required=False
    )
    event_title = serializers.CharField(source='event.title', read_only=True, allow_null=True)
    remaining_capacity = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Presentation
        fields = [
            'id', 'event', 'event_title', 'title', 'description',
            'presenters_details', 'presenter_ids',
            'type', 'level', 'is_online', 'location', 'online_link',
            'start_time', 'end_time', 'is_paid', 'price', 'capacity',
            'is_active', 'poster', 'remaining_capacity', "requirements", "contents", "timing"
        ]
        read_only_fields = ['event_title', ]

    @extend_schema_field(OpenApiTypes.INT)
    def get_remaining_capacity(self, obj):
        if obj.capacity is None:
            return None
        taken = obj.enrollments.filter(
            status__in=[
                PresentationEnrollment.STATUS_COMPLETED_OR_FREE,
                PresentationEnrollment.STATUS_PENDING_PAYMENT,
            ]
        ).count()
        return max(obj.capacity - taken, 0)


@ts_interface()
class SoloCompetitionSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source='event.title', read_only=True, allow_null=True)
    remaining_capacity = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = SoloCompetition
        fields = [
            'id', 'event', 'event_title', 'title', 'description', 'start_datetime', 'end_datetime', 'poster',
            'rules', 'is_paid', 'price_per_participant', 'prize_details', 'is_active',
            'max_participants', 'created_at', 'remaining_capacity',
        ]
        read_only_fields = ['event_title', 'created_at', ]

    @extend_schema_field(OpenApiTypes.INT)
    def get_remaining_capacity(self, obj):
        if obj.max_participants is None:
            return None
        taken = getattr(obj, 'reserved_count', None)
        if taken is None:
            taken = obj.registrations.filter(status__in=[
                SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE,
                SoloCompetitionRegistration.STATUS_PENDING_PAYMENT,
            ]).count()
        return max(obj.max_participants - taken, 0)


@ts_interface()
class GroupCompetitionSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source='event.title', read_only=True, allow_null=True)
    remaining_capacity = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = GroupCompetition
        fields = [
            'id', 'event', 'event_title', 'title', 'description', 'start_datetime', 'end_datetime',
            'rules', 'is_paid', 'price_per_member', 'prize_details', 'is_active', 'poster',
            'min_group_size', 'max_group_size', 'max_teams',
            'requires_admin_approval', 'member_verification_instructions',
            'allow_content_submission',
            'created_at', 'remaining_capacity',
        ]
        read_only_fields = ['event_title', 'created_at', ]

    @extend_schema_field(OpenApiTypes.INT)
    def get_remaining_capacity(self, obj):
        if obj.max_teams is None:
            return None
        taken = getattr(obj, 'reserved_count', None)
        if taken is None:
            taken = obj.registrations.filter(status__in=CompetitionTeamRegistration.RESERVED_STATUSES).count()
        return max(obj.max_teams - taken, 0)


@ts_interface()
class TeamMembershipUserDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'email', 'first_name', 'last_name', 'profile_picture']


@ts_interface()
class TeamMembershipSerializer(serializers.ModelSerializer):
    user_details = TeamMembershipUserDetailSerializer(source='user', read_only=True)

    class Meta:
        model = TeamMembership
        fields = ['id', 'user_details', 'status', 'joined_at', 'invited_by', 'expires_at', 'responded_at']
        read_only_fields = fields


@ts_interface()
class ContentImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContentImage
        fields = ['id', 'image', 'caption', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_at']


@ts_interface()
class TeamContentSerializer(serializers.ModelSerializer):
    images = ContentImageSerializer(many=True, read_only=True)
    uploaded_images = serializers.ListField(
        child=serializers.ImageField(max_length=1000000, allow_empty_file=False, use_url=False),
        write_only=True, required=False
    )
    team_name = serializers.CharField(source='team.name', read_only=True)
    competition_id = serializers.IntegerField(source='registration.competition_id', read_only=True)
    likes_count = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    is_liked_by_requester = serializers.SerializerMethodField()

    class Meta:
        model = TeamContent
        fields = [
            'id', 'team', 'team_name', 'registration', 'competition_id', 'description', 'file_link', 'images', 'uploaded_images',
            'likes_count', 'comments_count', 'is_liked_by_requester',
            'created_at',
        ]
        read_only_fields = ['team', 'team_name', 'registration', 'competition_id', 'created_at', 'likes_count', 'comments_count', 'is_liked_by_requester']

    @extend_schema_field(OpenApiTypes.INT)
    def get_likes_count(self, obj):
        return obj.likes.count()

    @extend_schema_field(OpenApiTypes.INT)
    def get_comments_count(self, obj):
        return obj.comments.count()

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_liked_by_requester(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            if 'likes' in getattr(obj, '_prefetched_objects_cache', {}):
                return any(like.user_id == request.user.pk for like in obj.likes.all())
            return ContentLike.objects.filter(team_content=obj, user=request.user).exists()
        return False

    def create(self, validated_data):
        uploaded_images_data = validated_data.pop('uploaded_images', [])
        team_content = TeamContent.objects.create(**validated_data)
        for image_data in uploaded_images_data:
            ContentImage.objects.create(team_content=team_content, image=image_data)
        return team_content

    def update(self, instance, validated_data):
        uploaded_images_data = validated_data.pop('uploaded_images', None)

        instance.description = validated_data.get('description', instance.description)
        instance.file_link = validated_data.get('file_link', instance.file_link)
        instance.save()

        if uploaded_images_data is not None:
            instance.images.all().delete()
            for image_data in uploaded_images_data:
                ContentImage.objects.create(team_content=instance, image=image_data)
        return instance


@ts_interface()
class CompetitionTeamRegistrationSerializer(serializers.ModelSerializer):
    competition_details = serializers.SerializerMethodField()
    member_ids = serializers.SerializerMethodField()

    class Meta:
        model = CompetitionTeamRegistration
        fields = ['id', 'competition_details', 'status', 'price', 'member_ids', 'order_item',
                  'reviewed_by', 'reviewed_at', 'admin_remarks', 'activated_at', 'created_at', 'updated_at']
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_member_ids(self, obj):
        return [member.user_id for member in obj.members.all()]

    @extend_schema_field(GroupCompetitionSerializer)
    def get_competition_details(self, obj):
        if hasattr(obj, 'reserved_count'):
            obj.competition.reserved_count = obj.reserved_count
        return GroupCompetitionSerializer(obj.competition, context=self.context).data


@ts_interface()
class CompetitionTeamDetailSerializer(serializers.ModelSerializer):
    leader_details = TeamMembershipUserDetailSerializer(source='leader', read_only=True)
    group_competition_details = serializers.SerializerMethodField()
    memberships = TeamMembershipSerializer(many=True, read_only=True)
    registrations = CompetitionTeamRegistrationSerializer(many=True, read_only=True)
    content_submission = serializers.SerializerMethodField()
    management_status = serializers.CharField(default='forming', read_only=True)
    accepted_member_count = serializers.SerializerMethodField()

    @extend_schema_field(GroupCompetitionSerializer)
    def get_group_competition_details(self, obj):
        for registration in obj.registrations.all():
            if registration.competition_id == obj.group_competition_id:
                return CompetitionTeamRegistrationSerializer(registration, context=self.context).data['competition_details']
        return GroupCompetitionSerializer(obj.group_competition, context=self.context).data if obj.group_competition_id else None

    @extend_schema_field(TeamContentSerializer)
    def get_content_submission(self, obj):
        submissions = list(obj.content_submissions.all())
        content = next((s for s in submissions if s.registration_id and
                        s.registration.competition_id == obj.group_competition_id), None)
        return TeamContentSerializer(content or (submissions[0] if submissions else None), context=self.context).data if submissions else None

    @extend_schema_field(OpenApiTypes.INT)
    def get_accepted_member_count(self, obj):
        return sum(m.status == TeamMembership.STATUS_ACCEPTED for m in obj.memberships.all())

    class Meta:
        model = CompetitionTeam
        fields = [
            'id', 'name', 'leader_details', 'group_competition_details',
            'status', 'is_approved_by_admin', 'admin_remarks',
            'memberships', 'content_submission',
            'created_at', 'registrations', 'management_status', 'accepted_member_count',
        ]
        read_only_fields = fields


@ts_interface()
class TeamCreateSerializer(serializers.Serializer):
    team_name = serializers.CharField(max_length=255)
    member_emails = serializers.ListField(
        child=serializers.EmailField(),
        required=False,
        allow_empty=True,
        default=list,
    )

    def validate_team_name(self, value):
        if CompetitionTeam.objects.filter(name__iexact=value).exists():
            raise serializers.ValidationError("A team with this name already exists.")
        return value

    def validate_member_emails(self, emails):
        request = self.context.get('request')
        leader_email = request.user.email.lower()

        cleaned_emails = [email.lower() for email in emails]

        if leader_email in cleaned_emails:
            raise serializers.ValidationError("Leader cannot be in the member list.")
        if len(cleaned_emails) != len(set(cleaned_emails)):
            raise serializers.ValidationError("Duplicate emails found in the member list.")

        for email in cleaned_emails:
            if not CustomUser.objects.filter(email__iexact=email).exists():
                raise serializers.ValidationError(f"User with email '{email}' not found.")

        return cleaned_emails


@ts_interface()
class InviteActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['accept', 'reject'])


class InviteMemberSerializer(serializers.Serializer):
    email = serializers.EmailField()


class RegistrationReviewSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    remarks = serializers.CharField(required=False, allow_blank=True, default='')


@ts_interface()
class ContentLikeSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = ContentLike
        fields = ['id', 'user', 'user_email', 'team_content', 'created_at']
        read_only_fields = ['user', 'user_email', 'team_content', 'created_at']


@ts_interface()
class LikeStatusSerializer(serializers.Serializer):
    liked = serializers.BooleanField()
    likes_count = serializers.IntegerField()


@ts_interface()
class ContentCommentSerializer(serializers.ModelSerializer):
    user_details = TeamMembershipUserDetailSerializer(source='user', read_only=True)

    class Meta:
        model = ContentComment
        fields = ['id', 'user', 'user_details', 'team_content', 'text', 'created_at', ]
        read_only_fields = ['user', 'user_details', 'team_content', 'created_at', ]


@ts_interface()
class CommentListSerializer(serializers.Serializer):
    parent_content_id = serializers.IntegerField()
    parent_content_likes_count = serializers.IntegerField()
    comments = ContentCommentSerializer(many=True)


@ts_interface()
class CommentCreateSerializer(serializers.Serializer):
    text = serializers.CharField()


@ts_interface()
class CommentUpdateSerializer(serializers.Serializer):
    text = serializers.CharField()


@ts_interface()
class EventDetailSerializer(serializers.ModelSerializer):
    presentations = PresentationSerializer(many=True, read_only=True)
    solo_competitions = SoloCompetitionSerializer(many=True, read_only=True, source='solocompetition_set')
    group_competitions = GroupCompetitionSerializer(many=True, read_only=True, source='groupcompetition_set')

    class Meta:
        model = Event
        fields = [
            'id', 'title', 'description', 'start_date', 'end_date', 'is_active',
            'presentations', 'solo_competitions', 'group_competitions', 'poster',
            'created_at', 'landing_url', 'manager'
        ]
        read_only_fields = ['created_at', ]


@ts_interface()
class EventListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ['id', 'title', 'start_date', 'end_date', 'poster',
                  'is_active', 'landing_url', 'description', 'manager']


@ts_interface()
class PresentationEnrollmentSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    presentation_title = serializers.CharField(source='presentation.title', read_only=True)

    class Meta:
        model = PresentationEnrollment
        fields = ['id', 'user_email', 'presentation_title', 'status', 'enrolled_at', 'order_item']
        read_only_fields = fields


@ts_interface()
class SoloCompetitionRegistrationSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    solo_competition_title = serializers.CharField(source='solo_competition.title', read_only=True)

    class Meta:
        model = SoloCompetitionRegistration
        fields = ['id', 'user_email', 'solo_competition_title', 'status', 'registered_at', 'order_item']
        read_only_fields = fields


@ts_interface()
class PostListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["id", "title", "excerpt", "published_at", ]
        read_only_fields = fields


@ts_interface()
class PostDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ["id", "title", "excerpt", "body_markdown", "published_at", ]
        read_only_fields = fields
