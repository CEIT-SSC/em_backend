from django.contrib import admin
from .models import CertificateRequest

@admin.register(CertificateRequest)
class CertificateRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'enrollment', 'is_approved', 'requested_at')
    list_filter = ('is_approved',)
    search_fields = ('enrollment__user__username', 'enrollment__presentation__title')
    ordering = ('-requested_at',)
    list_editable = ('is_approved',)
    list_per_page = 20
