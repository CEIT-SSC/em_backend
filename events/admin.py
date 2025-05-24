from django.contrib import admin
from .models import (
    Presenter, Event, Presentation,
    SoloCompetition, GroupCompetition, CompetitionTeam, TeamMembership,
    PresentationEnrollment, SoloCompetitionRegistration
)

@admin.register(Presenter)
class PresenterAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'created_at')
    search_fields = ('name', 'email')
    list_filter = ('created_at',)

class PresentationInline(admin.TabularInline):
    model = Presentation
    extra = 1
    autocomplete_fields = ['presenters']

class SoloCompetitionInline(admin.TabularInline):
    model = SoloCompetition
    extra = 0

class GroupCompetitionInline(admin.TabularInline):
    model = GroupCompetition
    extra = 0

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'start_date', 'end_date', 'is_active', 'created_at')
    search_fields = ('title', 'description')
    list_filter = ('is_active', 'start_date', 'end_date')
    inlines = [PresentationInline, SoloCompetitionInline, GroupCompetitionInline]

@admin.register(Presentation)
class PresentationAdmin(admin.ModelAdmin):
    list_display = ('title', 'event', 'type', 'start_time', 'end_time', 'is_paid', 'price', 'capacity')
    search_fields = ('title', 'description', 'event__title')
    list_filter = ('type', 'is_online', 'is_paid', 'event')
    autocomplete_fields = ['presenters', 'event']

@admin.register(SoloCompetition)
class SoloCompetitionAdmin(admin.ModelAdmin):
    list_display = ('title', 'event', 'start_datetime', 'end_datetime', 'is_paid', 'price_per_participant', 'max_participants')
    search_fields = ('title', 'description', 'event__title')
    list_filter = ('is_paid', 'event', 'start_datetime')
    autocomplete_fields = ['event']

class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 1
    autocomplete_fields = ['user']

@admin.register(CompetitionTeam)
class CompetitionTeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'leader', 'group_competition', 'status', 'is_approved_by_admin', 'created_at')
    search_fields = ('name', 'leader__email', 'group_competition__title')
    list_filter = ('status', 'is_approved_by_admin', 'group_competition')
    autocomplete_fields = ['leader', 'group_competition']
    inlines = [TeamMembershipInline]
    actions = ['approve_teams', 'reject_teams']

    def approve_teams(self, request, queryset):
        updated_count = 0
        for team in queryset:
            if team.needs_admin_approval() and team.status == CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION:
                team.is_approved_by_admin = True
                if not team.group_competition.is_paid:
                    team.status = CompetitionTeam.STATUS_ACTIVE
                else:
                    team.status = CompetitionTeam.STATUS_APPROVED_AWAITING_PAYMENT
                team.save()
                updated_count += 1
        self.message_user(request, f"{updated_count} team(s) successfully approved.")
    approve_teams.short_description = "Approve selected teams"

    def reject_teams(self, request, queryset):
        updated_count = 0
        for team in queryset:
            if team.needs_admin_approval() and team.status == CompetitionTeam.STATUS_PENDING_ADMIN_VERIFICATION:
                team.is_approved_by_admin = False
                team.status = CompetitionTeam.STATUS_REJECTED_BY_ADMIN
                team.save()
                updated_count += 1
        self.message_user(request, f"{updated_count} team(s) successfully rejected.")
    reject_teams.short_description = "Reject selected teams"


@admin.register(GroupCompetition)
class GroupCompetitionAdmin(admin.ModelAdmin):
    list_display = ('title', 'event', 'start_datetime', 'is_paid', 'price_per_group', 'requires_admin_approval')
    search_fields = ('title', 'description', 'event__title')
    list_filter = ('is_paid', 'requires_admin_approval', 'event', 'start_datetime')
    autocomplete_fields = ['event']

@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'team', 'joined_at')
    search_fields = ('user__email', 'team__name')
    list_filter = ('team',)
    autocomplete_fields = ['user', 'team']

@admin.register(PresentationEnrollment)
class PresentationEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'presentation', 'status', 'enrolled_at')
    search_fields = ('user__email', 'presentation__title')
    list_filter = ('status', 'presentation')
    autocomplete_fields = ['user', 'presentation', 'order_item']

@admin.register(SoloCompetitionRegistration)
class SoloCompetitionRegistrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'solo_competition', 'status', 'registered_at')
    search_fields = ('user__email', 'solo_competition__title')
    list_filter = ('status', 'solo_competition')
    autocomplete_fields = ['user', 'solo_competition', 'order_item']

