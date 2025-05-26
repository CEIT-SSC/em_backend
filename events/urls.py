from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    EventViewSet, PresentationViewSet, SoloCompetitionViewSet, GroupCompetitionViewSet,
    MyTeamsViewSet, MyPresentationEnrollmentsView, MySoloCompetitionRegistrationsView,
    TeamContentViewSet, ContentCommentViewSet
)

router = DefaultRouter()
router.register(r'events', EventViewSet, basename='event')
router.register(r'presentations', PresentationViewSet, basename='presentation')
router.register(r'solo-competitions', SoloCompetitionViewSet, basename='solocompetition')
router.register(r'group-competitions', GroupCompetitionViewSet, basename='groupcompetition')
router.register(r'my-teams', MyTeamsViewSet, basename='my-team')

router.register(r'team-content', TeamContentViewSet, basename='teamcontent')
router.register(r'my-content-comments', ContentCommentViewSet, basename='mycontentcomment')

app_name = 'events'

urlpatterns = [
    path('', include(router.urls)),
    path('my-enrollments/presentations/', MyPresentationEnrollmentsView.as_view(), name='my-presentation-enrollments'),
    path('my-registrations/solo-competitions/', MySoloCompetitionRegistrationsView.as_view(),
         name='my-solo-registrations'),
]
