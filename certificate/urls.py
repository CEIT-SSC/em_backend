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
    GroupCompetitionCertificateDetailView,
)

urlpatterns = [
    # Presentation certificates
    path('<int:enrollment_pk>/request/', CertificateRequestView.as_view(), name='cert-request'),
    path('<int:enrollment_pk>/verify/<str:lang>/', CertificateDetailView.as_view(), name='cert-detail'),
    path('enrollments/completed/', CompletedEnrollmentsView.as_view(), name='completed-enrollments'),

    # Solo competition certificates
    path('competition/<int:id>/request/', CompetitionCertificateRequestView.as_view(), name='competition-cert-request'),
    path('competition/', CompetitionCertificateListView.as_view(), name='competition-cert-list'),
    path('competition/<int:pk>/verify/<str:lang>/', CompetitionCertificateDetailView.as_view(), name='competition-cert-detail'),

    # Group competition certificates
    path('group-competition/<int:registration_id>/request/', GroupCompetitionCertificateRequestView.as_view(), name='group-competition-cert-request'),
    path('group-competition/', GroupCompetitionCertificateListView.as_view(), name='group-competition-cert-list'),
    path('group-competition/<int:registration_id>/verify/<str:lang>/', GroupCompetitionCertificateDetailView.as_view(), name='group-competition-cert-detail'),
]
