from django.db import transaction, models
from django.apps import apps
from rest_framework import viewsets, status, generics, serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes

from .models import (
    Event, Presentation,
    SoloCompetition, GroupCompetition, CompetitionTeam, TeamMembership,
    PresentationEnrollment, SoloCompetitionRegistration
)
from .serializers import (
    EventListSerializer, EventDetailSerializer, PresentationSerializer,
    SoloCompetitionSerializer, GroupCompetitionSerializer,
    CompetitionTeamSerializer, CompetitionTeamSubmitSerializer,
    PresentationEnrollmentSerializer, SoloCompetitionRegistrationSerializer
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

    CartItem.objects.create(
        cart=cart,
        content_type=content_type,
        object_id=item_instance.pk
    )
    if isinstance(item_instance, CompetitionTeam):
        item_instance.status = CompetitionTeam.STATUS_IN_CART
        item_instance.save()
    return True, "Item added to your cart for payment."


@extend_schema(tags=['Public - Events & Activities'])
class EventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Event.objects.filter(is_active=True).prefetch_related(
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
class PresentationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Presentation.objects.select_related('event').prefetch_related('presenters').filter(
        event__is_active=True).order_by('start_time')
    serializer_class = PresentationSerializer
    filterset_fields = ['event', 'type', 'is_online', 'is_paid']

    @extend_schema(
        summary="Enroll in a presentation (handles free/zero-price or adds paid to cart)",
        request=None,
        responses={
            200: PresentationEnrollmentSerializer,
            201: PresentationEnrollmentSerializer,
            400: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT
        }
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='enroll')
    def enroll(self, request, pk=None):
        presentation = self.get_object()
        user = request.user

        existing_enrollment = PresentationEnrollment.objects.filter(user=user, presentation=presentation,
                                                                    status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).first()
        if existing_enrollment:
            return Response({"message": "You are already actively enrolled in this presentation."},
                            status=status.HTTP_200_OK)

        if presentation.capacity is not None and presentation.enrollments.filter(
                status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).count() >= presentation.capacity:
            return Response({"error": "This presentation is at full capacity."}, status=status.HTTP_400_BAD_REQUEST)

        is_effectively_free = not presentation.is_paid or (presentation.price is not None and presentation.price <= 0)

        if is_effectively_free:
            enrollment, created = PresentationEnrollment.objects.update_or_create(
                user=user,
                presentation=presentation,
                defaults={'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE, 'order_item': None}
            )
            serializer = PresentationEnrollmentSerializer(enrollment)
            return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        else:  # Paid presentation
            success, message = _add_item_to_user_cart(user, presentation, 'presentation')
            if success:
                return Response({"message": message}, status=status.HTTP_200_OK)
            else:
                return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['Public - Events & Activities'])
class SoloCompetitionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SoloCompetition.objects.select_related('event').filter(is_active=True, event__is_active=True).order_by(
        'start_datetime')
    serializer_class = SoloCompetitionSerializer
    filterset_fields = ['event', 'is_paid']

    @extend_schema(
        summary="Register for a solo competition (handles free/zero-price or adds paid to cart)",
        request=None,
        responses={
            200: SoloCompetitionRegistrationSerializer,
            201: SoloCompetitionRegistrationSerializer,
            400: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT
        }
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='register')
    def register(self, request, pk=None):
        competition = self.get_object()
        user = request.user

        existing_registration = SoloCompetitionRegistration.objects.filter(user=user, solo_competition=competition,
                                                                           status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).first()
        if existing_registration:
            return Response({"message": "You are already actively registered for this solo competition."},
                            status=status.HTTP_200_OK)

        if competition.max_participants is not None and competition.registrations.filter(
                status=PresentationEnrollment.STATUS_COMPLETED_OR_FREE).count() >= competition.max_participants:
            return Response({"error": "This competition has reached its maximum participant limit."},
                            status=status.HTTP_400_BAD_REQUEST)

        is_effectively_free = not competition.is_paid or (
                    competition.price_per_participant is not None and competition.price_per_participant <= 0)

        if is_effectively_free:
            registration, created = SoloCompetitionRegistration.objects.update_or_create(
                user=user,
                solo_competition=competition,
                defaults={'status': PresentationEnrollment.STATUS_COMPLETED_OR_FREE, 'order_item': None}
            )
            serializer = SoloCompetitionRegistrationSerializer(registration)
            return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        else:  # Paid solo competition
            success, message = _add_item_to_user_cart(user, competition, 'solocompetition')
            if success:
                return Response({"message": message}, status=status.HTTP_200_OK)
            else:
                return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['Public - Events & Activities'])
class GroupCompetitionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = GroupCompetition.objects.select_related('event').filter(is_active=True, event__is_active=True).order_by(
        'start_datetime')
    serializer_class = GroupCompetitionSerializer
    filterset_fields = ['event', 'is_paid', 'requires_admin_approval']

    @extend_schema(
        summary="Register/Submit a team for a group competition",
        description="Handles free/zero-price, paid (adds to cart), and verifiable (submits for approval) team registrations.",
        request=CompetitionTeamSubmitSerializer,
        responses={
            201: CompetitionTeamSerializer,
            200: CompetitionTeamSerializer,
            400: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT
        }
    )
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated], url_path='register-team')
    def register_team(self, request, pk=None):
        group_competition = self.get_object()
        user = request.user  # Team Leader

        if TeamMembership.objects.filter(
                user=user,
                team__group_competition=group_competition,
                team__status__in=[
                    CompetitionTeam.STATUS_ACTIVE,
                    CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION,
                    CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT,
                    CompetitionTeam.STATUS_IN_CART,
                    CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION
                ]
        ).exists():
            return Response(
                {"error": "You are already involved with a team (or have a submission pending) for this competition."},
                status=status.HTTP_400_BAD_REQUEST)

        serializer = CompetitionTeamSubmitSerializer(data=request.data, context={'request': request,
                                                                                 'group_competition': group_competition})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        team_name = serializer.validated_data['team_name']
        validated_member_users_data = serializer.validated_data['validated_member_users']

        all_involved_user_ids = [user.id] + [m_data['user_instance'].id for m_data in validated_member_users_data]

        conflicting_memberships = TeamMembership.objects.filter(
            team__group_competition=group_competition,
            user_id__in=all_involved_user_ids,
            team__status__in=[
                CompetitionTeam.STATUS_ACTIVE,
                CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION,
                CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT,
                CompetitionTeam.STATUS_IN_CART,
                CompetitionTeam.STATUS_AWAITING_PAYMENT_CONFIRMATION
            ]
        ).values_list('user__email', flat=True)

        if conflicting_memberships:
            return Response({
                                "error": f"One or more proposed members ({', '.join(conflicting_memberships)}) are already in another team or have a pending submission for this competition."},
                            status=status.HTTP_400_BAD_REQUEST)

        is_effectively_free = not group_competition.is_paid or (
                    group_competition.price_per_group is not None and group_competition.price_per_group <= 0)

        try:
            with transaction.atomic():
                team_status = ""
                if group_competition.requires_admin_approval:
                    team_status = CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION
                elif is_effectively_free:
                    team_status = CompetitionTeam.STATUS_ACTIVE
                else:
                    team_status = CompetitionTeam.STATUS_IN_CART

                team = CompetitionTeam.objects.create(
                    name=team_name,
                    leader=user,
                    group_competition=group_competition,
                    status=team_status,  # Set initial status
                    is_approved_by_admin=(not group_competition.requires_admin_approval)
                )

                TeamMembership.objects.create(user=user, team=team)
                for member_data in validated_member_users_data:
                    TeamMembership.objects.create(
                        user=member_data['user_instance'],
                        team=team,
                        government_id_picture=member_data.get('government_id_picture')
                    )

                if group_competition.requires_admin_approval:
                    team_serializer = CompetitionTeamSerializer(team)
                    return Response(
                        {"message": "Team submitted for admin approval.", "team_details": team_serializer.data},
                        status=status.HTTP_201_CREATED)

                if is_effectively_free:
                    team_serializer = CompetitionTeamSerializer(team)
                    return Response({"message": "Team successfully registered (free/zero-price).",
                                     "team_details": team_serializer.data}, status=status.HTTP_201_CREATED)
                else:  # Paid, non-verified
                    success, message = _add_item_to_user_cart(user, team, 'competitionteam')
                    if success:
                        team.refresh_from_db()  # Ensure status is IN_CART
                        team_serializer = CompetitionTeamSerializer(team)
                        return Response({"message": message, "team_details": team_serializer.data},
                                        status=status.HTTP_200_OK)
                    else:
                        raise  # Re-raise to rollback transaction if add to cart fails unexpectedly

        except serializers.ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": f"An unexpected error occurred during team registration: {str(e)}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(tags=['User - My Activities & Teams'])
class MyTeamsViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CompetitionTeamSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user_teams_ids = TeamMembership.objects.filter(user=self.request.user).values_list('team_id', flat=True)
        from django.db.models import Q
        return CompetitionTeam.objects.filter(
            Q(leader=self.request.user) | Q(id__in=user_teams_ids)
        ).distinct().select_related('group_competition', 'leader').prefetch_related('memberships__user').order_by(
            '-created_at')

    @extend_schema(
        summary="Add an admin-approved paid team to cart",
        request=None,
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT,
                   404: OpenApiTypes.OBJECT}
    )
    @action(detail=True, methods=['post'], url_path='add-to-cart', permission_classes=[IsAuthenticated])
    def add_approved_team_to_cart(self, request, pk=None):
        team = self.get_object()
        user = request.user

        if team.leader != user:
            return Response({"error": "You are not the leader of this team."}, status=status.HTTP_403_FORBIDDEN)

        is_team_competition_paid = team.group_competition.is_paid and \
                                   (
                                               team.group_competition.price_per_group is not None and team.group_competition.price_per_group > 0)

        if not (team.group_competition.requires_admin_approval and
                team.is_approved_by_admin and
                is_team_competition_paid and
                team.status == CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT):  # Correct status to check
            return Response({"error": "This team is not eligible to be added to cart for payment at this time."},
                            status=status.HTTP_400_BAD_REQUEST)

        success, message = _add_item_to_user_cart(user, team, 'competitionteam')
        if success:
            team.refresh_from_db()
            return Response({"message": message, "team_id": team.id, "new_team_status": team.get_status_display()},
                            status=status.HTTP_200_OK)
        else:
            return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['User - My Activities & Teams'])
class MyPresentationEnrollmentsView(generics.ListAPIView):
    serializer_class = PresentationEnrollmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PresentationEnrollment.objects.filter(user=self.request.user).select_related('presentation__event',
                                                                                            'user').order_by(
            '-enrolled_at')


@extend_schema(tags=['User - My Activities & Teams'])
class MySoloCompetitionRegistrationsView(generics.ListAPIView):
    serializer_class = SoloCompetitionRegistrationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return SoloCompetitionRegistration.objects.filter(user=self.request.user).select_related(
            'solo_competition__event', 'user').order_by('-registered_at')
