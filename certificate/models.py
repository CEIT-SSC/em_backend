from django.db import models
import uuid
from django.core.validators import MinValueValidator, MaxValueValidator

class Certificate(models.Model):
    enrollment = models.OneToOneField(
        'events.PresentationEnrollment',
        on_delete=models.CASCADE,
        related_name='certificate'
    )
    name_on_certificate = models.CharField(max_length=255)
    file_en = models.FileField(
        upload_to='certificates/%Y/%m/',
        blank=True,
        null=True
    )
    file_fa = models.FileField(
        upload_to='certificates/%Y/%m/',
        blank=True,
        null=True
    )
    is_verified = models.BooleanField(default=False)
    requested_at = models.DateTimeField(auto_now_add=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    grade = models.PositiveIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        blank=True,
        null=True,
        help_text="Grade from 0 to 100 (only for courses)"
    )

    def __str__(self):
        return f"Certificate for {self.enrollment.user.email}"

    class Meta:
        verbose_name = "Certificate"
        verbose_name_plural = "Certificates"
