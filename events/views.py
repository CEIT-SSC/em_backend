from django.db import transaction, models
from django.apps import apps
from rest_framework import viewsets, status, generics, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from em_backend.schemas import get_api_response_serializer, ApiErrorResponseSerializer, get_paginated_response_serializer
from .models import (
    Event, Presentation,
    SoloCompetition, GroupCompetition, CompetitionTeam, TeamMembership,
    TeamContent, ContentLike, ContentComment,
    PresentationEnrollment, SoloCompetitionRegistration, Post
)
from .serializers import (
    EventListSerializer, EventDetailSerializer, PresentationSerializer,
    SoloCompetitionSerializer, GroupCompetitionSerializer,
    CompetitionTeamSubmitSerializer, CompetitionTeamDetailSerializer,
    TeamContentSerializer, ContentCommentSerializer,
    PresentationEnrollmentSerializer, SoloCompetitionRegistrationSerializer, LikeStatusSerializer,
    CommentListSerializer, CommentCreateSerializer, CommentUpdateSerializer, PostListSerializer, PostDetailSerializer
)
from django.contrib.auth import get_user_model

CustomUser = get_user_model()


def _add_item_to_user_cart(user, item_instance, item_type_str):
    Cart = apps.get_model('shop', 'Cart')
    CartItem = apps.get_model('shop', 'CartItem')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    cart, _ = Cart.objects.get_or_create(user=user)
    content_type = ContentType.objects.get_for_model(item_instance.__class__)
    if CartItem.objects.filter(cart=cart, content_type=content_type, object_id=item_instance.pk).exists():
        return False, "Item already in cart."
    CartItem.objects.create(cart=cart, content_type=content_type, object_id=item_instance.pk)
    if isinstance(item_instance, CompetitionTeam):
        item_instance.status = CompetitionTeam.STATUS_IN_CART
        item_instance.save()
    return True, "Item added to your cart for payment."


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
                OpenApiParameter(name='type', type=str, location=OpenApiParameter.QUERY, description='Type of presentation'),
                OpenApiParameter(name='is_online', type=bool, location=OpenApiParameter.QUERY, description='Is online?'),
                OpenApiParameter(name='is_paid', type=bool, location=OpenApiParameter.QUERY, description='Is paid?'),
            ]
    ),
    retrieve=extend_schema(responses={200: get_api_response_serializer(PresentationSerializer), 404: ApiErrorResponseSerializer})
)
class PresentationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PresentationSerializer
    filterset_fields = ['event', 'type', 'is_online', 'is_paid']

    def get_queryset(self):
        queryset = Presentation.objects.select_related('event').prefetch_related('presenters')
        event_id = self.request.query_params.get('event')
        if event_id:
            return queryset.filter(event_id=event_id).order_by('start_time')
        else:
            return queryset.order_by('start_time')

    @extend_schema(
        summary="Enroll in a presentation",
        description="Allows an authenticated user to enroll in a presentation or add it to cart if paid.",
        request=None,
        responses={
            200: get_api_response_serializer(None),
            201: get_api_response_serializer(PresentationEnrollmentSerializer),
            400: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='enroll')
    def enroll(self, request, pk=None):
        presentation = self.get_object()
        user = request.user
        if not presentation.is_active:
            return Response({"error": "This presentation is not currently active for enrollment."},
                            status=status.HTTP_400_BAD_REQUEST)

        if PresentationEnrollment.objects.filter(user=user, presentation=presentation,
                                                 status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).exists():
            return Response({"message": "Already actively enrolled."}, status=status.HTTP_200_OK)
        if presentation.capacity is not None and presentation.enrollments.filter(
                status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).count() >= presentation.capacity:
            return Response({"error": "Full capacity."}, status=status.HTTP_400_BAD_REQUEST)
        is_effectively_free = not presentation.is_paid or (presentation.price is not None and presentation.price <= 0)
        if is_effectively_free:
            enrollment, created = PresentationEnrollment.objects.update_or_create(
                user=user, presentation=presentation,
                defaults={'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE, 'order_item': None}
            )
            return Response(PresentationEnrollmentSerializer(enrollment).data,
                            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        else:
            success, message = _add_item_to_user_cart(user, presentation, 'presentation')
            return Response({"message": message}, status=status.HTTP_200_OK) if success else Response(
                {"error": message}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['Public - Events & Activities'])
@extend_schema_view(
    list=extend_schema(
        responses={200: get_paginated_response_serializer(SoloCompetitionSerializer)},
        parameters = [
            OpenApiParameter(name='event', type=str, location=OpenApiParameter.QUERY, description='Event ID'),
            OpenApiParameter(name='is_paid', type=bool, location=OpenApiParameter.QUERY, description='Is paid?'),
        ]
    ),
    retrieve=extend_schema(responses={200: get_api_response_serializer(SoloCompetitionSerializer), 404: ApiErrorResponseSerializer})
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

    @extend_schema(
        summary="Register for a solo competition",
        description="Allows an authenticated user to register for a solo competition or add to cart if paid.",
        request=None,
        responses={
            200: get_api_response_serializer(None),
            201: get_api_response_serializer(SoloCompetitionRegistrationSerializer),
            400: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='register')
    def register(self, request, pk=None):
        competition = self.get_object()
        user = request.user
        if SoloCompetitionRegistration.objects.filter(user=user, solo_competition=competition,
                                                      status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE).exists():
            return Response({"message": "Already actively registered."}, status=status.HTTP_200_OK)
        if competition.max_participants is not None and competition.registrations.filter(
                status=SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE).count() >= competition.max_participants:
            return Response({"error": "Max participant limit reached."}, status=status.HTTP_400_BAD_REQUEST)
        is_effectively_free = not competition.is_paid or (
                    competition.price_per_participant is not None and competition.price_per_participant <= 0)
        if is_effectively_free:
            registration, created = SoloCompetitionRegistration.objects.update_or_create(
                user=user, solo_competition=competition,
                defaults={'status': SoloCompetitionRegistration.STATUS_COMPLETED_OR_FREE, 'order_item': None}
            )
            return Response(SoloCompetitionRegistrationSerializer(registration).data,
                            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        else:
            success, message = _add_item_to_user_cart(user, competition, 'solocompetition')
            return Response({"message": message}, status=status.HTTP_200_OK) if success else Response(
                {"error": message}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['Public - Events & Activities'])
@extend_schema_view(
    list=extend_schema(
        responses={200: get_paginated_response_serializer(GroupCompetitionSerializer)},
        parameters=[
            OpenApiParameter(name='event', type=str, location=OpenApiParameter.QUERY, description='Event ID'),
            OpenApiParameter(name='is_paid', type=bool, location=OpenApiParameter.QUERY, description='Is paid?'),
        ]
    ),
    retrieve=extend_schema(responses={200: get_api_response_serializer(GroupCompetitionSerializer), 404: ApiErrorResponseSerializer})
)
class GroupCompetitionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = GroupCompetitionSerializer
    filterset_fields = ['event', 'is_paid']

    def get_queryset(self):
        event_id = self.request.query_params.get('event')
        queryset = GroupCompetition.objects.select_related('event')

        if event_id:
            return queryset.filter(event_id=event_id).order_by('start_datetime')
        else:
            return queryset.order_by('start_datetime')

    @extend_schema(
        summary="Register/Submit a team",
        description="Submit a new team for a group competition.",
        request=CompetitionTeamSubmitSerializer,
        responses={
            200: get_api_response_serializer(CompetitionTeamDetailSerializer),
            201: get_api_response_serializer(CompetitionTeamDetailSerializer),
            400: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='register-team')
    def register_team(self, request, pk=None):
        group_competition = self.get_object()
        user = request.user

        serializer = CompetitionTeamSubmitSerializer(data=request.data, context={'request': request,
                                                                                 'group_competition': group_competition})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        team_name = serializer.validated_data['team_name']
        validated_member_users_data = serializer.validated_data['validated_member_users_data']

        is_effectively_free = not group_competition.is_paid or (
                    group_competition.price_per_group is not None and group_competition.price_per_group <= 0)

        try:
            with transaction.atomic():
                initial_status = CompetitionTeam.STATUS_IN_CART
                if group_competition.requires_admin_approval:
                    initial_status = CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION
                elif is_effectively_free:
                    initial_status = CompetitionTeam.STATUS_ACTIVE

                team = CompetitionTeam.objects.create(
                    name=team_name, leader=user, group_competition=group_competition,
                    status=initial_status,
                    is_approved_by_admin=(not group_competition.requires_admin_approval)
                )
                TeamMembership.objects.create(user=user, team=team)
                for member_data in validated_member_users_data:
                    TeamMembership.objects.create(
                        user=member_data['user_instance'], team=team,
                        government_id_picture=member_data.get('government_id_picture')
                    )

                if group_competition.requires_admin_approval:
                    return Response({"message": "Team submitted for admin approval.",
                                     "team_details": CompetitionTeamDetailSerializer(team, context={
                                         'request': request}).data}, status=status.HTTP_201_CREATED)

                if is_effectively_free:
                    return Response({"message": "Team successfully registered (free/zero-price).",
                                     "team_details": CompetitionTeamDetailSerializer(team, context={
                                         'request': request}).data}, status=status.HTTP_201_CREATED)
                else:
                    success, message = _add_item_to_user_cart(user, team, 'competitionteam')
                    if success:
                        team.refresh_from_db()
                        return Response({"message": message, "team_details": CompetitionTeamDetailSerializer(team,
                                                                                                             context={
                                                                                                                 'request': request}).data},
                                        status=status.HTTP_200_OK)
                    else:
                        raise Exception(message)

        except Exception as e:
            return Response({"error": f"An error occurred: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

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


@extend_schema(tags=['User - My Activities & Teams'])
@extend_schema_view(
    list=extend_schema(responses={200: get_paginated_response_serializer(CompetitionTeamDetailSerializer)}),
    retrieve=extend_schema(responses={200: get_api_response_serializer(CompetitionTeamDetailSerializer), 404: ApiErrorResponseSerializer}),
    destroy=extend_schema(
        summary="Delete a team (leader only)",
        description="Allows the team leader to delete their team. A 204 response from the view becomes a 200 response from the renderer.",
        responses={
            200: get_api_response_serializer(None),
            400: ApiErrorResponseSerializer,
            403: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        }
    )
)
class MyTeamsViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, mixins.DestroyModelMixin,
                     viewsets.GenericViewSet):
    serializer_class = CompetitionTeamDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user_teams_ids = TeamMembership.objects.filter(user=self.request.user).values_list('team_id', flat=True)
        from django.db.models import Q
        return CompetitionTeam.objects.filter(
            Q(leader=self.request.user) | Q(id__in=user_teams_ids)
        ).distinct().select_related('group_competition', 'leader').prefetch_related('memberships__user',
                                                                                    'content_submission__images',
                                                                                    'content_submission__likes',
                                                                                    'content_submission__comments').order_by(
            '-created_at')

    @extend_schema(
        summary="Add an admin-approved paid team to cart",
        description="Adds a team awaiting payment to the user's cart",
        request=None,
        responses={
            200: get_api_response_serializer(None),
            400: ApiErrorResponseSerializer,
            403: ApiErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=['post'], url_path='add-to-cart')
    def add_approved_team_to_cart(self, request, pk=None):
        team = self.get_object()
        user = request.user
        if team.leader != user: return Response({"error": "Not team leader."}, status=status.HTTP_403_FORBIDDEN)
        is_paid_comp = team.group_competition.is_paid and (
                    team.group_competition.price_per_group is not None and team.group_competition.price_per_group > 0)
        if not (
                team.group_competition.requires_admin_approval and team.is_approved_by_admin and is_paid_comp and team.status == CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT):
            return Response({"error": "Team not eligible to be added to cart."}, status=status.HTTP_400_BAD_REQUEST)
        success, message = _add_item_to_user_cart(user, team, 'competitionteam')
        if success:
            team.refresh_from_db()
            return Response({"message": message, "team_id": team.id, "new_team_status": team.get_status_display()},
                            status=status.HTTP_200_OK)
        else:
            return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Delete a team (leader only)",
        description="Allows the team leader to delete their team under eligible conditions",
        responses={
            204: get_api_response_serializer(None),
            403: ApiErrorResponseSerializer,
            404: ApiErrorResponseSerializer,
        },
    )
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.leader != request.user:
            return Response({"error": "You do not have permission to delete this team."},
                            status=status.HTTP_403_FORBIDDEN)

        if instance.status == CompetitionTeam.STATUS_ACTIVE and instance.group_competition.is_paid:
            return Response({"error": "Cannot delete an active team from a paid competition. Contact support."},
                            status=status.HTTP_400_BAD_REQUEST)

        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

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
    @action(detail=True, methods=['post', 'put'], url_path='submit-content', permission_classes=[IsAuthenticated])
    def submit_update_content(self, request, pk=None):
        team = self.get_object()
        if request.user != team.leader:
            return Response({"error": "Only the team leader can submit/update content."},
                            status=status.HTTP_403_FORBIDDEN)
        if not team.group_competition.allow_content_submission:
            return Response({"error": "Content submission is not allowed for this competition."},
                            status=status.HTTP_400_BAD_REQUEST)
        if team.status != CompetitionTeam.STATUS_ACTIVE:
            return Response({"error": "Team must be active to submit content."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            content_instance = TeamContent.objects.get(team=team)
            serializer = TeamContentSerializer(content_instance, data=request.data, partial=(request.method == 'PATCH'),
                                               context={'request': request})
        except TeamContent.DoesNotExist:
            if request.method == 'PUT':
                return Response({"error": "Content not found for update. Use POST to create."},
                                status=status.HTTP_404_NOT_FOUND)
            serializer = TeamContentSerializer(data=request.data, context={'request': request})

        if serializer.is_valid():
            if not getattr(serializer, 'instance', None):
                serializer.save(team=team)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['User - My Activities & Teams'],
    responses={200: get_paginated_response_serializer(PresentationEnrollmentSerializer)}
)
class MyPresentationEnrollmentsView(generics.ListAPIView):
    serializer_class = PresentationEnrollmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PresentationEnrollment.objects.filter(user=self.request.user).select_related('presentation__event',
                                                                                            'user').order_by(
            '-enrolled_at')


@extend_schema(
    tags=['User - My Activities & Teams'],
    responses={200: get_paginated_response_serializer(SoloCompetitionRegistrationSerializer)}
)
class MySoloCompetitionRegistrationsView(generics.ListAPIView):
    serializer_class = SoloCompetitionRegistrationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return SoloCompetitionRegistration.objects.filter(user=self.request.user).select_related(
            'solo_competition__event', 'user').order_by('-registered_at')


@extend_schema(tags=['Events - Content Interactions'])
@extend_schema_view(
    list=extend_schema(responses={200: get_paginated_response_serializer(TeamContentSerializer)}),
    retrieve=extend_schema(responses={200: get_api_response_serializer(TeamContentSerializer), 404: ApiErrorResponseSerializer})
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
    retrieve=extend_schema(responses={200: get_api_response_serializer(PostDetailSerializer), 404: ApiErrorResponseSerializer})
)
class PostViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Post.objects.filter(is_active=True)
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == "list":
            return PostListSerializer
        return PostDetailSerializer