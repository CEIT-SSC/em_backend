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

app_name = 'certificates'

urlpatterns = [
    path('presentations/eligible/', CompletedEnrollmentsView.as_view(), name='eligible-presentations-list'),
    path('presentations/<int:enrollment_pk>/request/', CertificateRequestView.as_view(), name='presentation-certificate-request'),
    path('presentations/<int:pk>/', CertificateDetailView.as_view(), name='presentation-certificate-detail'),

    path('competitions/solo/eligible/', CompetitionCertificateListView.as_view(), name='eligible-solo-competitions-list'),
    path('competitions/group/eligible/', GroupCompetitionCertificateListView.as_view(), name='eligible-group-competitions-list'),
    path('competitions/request/', UnifiedCompetitionCertificateRequestView.as_view(), name='competition-certificate-request'),
    path('competitions/<int:pk>/', CompetitionCertificateDetailView.as_view(), name='competition-certificate-detail'),

    path('verify/presentation/<uuid:verification_id>/', PublicCertificateVerifyView.as_view(), name='public-presentation-certificate-verify'),
    path('verify/competition/<uuid:verification_id>/', PublicCompetitionCertificateVerifyView.as_view(), name='public-competition-certificate-verify'),
]