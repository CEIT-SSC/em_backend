from django.urls import path
from .views import (
    CertificateRequestView,
    CertificateDetailView,
    CompletedEnrollmentsView,
)


urlpatterns = [
    path('enrollments/<int:enrollment_pk>/certificate/request/', CertificateRequestView.as_view(), name='cert-request'),
    path('enrollments/<int:enrollment_pk>/certificate/',         CertificateDetailView.as_view(), name='cert-detail'),
    path('enrollments/completed/',                             CompletedEnrollmentsView.as_view(), name='completed-enrollments'),
]
