from django import forms
from .models import Submission

class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ['full_name', 'email', 'phone_number', 'university', 'major', 'article_text', 'image_1', 'image_2', 'image_3', 'pdf_file']