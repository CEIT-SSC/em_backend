from django.db import models


class Certificate(models.Model):
    enrollment = models.OneToOneField(
        'events.PresentationEnrollment',
        on_delete=models.CASCADE,
        related_name='certificate'
    )
    name_on_certificate = models.CharField(max_length=255)
    file = models.FileField(
        upload_to='certificates/%Y/%m/',
        blank=True,
        null=True
    )
    is_verified = models.BooleanField(default=False)
    requested_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Certificate for {self.enrollment.user.email}"

    class Meta:
        verbose_name = "Certificate"
        verbose_name_plural = "Certificates"
