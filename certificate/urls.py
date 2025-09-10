from django.urls import path
from .views import (
    CompletedEnrollmentsView,
    CertificateRequestView,
    CertificateDetailView,
    CompetitionCertificateListView,
    GroupCompetitionCertificateListView,
    UnifiedCompetitionCertificateRequestView,
    CompetitionCertificateDetailView,
    PublicCertificateVerifyView,
    PublicCompetitionCertificateVerifyView,
)

urlpatterns = [
    path('presentations/eligible/', CompletedEnrollmentsView.as_view(), name='eligible-presentations'),
    path('presentations/<int:enrollment_pk>/request/', CertificateRequestView.as_view(), name='presentation-cert-request'),
    path('presentations/<int:pk>/', CertificateDetailView.as_view(), name='presentation-cert-detail'),

    path('competitions/solo/eligible/', CompetitionCertificateListView.as_view(), name='eligible-solo-competitions'),
    path('competitions/group/eligible/', GroupCompetitionCertificateListView.as_view(), name='eligible-group-competitions'),
    path('competitions/request/', UnifiedCompetitionCertificateRequestView.as_view(), name='competition-cert-request'),
    path('competitions/<int:pk>/', CompetitionCertificateDetailView.as_view(), name='competition-cert-detail'),

    path('verify/presentation/<uuid:verification_id>/', PublicCertificateVerifyView.as_view(), name='public-presentation-cert-verify'),
    path('verify/competition/<uuid:verification_id>/', PublicCompetitionCertificateVerifyView.as_view(), name='public-competition-cert-verify'),
]