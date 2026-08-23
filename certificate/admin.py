from django.contrib import admin
from django.utils.html import format_html
from .models import Certificate, CompetitionCertificate
from .services.generator import generate_cert_for_object, generate_presentation_cert, generate_solo_cert, generate_group_cert


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'enrollment',
        'name_on_certificate',
        'status',
        'is_verified',
        'preview_links',
        'requested_at',
    )
    list_filter = ('status', 'is_verified', 'requested_at')
    search_fields = (
        'name_on_certificate',
        'enrollment__user__email',
        'enrollment__presentation__title',
        'verification_id',
    )
    readonly_fields = ('verification_id', 'status', 'generation_error', 'requested_at')
    actions = ['verify_and_generate_certificates', 'regenerate_certificates', 'unverify_certificates']

    @admin.display(description='Preview Links')
    def preview_links(self, obj):
        links = []
        if obj.file_fa:
            links.append(f'<a href="{obj.file_fa.url}" target="_blank">Persian SVG</a>')
        if obj.file_en:
            links.append(f'<a href="{obj.file_en.url}" target="_blank">English SVG</a>')
        if not links:
            return "-"
        return format_html(" | ".join(links))

    @admin.action(description='Verify and Generate selected certificates')
    def verify_and_generate_certificates(self, request, queryset):
        success_count = 0
        fail_count = 0
        for cert in queryset:
            try:
                generate_presentation_cert(cert, force_regenerate=True)
                cert.is_verified = True
                cert.save(update_fields=['is_verified'])
                success_count += 1
            except Exception:
                fail_count += 1

        msg = f"Verified & generated {success_count} certificate(s)."
        if fail_count > 0:
            msg += f" {fail_count} failed generation (check generation_error field)."
            self.message_user(request, msg, level='WARNING')
        else:
            self.message_user(request, msg)

    @admin.action(description='Regenerate selected certificates')
    def regenerate_certificates(self, request, queryset):
        success_count = 0
        fail_count = 0
        for cert in queryset:
            try:
                generate_presentation_cert(cert, force_regenerate=True)
                success_count += 1
            except Exception:
                fail_count += 1

        msg = f"Regenerated {success_count} certificate(s)."
        if fail_count > 0:
            msg += f" {fail_count} failed (check generation_error)."
            self.message_user(request, msg, level='WARNING')
        else:
            self.message_user(request, msg)

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
        'status',
        'is_verified',
        'preview_links',
        'requested_at',
    )
    list_filter = ('registration_type', 'status', 'is_verified', 'requested_at')
    search_fields = (
        'name_on_certificate',
        'solo_registration__user__email',
        'solo_registration__solo_competition__title',
        'team__name',
        'team__group_competition__title',
        'verification_id',
    )
    readonly_fields = ('verification_id', 'status', 'generation_error', 'requested_at')
    actions = ['verify_and_generate_certificates', 'regenerate_certificates', 'unverify_certificates']

    @admin.display(description="Competition Title")
    def get_competition_title(self, obj):
        if obj.registration_type == "solo" and obj.solo_registration:
            return obj.solo_registration.solo_competition.title
        elif obj.registration_type == "group" and obj.team:
            return obj.team.group_competition.title
        return "-"

    @admin.display(description='Preview Links')
    def preview_links(self, obj):
        links = []
        if obj.file_fa:
            links.append(f'<a href="{obj.file_fa.url}" target="_blank">Persian SVG</a>')
        if obj.file_en:
            links.append(f'<a href="{obj.file_en.url}" target="_blank">English SVG</a>')
        if not links:
            return "-"
        return format_html(" | ".join(links))

    @admin.action(description='Verify and Generate selected certificates')
    def verify_and_generate_certificates(self, request, queryset):
        success_count = 0
        fail_count = 0
        for cert in queryset:
            try:
                generate_cert_for_object(cert, force_regenerate=True)
                cert.is_verified = True
                cert.save(update_fields=['is_verified'])
                success_count += 1
            except Exception:
                fail_count += 1

        msg = f"Verified & generated {success_count} competition certificate(s)."
        if fail_count > 0:
            msg += f" {fail_count} failed generation (check generation_error field)."
            self.message_user(request, msg, level='WARNING')
        else:
            self.message_user(request, msg)

    @admin.action(description='Regenerate selected certificates')
    def regenerate_certificates(self, request, queryset):
        success_count = 0
        fail_count = 0
        for cert in queryset:
            try:
                generate_cert_for_object(cert, force_regenerate=True)
                success_count += 1
            except Exception:
                fail_count += 1

        msg = f"Regenerated {success_count} competition certificate(s)."
        if fail_count > 0:
            msg += f" {fail_count} failed (check generation_error)."
            self.message_user(request, msg, level='WARNING')
        else:
            self.message_user(request, msg)

    @admin.action(description='Mark as not verified')
    def unverify_certificates(self, request, queryset):
        updated = queryset.update(is_verified=False)
        self.message_user(request, f"{updated} competition certificate(s) marked as not verified.")
