from django.urls import path
from .views import (
    CompletedEnrollmentsView,
    CompetitionCertificateRequestView,
    CompetitionCertificateListView,
    CertificateRequestView,
    CertificateDetailView
)


urlpatterns = [
    path('<int:enrollment_pk>/request/', CertificateRequestView.as_view(), name='cert-request'),
    path('<int:enrollment_pk>/verify/',         CertificateDetailView.as_view(), name='cert-detail'),
    path('enrollments/completed/',                             CompletedEnrollmentsView.as_view(), name='completed-enrollments'),
    path('competition/<int:id>/request/',
         CompetitionCertificateRequestView.as_view(),
         name='competition-cert-request'),
    path('competition/', CompetitionCertificateListView.as_view(), name='competition-cert-list'),
]
