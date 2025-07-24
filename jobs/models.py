from django.db import models

class Tag(models.Model):
    name   = models.CharField(max_length=64, unique=True)
    color  = models.CharField(max_length=7, help_text='HEX, e.g. #FF5733')

    def __str__(self):
        return self.name


class Job(models.Model):
    title        = models.CharField(max_length=255)
    excerpt     = models.CharField(max_length=400, blank=True)
    description  = models.TextField()

    company_image   = models.ImageField(upload_to='job_logos/', null=True, blank=True)
    company_url     = models.URLField(null=True, blank=True)
    resume_url      = models.URLField(null=True, blank=True)

    tags         = models.ManyToManyField(Tag, related_name='jobs')

    is_active    = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title