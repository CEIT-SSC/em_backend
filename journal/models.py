import os
import uuid
from django.db import models
from django.utils import timezone
from django.core.validators import FileExtensionValidator, RegexValidator

def get_file_path(instance, filename, folder):
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join(f'journal/{folder}/', filename)

def submission_docx_path(instance, filename): return get_file_path(instance, filename, 'submissions/docx')
def submission_pdf_path(instance, filename): return get_file_path(instance, filename, 'submissions/pdfs')
def submission_image_path(instance, filename): return get_file_path(instance, filename, 'submissions/images')
def release_cover_path(instance, filename): return get_file_path(instance, filename, 'releases/covers')
def release_pdf_path(instance, filename): return get_file_path(instance, filename, 'releases/pdfs')

class CallForPaper(models.Model):
    title = models.CharField(max_length=255, verbose_name="Call Title")
    description = models.TextField(verbose_name="Description")
    rules = models.TextField(verbose_name="Submission Rules", blank=True, help_text="Enter each rule on a new line.")
    start_date = models.DateTimeField(verbose_name="Start Date")
    end_date = models.DateTimeField(verbose_name="End Date")
    is_active = models.BooleanField(default=True, verbose_name="Active")

    class Meta:
        verbose_name = "Call for Paper"
        verbose_name_plural = "Calls for Papers"

    def __str__(self):
        return self.title

    @property
    def is_open(self):
        now = timezone.now()
        return self.is_active and self.start_date <= now <= self.end_date


class Submission(models.Model):
    phone_regex = RegexValidator(regex=r'^09\d{9}$', message="Phone number must be in format: '09123456789'.")
    
    call = models.ForeignKey(CallForPaper, on_delete=models.CASCADE, related_name='submissions')
    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone_number = models.CharField(validators=[phone_regex], max_length=11)
    university = models.CharField(max_length=255)
    major = models.CharField(max_length=255)
    article_text = models.TextField(verbose_name="Abstract (HTML)")
    
    docx_file = models.FileField(upload_to=submission_docx_path, validators=[FileExtensionValidator(['doc', 'docx'])], verbose_name="Main Article (DOCX)")
    pdf_file = models.FileField(upload_to=submission_pdf_path, validators=[FileExtensionValidator(['pdf'])], null=True, blank=True, verbose_name="Referenced Paper (PDF)")
    
    image_1 = models.ImageField(upload_to=submission_image_path, null=True, blank=True, validators=[FileExtensionValidator(['jpg', 'jpeg', 'png'])])
    image_2 = models.ImageField(upload_to=submission_image_path, null=True, blank=True, validators=[FileExtensionValidator(['jpg', 'jpeg', 'png'])])
    image_3 = models.ImageField(upload_to=submission_image_path, null=True, blank=True, validators=[FileExtensionValidator(['jpg', 'jpeg', 'png'])])
    
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Submission"
        verbose_name_plural = "Submissions"

    def __str__(self):
        return f"{self.full_name} - {self.call.title}"


class Release(models.Model):
    title = models.CharField(max_length=255)
    volume = models.CharField(max_length=50)
    abstract = models.TextField()
    publish_date = models.DateField()
    cover_image = models.ImageField(upload_to=release_cover_path, validators=[FileExtensionValidator(['jpg', 'jpeg', 'png'])])
    pdf_file = models.FileField(upload_to=release_pdf_path, validators=[FileExtensionValidator(['pdf'])])

    class Meta:
        ordering = ['-publish_date']

    def __str__(self):
        return f"{self.title} - Vol {self.volume}"


class AboutUsConfig(models.Model):
    title = models.CharField(max_length=100, default="About Us Settings")
    data = models.JSONField(help_text='{"description": "...", "members": [{"name": "Ali", "role": "Editor", "image": ""}]}')

    def __str__(self):
        return self.title