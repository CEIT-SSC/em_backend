import os
import csv
from django import forms
from django.contrib import admin
from django.http import HttpResponse
from django.utils.html import format_html
from .models import UploadedFile


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

# Define a custom field to handle the list of files during form validation
class MultipleFileField(forms.FileField):
    def to_python(self, data):
        # The widget returns a list. We pass the first file to Django's 
        # standard validation so the form doesn't crash. 
        if isinstance(data, list) and len(data) > 0:
            return super().to_python(data[0])
        return super().to_python(data)

class MultipleFileInputForm(forms.ModelForm):
    file = MultipleFileField(widget=MultipleFileInput(), required=True)

    class Meta:
        model = UploadedFile
        fields = ['file']


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    form = MultipleFileInputForm
    list_display = ('id', 'get_file_name', 'copyable_link', 'uploaded_at')
    readonly_fields = ('uploaded_at',)
    actions = ['export_links_csv', ]

    @admin.display(description="File Name")
    def get_file_name(self, obj):
        return os.path.basename(obj.file.name)

    @admin.display(description="Link")
    def copyable_link(self, obj):
        if obj.file:
            url = obj.file.url
            return format_html('<a href="{}" target="_blank">{}</a>', url, url)
        return "-"

    # Intercept the save process to handle the list of files
    def save_model(self, request, obj, form, change):
        if not change: 
            files = request.FILES.getlist('file')
            if files:
                obj.file = files[0]
                super().save_model(request, obj, form, change)
                
                for f in files[1:]:
                    UploadedFile.objects.create(file=f)
            else:
                super().save_model(request, obj, form, change)
        else:
            super().save_model(request, obj, form, change)

    # Export to CSV Action
    @admin.action(description="Export selected file links to CSV")
    def export_links_csv(self, request, queryset):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="file_links.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['ID', 'File Name', 'Absolute URL'])
        
        for obj in queryset:
            url = request.build_absolute_uri(obj.file.url) if obj.file else ""
            writer.writerow([obj.id, self.get_file_name(obj), url])
            
        return response