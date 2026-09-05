from django import forms
from django.core.exceptions import ValidationError
from .models import Submission

class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ['full_name', 'email', 'phone_number', 'university', 'major', 'article_text', 'docx_file', 'pdf_file', 'image_1', 'image_2', 'image_3']
        
    def clean_pdf_file(self):
        pdf = self.cleaned_data.get('pdf_file')
        if pdf:
            if pdf.size > 10 * 1024 * 1024:  # 10MB limit
                raise ValidationError("حجم فایل PDF نباید بیشتر از ۱۰ مگابایت باشد.")
        return pdf

    def clean_docx_file(self):
        docx = self.cleaned_data.get('docx_file')
        if docx:
            if docx.size > 10 * 1024 * 1024:  # 10MB limit
                raise ValidationError("حجم فایل DOCX نباید بیشتر از ۱۰ مگابایت باشد.")
        return docx