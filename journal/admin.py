from django.contrib import admin
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
    
    fieldsets = (
        ('Personal Info', {
            'fields': ('full_name', 'email', 'phone_number')
        }),
        ('Academic Info', {
            'fields': ('university', 'major')
        }),
        ('Article Details', {
            'fields': ('call', 'article_text', 'pdf_file')
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