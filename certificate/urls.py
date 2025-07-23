from django.urls import path
from .views import (
    CertificateRequestCreateView,
    CertificateRequestListView,
    CertificateRequestApproveView,
    CertificateDownloadView,
)

urlpatterns = [
    path('request/', CertificateRequestCreateView.as_view(), name='certificate-request'),
    path('requests/', CertificateRequestListView.as_view(), name='certificate-list'),
    path('requests/<int:pk>/approve/', CertificateRequestApproveView.as_view(), name='certificate-approve'),
    path('requests/<int:pk>/download/', CertificateDownloadView.as_view(), name='certificate-download'),
]
