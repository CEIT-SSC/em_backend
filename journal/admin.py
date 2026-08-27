from django.contrib import admin
from .models import CallForPaper, Submission, Release, AboutUsConfig

@admin.register(CallForPaper)
class CallForPaperAdmin(admin.ModelAdmin):
    list_display = ('title', 'start_date', 'end_date', 'is_active')
    list_filter = ('is_active',)

@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'call', 'university', 'submitted_at')
    list_filter = ('call', 'university')
    search_fields = ('full_name', 'email', 'phone_number')
    readonly_fields = ('submitted_at',)

@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_display = ('title', 'volume', 'publish_date')
    search_fields = ('title', 'volume')

@admin.register(AboutUsConfig)
class AboutUsConfigAdmin(admin.ModelAdmin):
    # Standard JSONField 
    pass