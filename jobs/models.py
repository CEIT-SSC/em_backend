from django.db import models

class Tag(models.Model):
    name   = models.CharField(max_length=64, unique=True)
    color  = models.CharField(max_length=7, help_text='HEX, e.g. #FF5733')

    def __str__(self):
        return self.name


class Job(models.Model):
    title       = models.CharField(max_length=255)
    # excerpt    = models.CharField(max_length=400, blank=True)
    description = models.TextField()

    company         = models.CharField(max_length=255, blank=True, default="")
    company_image   = models.ImageField(upload_to='job_logos/', null=True, blank=True)
    company_url     = models.URLField(null=True, blank=True)
    resume_url      = models.URLField(null=True, blank=True)

    button_link = models.CharField(max_length=1024, blank=True, default="")
    button_text = models.CharField(max_length=255,  blank=True, default="")

    tags       = models.ManyToManyField(Tag, related_name='jobs')
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title