import openpyxl
from django.contrib import admin
from django.http import HttpResponse
from .models import CallForPaper, Submission, Release, AboutUsConfig

@admin.register(CallForPaper)
class CallForPaperAdmin(admin.ModelAdmin):
    list_display = ('title', 'start_date', 'end_date', 'is_active', 'status_is_open')
    list_filter = ('is_active', 'start_date', 'end_date')
    search_fields = ('title', 'description')
    
    @admin.display(boolean=True, description='Currently Open')
    def status_is_open(self, obj):
        return obj.is_open

@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'call', 'university', 'major', 'submitted_at')
    list_filter = ('call', 'university', 'major')
    search_fields = ('full_name', 'email', 'phone_number')
    readonly_fields = ('submitted_at',)
    actions = ['export_to_excel']
    
    fieldsets = (
        ('Personal Info', {
            'fields': ('full_name', 'email', 'phone_number')
        }),
        ('Academic Info', {
            'fields': ('university', 'major')
        }),
        ('Article Details', {
            'fields': ('call', 'article_text', 'docx_file', 'pdf_file')
        }),
        ('Attachments (Images)', {
            'fields': ('image_1', 'image_2', 'image_3'),
            'classes': ('collapse',) 
        }),
        ('Meta Data', {
            'fields': ('submitted_at',),
            'classes': ('collapse',)
        }),
    )

    @admin.action(description="Export selected submissions to Excel (with links)")
    def export_to_excel(self, request, queryset):
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="submissions.xlsx"'
        
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        
        if worksheet is None:
            worksheet = workbook.create_sheet()
            
        worksheet.title = 'Submissions'
        
        # Define Columns
        columns = [
            'ID', 'Call Title', 'Full Name', 'Email', 'Phone Number', 
            'University', 'Major', 'Submitted At', 'Docx Link (Main)', 
            'PDF Link (Ref)', 'Image 1'
        ]
        worksheet.append(columns)
        
        for sub in queryset:
            # Build absolute URIs for files if they exist
            docx_url = request.build_absolute_uri(sub.docx_file.url) if sub.docx_file else ''
            pdf_url = request.build_absolute_uri(sub.pdf_file.url) if sub.pdf_file else ''
            img1_url = request.build_absolute_uri(sub.image_1.url) if sub.image_1 else ''
            submitted_date = sub.submitted_at.strftime('%Y-%m-%d %H:%M') if sub.submitted_at else ''
            
            worksheet.append([
                sub.id,
                sub.call.title,
                sub.full_name,
                sub.email,
                sub.phone_number,
                sub.university,
                sub.major,
                submitted_date,
                docx_url,
                pdf_url,
                img1_url
            ])
            
        workbook.save(response)
        return response

@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_display = ('title', 'volume', 'publish_date')
    list_filter = ('publish_date',)
    search_fields = ('title', 'volume', 'abstract')

@admin.register(AboutUsConfig)
class AboutUsConfigAdmin(admin.ModelAdmin):
    list_display = ('title',)
    
    # SINGLETON PATTERN: Ensure only ONE "About Us" record can ever exist.
    def has_add_permission(self, request):
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False