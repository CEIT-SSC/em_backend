from django.db import transaction, models
from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer, \
    get_paginated_response_serializer
from .models import (
    Event, Presentation,
    SoloCompetition, GroupCompetition, CompetitionTeam, TeamMembership,
    TeamContent, ContentLike, ContentComment, Post
)
from .serializers import (
    EventListSerializer, EventDetailSerializer, PresentationSerializer,
    SoloCompetitionSerializer, GroupCompetitionSerializer,
    TeamCreateSerializer, CompetitionTeamDetailSerializer, InviteActionSerializer,
    TeamContentSerializer, ContentCommentSerializer, LikeStatusSerializer,
    CommentListSerializer, CommentCreateSerializer, CommentUpdateSerializer, PostListSerializer, PostDetailSerializer,
    TeamMembershipSerializer
)
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404

CustomUser = get_user_model()


@extend_schema(tags=['Public - Events & Activities'])
@extend_schema_view(
    list=extend_schema(
        summary="List all active events",
        responses={200: get_paginated_response_serializer(EventListSerializer)}
    ),
    retrieve=extend_schema(
        summary="Retrieve a single event",
        responses={
            200: get_api_response_serializer(EventDetailSerializer),
            404: ApiErrorResponseSerializer
        }
    )
)
class EventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Event.objects.prefetch_related(
        models.Prefetch('presentations', queryset=Presentation.objects.filter(event__is_active=True)),
        models.Prefetch('solocompetition_set',
                        queryset=SoloCompetition.objects.filter(is_active=True, event__is_active=True)),
        models.Prefetch('groupcompetition_set',
                        queryset=GroupCompetition.objects.filter(is_active=True, event__is_active=True))
    ).order_by('-start_date')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return EventDetailSerializer
        return EventListSerializer


@extend_schema(tags=['Public - Events & Activities'])
@extend_schema_view(
    list=extend_schema(
        responses={200: get_paginated_response_serializer(PresentationSerializer)},
        parameters=[
            OpenApiParameter(name='event', type=str, location=OpenApiParameter.QUERY, description='Event ID'),
            OpenApiParameter(name='type', type=str, location=OpenApiParameter.QUERY,
                             description='Type of presentation'),
            OpenApiParameter(name='level', type=str, location=OpenApiParameter.QUERY, description='Level'),
            OpenApiParameter(name='is_online', type=bool, location=OpenApiParameter.QUERY, description='Is online?'),
            OpenApiParameter(name='is_paid', type=bool, location=OpenApiParameter.QUERY, description='Is paid?'),
        ]
    ),
    retrieve=extend_schema(
        responses={200: get_api_response_serializer(PresentationSerializer), 404: ApiErrorResponseSerializer})
)
class PresentationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PresentationSerializer
    filterset_fields = ['event', 'type', 'level', 'is_online', 'is_paid']

    def get_queryset(self):
        queryset = Presentation.objects.select_related('event').prefetch_related('presenters')
        event_id = self.request.query_params.get('event')
        if event_id:
            return queryset.filter(event_id=event_id).order_by('start_time')
        else:
            return queryset.order_by('start_time')


@extend_schema(tags=['Public - Events & Activities'])
@extend_schema_view(
    list=extend_schema(
        responses={200: get_paginated_response_serializer(SoloCompetitionSerializer)},
        parameters=[
            OpenApiParameter(name='event', type=str, location=OpenApiParameter.QUERY, description='Event ID'),
            OpenApiParameter(name='is_paid', type=bool, location=OpenApiParameter.QUERY, description='Is paid?'),
        ]
    ),
    retrieve=extend_schema(
        responses={200: get_api_response_serializer(SoloCompetitionSerializer), 404: ApiErrorResponseSerializer})
)
class SoloCompetitionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SoloCompetitionSerializer
    filterset_fields = ['event', 'is_paid']

    def get_queryset(self):
        event_id = self.request.query_params.get('event')
        queryset = SoloCompetition.objects.select_related('event')

        if event_id:
            return queryset.filter(event_id=event_id).order_by('start_datetime')
        else:
            return queryset.order_by('start_datetime')


@extend_schema(tags=['Public - Events & Activities'])
@extend_schema_view(
    list=extend_schema(
        responses={200: get_paginated_response_serializer(GroupCompetitionSerializer)},
        parameters=[
            OpenApiParameter(name='event', type=str, location=OpenApiParameter.QUERY, description='Event ID'),
            OpenApiParameter(name='is_paid', type=bool, location=OpenApiParameter.QUERY, description='Is paid?'),
        ]
    ),
    retrieve=extend_schema(
        responses={200: get_api_response_serializer(GroupCompetitionSerializer), 404: ApiErrorResponseSerializer})
)
class GroupCompetitionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = GroupCompetitionSerializer
    filterset_fields = ['event', 'is_paid']
    queryset = GroupCompetition.objects.select_related('event').order_by('start_datetime')

    @extend_schema(
        summary="List all content submissions for a group competition",
        description="Retrieve all submitted content for an active group competition. This is a non-paginated list.",
        responses={
            200: get_api_response_serializer(TeamContentSerializer(many=True)),
            400: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['get'], permission_classes=[AllowAny], url_path='list-content')
    def list_content_submissions(self, request, pk=None):
        group_competition = self.get_object()
        if not group_competition.allow_content_submission:
            return Response({"error": "Content submission is not allowed for this competition."},
                            status=status.HTTP_400_BAD_REQUEST)

        active_teams = CompetitionTeam.objects.filter(group_competition=group_competition,
                                                      status=CompetitionTeam.STATUS_ACTIVE)
        content_submissions = TeamContent.objects.filter(team__in=active_teams).select_related(
            'team__leader').prefetch_related('images', 'likes', 'comments')

        serializer = TeamContentSerializer(content_submissions, many=True, context={'request': request})
        return Response(serializer.data)


@extend_schema(tags=['User - My Teams'])
@extend_schema_view(
    list=extend_schema(summary="List my teams (led or member of)"),
    retrieve=extend_schema(summary="Get team details"),
    destroy=extend_schema(summary="Delete a team (leader only, if 'forming')"),
    create=extend_schema(summary="Create a new team and invite members"),
)
class MyTeamsViewSet(mixins.CreateModelMixin,
                     mixins.RetrieveModelMixin,
                     mixins.DestroyModelMixin,
                     mixins.ListModelMixin,
                     viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user_teams_ids = TeamMembership.objects.filter(user=self.request.user).values_list('team_id', flat=True)
        return CompetitionTeam.objects.filter(
            models.Q(id__in=user_teams_ids)
        ).distinct().select_related('group_competition', 'leader').prefetch_related('memberships__user').order_by(
            '-created_at')

    def get_serializer_class(self):
        if self.action == 'create':
            return TeamCreateSerializer
        return CompetitionTeamDetailSerializer

    def perform_create(self, serializer):
        team_name = serializer.validated_data['team_name']
        member_emails = serializer.validated_data['member_emails']
        leader = self.request.user

        with transaction.atomic():
            team = CompetitionTeam.objects.create(name=team_name, leader=leader)
            TeamMembership.objects.create(user=leader, team=team, status=TeamMembership.STATUS_ACCEPTED)

            for email in member_emails:
                member_user = CustomUser.objects.get(email__iexact=email)
                TeamMembership.objects.create(user=member_user, team=team, status=TeamMembership.STATUS_PENDING)
        return team

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team = self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(CompetitionTeamDetailSerializer(team, context={'request': request}).data,
                        status=status.HTTP_201_CREATED, headers=headers)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.leader != request.user:
            return Response({"error": "Only the team leader can delete the team."}, status=status.HTTP_403_FORBIDDEN)
        if instance.status != CompetitionTeam.STATUS_FORMING:
            return Response({"error": "Only teams in the 'forming' state can be deleted."},
                            status=status.HTTP_400_BAD_REQUEST)

        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Register team for a competition (leader only)",
        request=None,
        responses={200: get_api_response_serializer(CompetitionTeamDetailSerializer)}
    )
    @action(detail=True, methods=['post'], url_path='register-competition/(?P<competition_pk>[^/.]+)')
    def register_for_competition(self, request, pk=None, competition_pk=None):
        team = self.get_object()
        if team.leader != request.user:
            return Response({"error": "Only the team leader can register the team."}, status=status.HTTP_403_FORBIDDEN)
        if team.status != CompetitionTeam.STATUS_FORMING:
            return Response({"error": "Team must be in 'forming' state to register."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not team.is_ready_for_competition():
            return Response({"error": "Not all members have accepted their invitations."},
                            status=status.HTTP_400_BAD_REQUEST)

        competition = get_object_or_404(GroupCompetition, pk=competition_pk)

        team_size = team.memberships.filter(status=TeamMembership.STATUS_ACCEPTED).count()
        if not (competition.min_group_size <= team_size <= competition.max_group_size):
            return Response({
                                "error": f"Team size ({team_size}) is not within the competition's limits ({competition.min_group_size}-{competition.max_group_size})."},
                            status=status.HTTP_400_BAD_REQUEST)

        team.group_competition = competition
        if competition.requires_admin_approval:
            team.status = CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION
        else:
            team.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
        team.save()

        return Response(CompetitionTeamDetailSerializer(team, context={'request': request}).data,
                        status=status.HTTP_200_OK)

    @extend_schema(
        summary="Submit/Update Team Content",
        description="Allows the team leader to submit or update their team's competition content",
        request=TeamContentSerializer,
        responses={
            200: get_api_response_serializer(TeamContentSerializer),
            201: get_api_response_serializer(TeamContentSerializer),
            400: ApiErrorResponseSerializer,
            403: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['post', 'put'], url_path='submit-content')
    def submit_update_content(self, request, pk=None):
        team = self.get_object()
        if request.user != team.leader:
            return Response({"error": "Only the team leader can submit/update content."},
                            status=status.HTTP_403_FORBIDDEN)
        if not team.group_competition or not team.group_competition.allow_content_submission:
            return Response({"error": "Content submission is not allowed for this competition."},
                            status=status.HTTP_400_BAD_REQUEST)
        if team.status != CompetitionTeam.STATUS_ACTIVE:
            return Response({"error": "Team must be active to submit content."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            content_instance = TeamContent.objects.get(team=team)
            serializer = TeamContentSerializer(content_instance, data=request.data, partial=(request.method == 'PUT'),
                                               context={'request': request})
        except TeamContent.DoesNotExist:
            serializer = TeamContentSerializer(data=request.data, context={'request': request})

        if serializer.is_valid():
            instance = serializer.save(team=team) if not getattr(serializer, 'instance', None) else serializer.save()
            status_code = status.HTTP_201_CREATED if not getattr(serializer, 'instance', None) else status.HTTP_200_OK
            return Response(TeamContentSerializer(instance, context={'request': request}).data, status=status_code)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['User - My Invitations'])
class MyInvitationsViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CompetitionTeamDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return CompetitionTeam.objects.filter(
            memberships__user=self.request.user,
            memberships__status=TeamMembership.STATUS_PENDING
        ).distinct()

    @extend_schema(
        summary="Accept or reject a team invitation",
        request=InviteActionSerializer,
        responses={200: get_api_response_serializer(TeamMembershipSerializer)}
    )
    @action(detail=True, methods=['post'], url_path='respond')
    def respond_to_invitation(self, request, pk=None):
        team = self.get_object()
        membership = get_object_or_404(TeamMembership, team=team, user=request.user)

        if membership.status != TeamMembership.STATUS_PENDING:
            return Response({"error": "This invitation has already been responded to."},
                            status=status.HTTP_400_BAD_REQUEST)

        serializer = InviteActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data['action']

        if action == 'accept':
            membership.status = TeamMembership.STATUS_ACCEPTED
            membership.save()
        elif action == 'reject':
            membership.delete()
            return Response({"message": "Invitation rejected."}, status=status.HTTP_200_OK)

        return Response(TeamMembershipSerializer(membership).data, status=status.HTTP_200_OK)


@extend_schema(tags=['Events - Content Interactions'])
@extend_schema_view(
    list=extend_schema(responses={200: get_paginated_response_serializer(TeamContentSerializer)}),
    retrieve=extend_schema(
        responses={200: get_api_response_serializer(TeamContentSerializer), 404: ApiErrorResponseSerializer})
)
class TeamContentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TeamContent.objects.filter(team__status=CompetitionTeam.STATUS_ACTIVE).select_related('team__leader',
                                                                                                     'team__group_competition').prefetch_related(
        'images', 'likes', 'comments')
    serializer_class = TeamContentSerializer
    permission_classes = [AllowAny]

    def get_serializer_context(self):
        return {'request': self.request, 'view': self}

    @extend_schema(
        summary="Like or Unlike a Team Content Submission",
        request=None,
        responses={
            200: get_api_response_serializer(LikeStatusSerializer),
            403: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='toggle-like')
    def toggle_like(self, request, pk=None):
        content = self.get_object()
        user = request.user

        like, created = ContentLike.objects.get_or_create(user=user, team_content=content)
        if not created:
            like.delete()
            liked = False
        else:
            liked = True
        return Response({"liked": liked, "likes_count": content.likes.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="List comments for a Team Content Submission",
        responses={
            200: get_api_response_serializer(CommentListSerializer),
            404: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['get'], permission_classes=[AllowAny], url_path='comments')
    def list_comments(self, request, pk=None):
        content = self.get_object()
        comments = content.comments.select_related('user').order_by('created_at')
        comment_serializer = ContentCommentSerializer(comments, many=True, context={'request': request})

        response_data = {
            "parent_content_id": content.id,
            "parent_content_likes_count": content.likes.count(),
            "comments": comment_serializer.data
        }
        return Response(response_data)

    @extend_schema(
        summary="Post a comment on a Team Content Submission",
        request=CommentCreateSerializer,
        responses={
            201: get_api_response_serializer(ContentCommentSerializer),
            400: ApiErrorResponseSerializer,
            403: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='add-comment')
    def add_comment(self, request, pk=None):
        content = self.get_object()
        user = request.user
        text = request.data.get('text')
        if not text or not str(text).strip():
            return Response({"text": ["This field may not be blank."]}, status=status.HTTP_400_BAD_REQUEST)

        comment = ContentComment.objects.create(user=user, team_content=content, text=text)
        serializer = ContentCommentSerializer(comment, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Events - Content Interactions'])
@extend_schema_view(
    partial_update=extend_schema(
        summary="Update user's own comment",
        request=CommentUpdateSerializer,
        responses={
            200: get_api_response_serializer(ContentCommentSerializer),
            400: ApiErrorResponseSerializer,
            403: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer
        }
    ),
    destroy=extend_schema(
        summary="Delete user's own comment",
        description="A 204 from the view becomes a 200 from the renderer.",
        responses={
            200: get_api_response_serializer(None),
            403: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer
        }
    )
)
class ContentCommentViewSet(mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = ContentComment.objects.all()
    serializer_class = ContentCommentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ContentComment.objects.filter(user=self.request.user)

    def partial_update(self, request, *args, **kwargs):
        text = request.data.get('text')
        if 'text' not in request.data or not str(text).strip():
            return Response({"text": ["This field may not be blank."]}, status=status.HTTP_400_BAD_REQUEST)
        if len(request.data) > 1:
            return Response({"error": "Only the 'text' field can be updated."}, status=status.HTTP_400_BAD_REQUEST)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)


@extend_schema(tags=["Public - News"])
@extend_schema_view(
    list=extend_schema(responses={200: get_paginated_response_serializer(PostListSerializer)}),
    retrieve=extend_schema(
        responses={200: get_api_response_serializer(PostDetailSerializer), 404: ApiErrorResponseSerializer})
)
class PostViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Post.objects.filter(is_active=True)
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == "list":
            return PostListSerializer
        return PostDetailSerializer