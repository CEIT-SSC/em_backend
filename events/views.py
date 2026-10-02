from django.db import transaction, IntegrityError, models
from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated, AllowAny
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer, \
    get_paginated_response_serializer
from .models import (
    Event, Presentation,
    SoloCompetition, GroupCompetition, CompetitionTeam, TeamMembership, CompetitionTeamRegistration,
    TeamContent, ContentLike, ContentComment, Post
)
from .serializers import (
    EventListSerializer, EventDetailSerializer, PresentationSerializer,
    SoloCompetitionSerializer, GroupCompetitionSerializer,
    TeamCreateSerializer, CompetitionTeamDetailSerializer, InviteActionSerializer, InviteMemberSerializer,
    CompetitionTeamRegistrationSerializer, RegistrationReviewSerializer,
    TeamContentSerializer, ContentCommentSerializer, LikeStatusSerializer,
    CommentListSerializer, CommentCreateSerializer, CommentUpdateSerializer, PostListSerializer, PostDetailSerializer,
    TeamMembershipSerializer
)
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from . import services
from .queries import teams_for_api

CustomUser = get_user_model()


class PresentationPagination(PageNumberPagination):
    page_size = 50


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
        models.Prefetch('presentations', queryset=Presentation.objects.filter(
            event__is_active=True)),
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
        responses={200: get_paginated_response_serializer(
            PresentationSerializer)},
        parameters=[
            OpenApiParameter(
                name='event', type=str, location=OpenApiParameter.QUERY, description='Event ID'),
            OpenApiParameter(name='type', type=str, location=OpenApiParameter.QUERY,
                             description='Type of presentation'),
            OpenApiParameter(
                name='level', type=str, location=OpenApiParameter.QUERY, description='Level'),
            OpenApiParameter(name='is_online', type=bool,
                             location=OpenApiParameter.QUERY, description='Is online?'),
            OpenApiParameter(name='is_paid', type=bool,
                             location=OpenApiParameter.QUERY, description='Is paid?'),
        ]
    ),
    retrieve=extend_schema(
        responses={200: get_api_response_serializer(PresentationSerializer), 404: ApiErrorResponseSerializer})
)
class PresentationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PresentationSerializer
    pagination_class = PresentationPagination
    filterset_fields = ['event', 'type', 'level', 'is_online', 'is_paid']

    def get_queryset(self):
        queryset = Presentation.objects.select_related(
            'event').prefetch_related('presenters')
        event_id = self.request.query_params.get('event')
        if event_id:
            return queryset.filter(event_id=event_id).order_by('start_time')
        else:
            return queryset.order_by('start_time')


@extend_schema(tags=['Public - Events & Activities'])
@extend_schema_view(
    list=extend_schema(
        responses={200: get_paginated_response_serializer(
            SoloCompetitionSerializer)},
        parameters=[
            OpenApiParameter(
                name='event', type=str, location=OpenApiParameter.QUERY, description='Event ID'),
            OpenApiParameter(name='is_paid', type=bool,
                             location=OpenApiParameter.QUERY, description='Is paid?'),
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
        queryset = SoloCompetition.objects.select_related('event').annotate(
            reserved_count=models.Count('registrations', filter=models.Q(
                registrations__status__in=['pending_payment', 'completed_or_free'])))

        if event_id:
            return queryset.filter(event_id=event_id).order_by('start_datetime')
        else:
            return queryset.order_by('start_datetime')


@extend_schema(tags=['Public - Events & Activities'])
@extend_schema_view(
    list=extend_schema(
        responses={200: get_paginated_response_serializer(
            GroupCompetitionSerializer)},
        parameters=[
            OpenApiParameter(
                name='event', type=str, location=OpenApiParameter.QUERY, description='Event ID'),
            OpenApiParameter(name='is_paid', type=bool,
                             location=OpenApiParameter.QUERY, description='Is paid?'),
        ]
    ),
    retrieve=extend_schema(
        responses={200: get_api_response_serializer(GroupCompetitionSerializer), 404: ApiErrorResponseSerializer})
)
class GroupCompetitionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = GroupCompetitionSerializer
    filterset_fields = ['event', 'is_paid']
    queryset = GroupCompetition.objects.select_related('event').annotate(
        reserved_count=models.Count('registrations', filter=models.Q(
            registrations__status__in=CompetitionTeamRegistration.RESERVED_STATUSES))).order_by('start_datetime')

    @extend_schema(
        summary="List all content submissions for a group competition",
        description="Retrieve all submitted content for an active group competition. This is a non-paginated list.",
        responses={
            200: get_paginated_response_serializer(TeamContentSerializer),
            400: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['get'], permission_classes=[AllowAny], url_path='list-content')
    def list_content_submissions(self, request, pk=None):
        group_competition = self.get_object()
        if not group_competition.allow_content_submission:
            return Response({"error": "Content submission is not allowed for this competition."},
                            status=status.HTTP_400_BAD_REQUEST)

        content_submissions = TeamContent.objects.filter(
            registration__competition=group_competition,
            registration__status=CompetitionTeamRegistration.ACTIVE,
        ).select_related('team__leader', 'registration__competition').prefetch_related('images', 'likes', 'comments')

        serializer = TeamContentSerializer(
            content_submissions, many=True, context={'request': request})
        return Response(serializer.data)


@extend_schema(tags=['User - My Teams'])
@extend_schema_view(
    list=extend_schema(
        summary="List my teams (led or member of)",
        description="Paginated list of teams where the user is the leader or a member.",
        responses={200: get_paginated_response_serializer(
            CompetitionTeamDetailSerializer)},
        tags=["User - My Teams"],
        operation_id="my_teams_list",
    ),
    retrieve=extend_schema(
        summary="Get team details",
        responses={
            200: get_api_response_serializer(CompetitionTeamDetailSerializer),
            404: ApiErrorResponseSerializer,
        },
        operation_id="my_teams_retrieve",
        tags=["User - My Teams"],
    ),
    create=extend_schema(
        summary="Create a new team and invite members",
        request=TeamCreateSerializer,
        responses={
            201: get_api_response_serializer(CompetitionTeamDetailSerializer),
            400: ApiErrorResponseSerializer,
        },
        operation_id="my_teams_create",
        tags=["User - My Teams"],
    ),
    destroy=extend_schema(
        summary="Delete a team (leader only, if 'forming')",
        responses={
            400: ApiErrorResponseSerializer,
            403: ApiErrorResponseSerializer,
        },
        operation_id="my_teams_destroy",
        tags=["User - My Teams"],
    ),
)
class MyTeamsViewSet(mixins.CreateModelMixin,
                     mixins.RetrieveModelMixin,
                     mixins.DestroyModelMixin,
                     mixins.ListModelMixin,
                     viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user_teams_ids = TeamMembership.objects.filter(
            user=self.request.user, status=TeamMembership.STATUS_ACCEPTED).values_list('team_id', flat=True)
        return teams_for_api().filter(
            models.Q(id__in=user_teams_ids) | models.Q(leader=self.request.user)
        ).order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'create':
            return TeamCreateSerializer
        return CompetitionTeamDetailSerializer

    def perform_create(self, serializer):
        team_name = serializer.validated_data['team_name']
        member_emails = serializer.validated_data.get('member_emails', [])
        leader = self.request.user

        with transaction.atomic():
            team = CompetitionTeam.objects.create(
                name=team_name, leader=leader)
            TeamMembership.objects.create(
                user=leader, team=team, status=TeamMembership.STATUS_ACCEPTED, expires_at=None, responded_at=timezone.now())

            for email in member_emails:
                member_user = CustomUser.objects.get(email__iexact=email)
                TeamMembership.objects.create(
                    user=member_user, team=team, status=TeamMembership.STATUS_PENDING, invited_by=leader)
        return team

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            team = self.perform_create(serializer)
        except IntegrityError:
            raise services.CompetitionError('A team with this name already exists.', 'duplicate_team')
        headers = self.get_success_headers(serializer.data)
        return Response(CompetitionTeamDetailSerializer(team, context={'request': request}).data,
                        status=status.HTTP_201_CREATED, headers=headers)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        services.delete_team(instance.pk, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=InviteMemberSerializer, responses={201: TeamMembershipSerializer})
    @action(detail=True, methods=['post'], url_path='add-member')
    def add_member(self, request, pk=None):
        team = self.get_object()
        serializer = InviteMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.invite_member(team.pk, request.user, serializer.validated_data['email'])
        return Response(TeamMembershipSerializer(membership).data, status=201)

    @extend_schema(request=None, responses={200: CompetitionTeamRegistrationSerializer})
    @action(detail=True, methods=['post'], url_path='cancel-registration/(?P<competition_pk>[0-9]+)')
    def cancel_registration(self, request, pk=None, competition_pk=None):
        registration = services.resolve_registration(self.get_object(), competition_pk)
        registration = services.cancel_registration(registration.pk, request.user)
        return Response(CompetitionTeamRegistrationSerializer(registration).data)

    @extend_schema(request=RegistrationReviewSerializer, responses={200: CompetitionTeamRegistrationSerializer})
    @action(detail=True, methods=['post'], url_path='review-registration/(?P<competition_pk>[0-9]+)')
    def review_registration(self, request, pk=None, competition_pk=None):
        # Staff review is also available through the registration admin.
        if not request.user.is_staff:
            raise services.CompetitionError('Administrative review requires staff access.', 'staff_required', 403)
        team = get_object_or_404(CompetitionTeam, pk=pk)
        registration = services.resolve_registration(team, competition_pk)
        serializer = RegistrationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = services.review_registration(registration.pk, request.user,
            approve=serializer.validated_data['approve'], remarks=serializer.validated_data['remarks'])
        return Response(CompetitionTeamRegistrationSerializer(registration).data)

    @extend_schema(
        summary="Register team for a competition (leader only)",
        request=None,
        responses={200: get_api_response_serializer(
            CompetitionTeamDetailSerializer)}
    )
    @action(detail=True, methods=['post'], url_path='register-competition/(?P<competition_pk>[^/.]+)')
    def register_for_competition(self, request, pk=None, competition_pk=None):
        team = self.get_object()
        try:
            competition_id = int(competition_pk)
        except (ValueError, TypeError):
            raise services.CompetitionError('Invalid competition ID.', 'invalid_competition')
        services.register_team(team.pk, competition_id, request.user)
        team.refresh_from_db()
        return Response(CompetitionTeamDetailSerializer(team, context={'request': request}).data)

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

        registration = services.resolve_registration(team, request.query_params.get('competition_id'))
        competition = registration.competition
        if not competition.allow_content_submission:
            return Response({"error": "Content submission is not allowed for this competition."},
                            status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        if not (competition.start_datetime <= now <= competition.end_datetime):
            return Response({"error": "Content can only be submitted within the competition's time frame."},
                            status=status.HTTP_400_BAD_REQUEST)

        if registration.status != CompetitionTeamRegistration.ACTIVE:
            return Response({"error": "Team must be active to submit content."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            content_instance = TeamContent.objects.get(registration=registration)
            serializer = TeamContentSerializer(content_instance, data=request.data, partial=(request.method == 'PUT'),
                                               context={'request': request})
        except TeamContent.DoesNotExist:
            serializer = TeamContentSerializer(
                data=request.data, context={'request': request})

        if serializer.is_valid():
            instance = serializer.save(team=team, registration=registration) if not getattr(
                serializer, 'instance', None) else serializer.save()
            status_code = status.HTTP_201_CREATED if request.method == 'POST' else status.HTTP_200_OK
            return Response(TeamContentSerializer(instance, context={'request': request}).data, status=status_code)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['User - My Invitations'])
@extend_schema_view(
    list=extend_schema(
        summary="List my pending team invitations",
        description="Paginated list of teams where the current user has a pending invitation.",
        responses={200: get_paginated_response_serializer(
            CompetitionTeamDetailSerializer)},
        tags=["User - My Invitations"],
    ),
)
class MyInvitationsViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CompetitionTeamDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return teams_for_api().filter(
            models.Q(memberships__expires_at__gt=timezone.now()) |
            models.Q(memberships__expires_at__isnull=True),
            memberships__user=self.request.user,
            memberships__status=TeamMembership.STATUS_PENDING,
        ).distinct()

    @extend_schema(
        summary="Accept or reject a team invitation",
        request=InviteActionSerializer,
        responses={
            200: get_api_response_serializer(TeamMembershipSerializer),
        }
    )
    @action(detail=True, methods=['post'], url_path='respond')
    def respond_to_invitation(self, request, pk=None):
        serializer = InviteActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.respond_to_invitation(pk, request.user, serializer.validated_data['action'])
        return Response(TeamMembershipSerializer(membership).data)


@extend_schema(tags=['Events - Content Interactions'])
@extend_schema_view(
    list=extend_schema(
        responses={200: get_paginated_response_serializer(TeamContentSerializer)}),
    retrieve=extend_schema(
        responses={200: get_api_response_serializer(TeamContentSerializer), 404: ApiErrorResponseSerializer})
)
class TeamContentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TeamContent.objects.filter(registration__status=CompetitionTeamRegistration.ACTIVE).select_related('team__leader',
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

        like, created = ContentLike.objects.get_or_create(
            user=user, team_content=content)
        if not created:
            like.delete()
            liked = False
        else:
            liked = True

        fresh_likes_count = ContentLike.objects.filter(
            team_content=content).count()
        return Response({"liked": liked, "likes_count": fresh_likes_count}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="List comments for a Team Content Submission",
        responses={
            200: get_paginated_response_serializer(CommentListSerializer),
            404: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['get'], permission_classes=[AllowAny], url_path='comments')
    def list_comments(self, request, pk=None):
        content = self.get_object()
        comments = content.comments.select_related(
            'user').order_by('created_at')
        comment_serializer = ContentCommentSerializer(
            comments, many=True, context={'request': request})

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

        comment = ContentComment.objects.create(
            user=user, team_content=content, text=text)
        serializer = ContentCommentSerializer(
            comment, context={'request': request})
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
    list=extend_schema(
        responses={200: get_paginated_response_serializer(PostListSerializer)}),
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
