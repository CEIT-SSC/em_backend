from django.contrib import admin
from .models import Certificate


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'enrollment',
        'name_on_certificate',
        'is_verified',
        'requested_at',
    )
    list_filter = ('is_verified', 'requested_at')
    search_fields = (
        'name_on_certificate',
        'enrollment__user__email',
        'enrollment__presentation__title',
    )
    actions = ['verify_certificates', 'unverify_certificates']

    @admin.action(description='Mark as verified')
    def verify_certificates(self, request, queryset):
        updated = queryset.update(is_verified=True)
        self.message_user(request, f"{updated} certificate(s) marked as verified.")

    @admin.action(description='Mark as not verified')
    def unverify_certificates(self, request, queryset):
        updated = queryset.update(is_verified=False)
        self.message_user(request, f"{updated} certificate(s) marked as not verified.")