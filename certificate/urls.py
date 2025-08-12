from django.urls import path
from .views import (
    CertificateRequestView,
    CertificateDetailView,
    CompletedEnrollmentsView,
)


urlpatterns = [
    path('<int:enrollment_pk>/request/', CertificateRequestView.as_view(), name='cert-request'),
    path('<int:enrollment_pk>/verify/',         CertificateDetailView.as_view(), name='cert-detail'),
    path('enrollments/completed/',                             CompletedEnrollmentsView.as_view(), name='completed-enrollments'),
]
