from django.contrib import admin
from .models import Certificate, CompetitionCertificate


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


@admin.register(CompetitionCertificate)
class CompetitionCertificateAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'registration_type',
        'get_competition_title',
        'name_on_certificate',
        'ranking',
        'is_verified',
        'requested_at',
    )
    list_filter = ('registration_type', 'is_verified', 'requested_at')
    search_fields = (
        'name_on_certificate',
        'solo_registration__user__email',
        'solo_registration__solo_competition__title',
        'team__name',
        'team__group_competition__title',
    )
    actions = ['verify_certificates', 'unverify_certificates']

    @admin.display(description="Competition Title")
    def get_competition_title(self, obj):
        if obj.registration_type == "solo" and obj.solo_registration:
            return obj.solo_registration.solo_competition.title
        elif obj.registration_type == "group" and obj.team:
            return obj.team.group_competition.title
        return "-"

    @admin.action(description='Mark as verified')
    def verify_certificates(self, request, queryset):
        updated = queryset.update(is_verified=True)
        self.message_user(request, f"{updated} competition certificate(s) marked as verified.")

    @admin.action(description='Mark as not verified')
    def unverify_certificates(self, request, queryset):
        updated = queryset.update(is_verified=False)
        self.message_user(request, f"{updated} competition certificate(s) marked as not verified.")
