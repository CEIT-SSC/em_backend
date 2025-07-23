from django.db import models
from django.conf import settings
from events.models import PresentationEnrollment

class CertificateRequest(models.Model):
    enrollment = models.ForeignKey(PresentationEnrollment, on_delete=models.CASCADE, related_name='certificate_requests')
    requested_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(null=True, blank=True)  # None = pending, True = approved, False = rejected

    class Meta:
        unique_together = ('enrollment',)

    def __str__(self):
        return f"Certificate for {self.enrollment.user.username} - {self.enrollment.presentation.title}"
