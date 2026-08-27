from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone

class CallForPaper(models.Model):
    title = models.CharField(max_length=255, verbose_name="Call Title")
    description = models.TextField(verbose_name="Description")
    start_date = models.DateTimeField(verbose_name="Start Date")
    end_date = models.DateTimeField(verbose_name="End Date")
    is_active = models.BooleanField(default=True, verbose_name="Active")

    class Meta:
        verbose_name = "Call for Paper"
        verbose_name_plural = "Calls for Papers"

    def __str__(self):
        return self.title

    def is_open(self):
        now = timezone.now()
        return self.is_active and self.start_date <= now <= self.end_date

class Submission(models.Model):
    call = models.ForeignKey(CallForPaper, on_delete=models.CASCADE, related_name='submissions', verbose_name="Related Call")
    full_name = models.CharField(max_length=255, verbose_name="Full Name")
    email = models.EmailField(verbose_name="Email")
    phone_number = models.CharField(max_length=20, verbose_name="Phone Number")
    university = models.CharField(max_length=255, verbose_name="University")
    major = models.CharField(max_length=255, verbose_name="Field of Study")
    
    article_text = models.TextField(verbose_name="Article Text")
    image_1 = models.ImageField(upload_to='journal/submissions/images/', null=True, blank=True, verbose_name="Image 1")
    image_2 = models.ImageField(upload_to='journal/submissions/images/', null=True, blank=True, verbose_name="Image 2")
    image_3 = models.ImageField(upload_to='journal/submissions/images/', null=True, blank=True, verbose_name="Image 3")
    pdf_file = models.FileField(upload_to='journal/submissions/pdfs/', verbose_name="Article PDF File")
    
    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name="Submitted At")

    class Meta:
        verbose_name = "Submission"
        verbose_name_plural = "Submissions"

    def __str__(self):
        return f"{self.full_name} - {self.call.title}"

class Release(models.Model):
    title = models.CharField(max_length=255, verbose_name="Journal Title")
    volume = models.CharField(max_length=50, verbose_name="Volume / Issue")
    abstract = models.TextField(verbose_name="Abstract / Description")
    publish_date = models.DateField(verbose_name="Publish Date")
    cover_image = models.ImageField(upload_to='journal/releases/covers/', verbose_name="Cover Image")
    pdf_file = models.FileField(upload_to='journal/releases/pdfs/', verbose_name="Journal PDF File")

    class Meta:
        verbose_name = "Release"
        verbose_name_plural = "Releases"
        ordering = ['-publish_date']

    def __str__(self):
        return f"{self.title} - Volume {self.volume}"

class AboutUsConfig(models.Model):
    title = models.CharField(max_length=100, default="About Us Settings (Create only one record)")
    data = models.JSONField(
        verbose_name="Member Data (JSON)",
        help_text='Example: {"description": "Journal description", "members": [{"name": "Ali", "role": "Editor", "image": "/media/ali.png"}]}'
    )

    class Meta:
        verbose_name = "About Us Setting"
        verbose_name_plural = "About Us Settings"

    def __str__(self):
        return "Journal About Us Settings"