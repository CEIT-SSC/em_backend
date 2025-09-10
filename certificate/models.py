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



class CompetitionCertificate(models.Model):
    REGISTRATION_TYPES = [
        ("solo", "Solo Competition"),
        ("group", "Group Competition Team"),
    ]

    registration_type = models.CharField(
        max_length=10, choices=REGISTRATION_TYPES, verbose_name="Registration Type"
    )

    # either solo registration or a team
    solo_registration = models.OneToOneField(
        'events.SoloCompetitionRegistration',
        on_delete=models.CASCADE,
        related_name="certificate",
        blank=True,
        null=True,
    )
    team = models.OneToOneField(
        'events.CompetitionTeam',
        on_delete=models.CASCADE,
        related_name="certificate",
        blank=True,
        null=True,
    )

    name_on_certificate = models.CharField(max_length=255, verbose_name="Name on Certificate")

    # admin-entered ranking
    ranking = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text="Final position in competition (1 = first place, 2 = second, etc.)"
    )

    file_en = models.FileField(
        upload_to="certificates/competitions/%Y/%m/",
        blank=True,
        null=True,
        verbose_name="English Certificate File"
    )
    file_fa = models.FileField(
        upload_to="certificates/competitions/%Y/%m/",
        blank=True,
        null=True,
        verbose_name="Persian Certificate File"
    )

    is_verified = models.BooleanField(default=False, verbose_name="Verified by Admin?")
    requested_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.registration_type == "solo" and self.solo_registration:
            return f"Certificate for {self.solo_registration.user.email} - {self.solo_registration.solo_competition.title}"
        elif self.registration_type == "group" and self.team:
            return f"Certificate for Team {self.team.name} - {self.team.group_competition.title}"
        return f"Competition Certificate {self.id}"

    class Meta:
        verbose_name = "Competition Certificate"
        verbose_name_plural = "Competition Certificates"