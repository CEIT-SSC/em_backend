from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    EventViewSet, PresentationViewSet, SoloCompetitionViewSet, GroupCompetitionViewSet,
    MyTeamsViewSet, TeamContentViewSet, ContentCommentViewSet, PostViewSet, MyInvitationsViewSet
)

router = DefaultRouter()
router.register(r'events', EventViewSet, basename='event')
router.register(r'presentations', PresentationViewSet, basename='presentation')
router.register(r'solo-competitions', SoloCompetitionViewSet, basename='solocompetition')
router.register(r'group-competitions', GroupCompetitionViewSet, basename='groupcompetition')
router.register(r'my-teams', MyTeamsViewSet, basename='my-team')
router.register(r'my-invitations', MyInvitationsViewSet, basename='my-invitation')
router.register(r'team-content', TeamContentViewSet, basename='teamcontent')
router.register(r'my-content-comments', ContentCommentViewSet, basename='mycontentcomment')
router.register(r"posts", PostViewSet, basename="post")

app_name = 'events'

urlpatterns = [
    path('', include(router.urls)),
]