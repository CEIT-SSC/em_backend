from django.urls import path
from .views import (
    CompletedEnrollmentsView,
    CertificateRequestView,
    CertificateDetailView,
    CompetitionCertificateRequestView,
    CompetitionCertificateListView,
    CompetitionCertificateDetailView,
    GroupCompetitionCertificateRequestView,
    GroupCompetitionCertificateListView,
)

urlpatterns = [
    path('enrollments/completed/', CompletedEnrollmentsView.as_view(), name='completed-enrollments'),
    path('presentation/<int:enrollment_pk>/request/', CertificateRequestView.as_view(),
         name='presentation-cert-request'),
    path('presentation/<int:pk>/', CertificateDetailView.as_view(), name='presentation-cert-detail'),

    path('competition/solo/', CompetitionCertificateListView.as_view(), name='solo-competition-cert-list'),
    path('competition/solo/request/', CompetitionCertificateRequestView.as_view(),
         name='solo-competition-cert-request'),

    path('competition/group/', GroupCompetitionCertificateListView.as_view(), name='group-competition-cert-list'),
    path('competition/group/<int:registration_id>/request/', GroupCompetitionCertificateRequestView.as_view(),
         name='group-competition-cert-request'),
    path('competition/<int:pk>/', CompetitionCertificateDetailView.as_view(), name='competition-cert-detail'),
]